# pruning_tool.py
# Person 4 — Structure-Aware Context Pruning
#
# Two pruning methods:
#   "heuristic" — Weighted scoring + keyword windowing.  Default; also the
#                 fallback when torch/transformers are unavailable.
#   "selective" — Selective Context (Li et al., 2023): token-level
#                 self-information via a causal LM.  Opt-in (method="selective").
#
# Input:  retrieval results from Person 3
# Output: injection-compatible ContextChunk list for PromptBuilder

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────

DEFAULT_TOKEN_BUDGET = 1800


@dataclass
class HeuristicWeights:
    """Configurable weights for the heuristic scoring fallback.

    All four weights should sum to 1.0 for interpretability, but this
    is not enforced — the final score is just a weighted sum.
    """

    retrieval: float = 0.40
    query_match: float = 0.25
    function: float = 0.20
    structure: float = 0.15

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.retrieval, self.query_match, self.function, self.structure)


DEFAULT_WEIGHTS = HeuristicWeights()


# ── Types ──────────────────────────────────────────────────────

@dataclass
class PruningStats:
    original_chunks: int
    kept_chunks: int
    dropped_chunks: int
    original_est_tokens: int
    kept_est_tokens: int
    # Method actually applied. Can differ from requested_method when
    # "selective" was requested but the model could not be used.
    method: str = "heuristic"
    compression_ratio: float = 1.0
    requested_method: str = "heuristic"
    # "code" or "paper" when method == "selective", else None.
    selective_mode: Optional[str] = None
    # Chunks pruned with the heuristic because selective failed on them.
    fallback_chunks: int = 0
    fallback_reason: Optional[str] = None


# Injection-compatible output contract
# Mirrors PromptBuilder ContextChunk contract
ContextChunk = dict[str, Any]


# ── Utilities ──────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def infer_language_from_path(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".jsx": "jsx",
        ".java": "java",
        ".c": "c",
        ".cpp": "cpp",
        ".h": "c",
        ".hpp": "cpp",
        ".cs": "csharp",
        ".go": "go",
        ".rs": "rust",
        ".rb": "ruby",
        ".php": "php",
        ".kt": "kotlin",
        ".sql": "sql",
        ".md": "markdown",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
    }.get(suffix, "text")


def normalize_words(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text.lower().replace("-", " ")))


def query_match_score(query: str, text: str) -> float:
    q_words = normalize_words(query)
    if not q_words:
        return 0.0
    text_lower = text.lower()
    hits = sum(1 for w in q_words if w in text_lower)
    return min(hits / len(q_words), 1.0)


def function_overlap_score(query: str, fn_name: str) -> float:
    q_words = normalize_words(query)
    fn_words = normalize_words(fn_name.replace("_", " "))
    if not fn_words:
        return 0.0
    overlap = q_words & fn_words
    return min(len(overlap) / len(fn_words), 1.0)


def structure_score(code: str, path: str) -> float:
    """Prefer software-meaningful chunks over noise."""
    score = 0.0
    lower = code.lower()
    path_lower = path.lower()

    signature_patterns = [
        r"^\s*def\s+\w+\(",
        r"^\s*class\s+\w+",
        r"^\s*function\s+\w+\(",
        r"^\s*(public|private|protected)?\s*(static\s+)?\w[\w<>\[\]]*\s+\w+\(",
        r"^\s*interface\s+\w+",
    ]
    if any(re.search(p, code, re.MULTILINE) for p in signature_patterns):
        score += 0.5

    if re.search(r"^\s*(import|from\s+\S+\s+import)\s+", code, re.MULTILINE):
        score += 0.15

    if any(k in lower for k in ["return ", "if ", "for ", "while ", "try:", "except", "throw ", "raise "]):
        score += 0.2

    if any(k in path_lower for k in ["test", "spec"]):
        score -= 0.1

    if any(k in lower for k in ["auto-generated", "generated automatically", "do not edit"]):
        score -= 0.25

    return max(0.0, min(score, 1.0))


def detect_dependencies(code: str) -> str:
    """Convert import/include lines into free-text dependency notes for PromptBuilder."""
    deps: list[str] = []

    py_matches = re.findall(r"^\s*import\s+([A-Za-z0-9_., ]+)", code, re.MULTILINE)
    for match in py_matches:
        deps.extend([x.strip() for x in match.split(",") if x.strip()])

    from_matches = re.findall(r"^\s*from\s+([A-Za-z0-9_\.]+)\s+import\s+([A-Za-z0-9_.*, ]+)", code, re.MULTILINE)
    for module, symbols in from_matches:
        deps.append(module)
        deps.extend([f"{module}.{x.strip()}" for x in symbols.split(",") if x.strip() and x.strip() != "*"])

    c_matches = re.findall(r'^\s*#include\s+[<"]([^>"]+)[>"]', code, re.MULTILINE)
    deps.extend(c_matches)

    seen = []
    for d in deps:
        if d not in seen:
            seen.append(d)

    return ", ".join(seen[:12])


def remove_noise_lines(code: str) -> str:
    """Remove blank lines and decorative separators."""
    cleaned: list[str] = []
    for line in code.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.fullmatch(r"[#/\-*_=~]{4,}", stripped):
            continue
        cleaned.append(line.rstrip())
    return "\n".join(cleaned).strip()


def find_best_window(code: str, query: str, radius: int = 3, max_lines: int = 40) -> tuple[str, Optional[int]]:
    """Keep a focused code window around query-relevant lines."""
    lines = code.splitlines()
    if not lines:
        return "", None

    q_words = normalize_words(query)
    hit_indices: list[int] = []

    for i, line in enumerate(lines):
        ll = line.lower()
        score = 0
        for w in q_words:
            if w in ll:
                score += 1
        if re.search(r"\b(def|class|function|return|if|for|while|import|from)\b", ll):
            score += 1
        if score > 0:
            hit_indices.append(i)

    if not hit_indices:
        kept = lines[:max_lines]
        return "\n".join(kept).strip(), 1 if kept else None

    spans: list[tuple[int, int]] = []
    for idx in hit_indices:
        start = max(0, idx - radius)
        end = min(len(lines) - 1, idx + radius)
        spans.append((start, end))

    spans.sort()
    merged: list[tuple[int, int]] = []
    for s, e in spans:
        if not merged:
            merged.append((s, e))
        else:
            ps, pe = merged[-1]
            if s <= pe + 2:
                merged[-1] = (ps, max(pe, e))
            else:
                merged.append((s, e))

    kept_lines: list[str] = []
    first_line_no: Optional[int] = None

    for s, e in merged:
        for i in range(s, e + 1):
            kept_lines.append(lines[i])
            if first_line_no is None:
                first_line_no = i + 1
            if len(kept_lines) >= max_lines:
                return "\n".join(kept_lines).strip(), first_line_no

    return "\n".join(kept_lines).strip(), first_line_no


# ── Scoring ────────────────────────────────────────────────────

def score_candidate(
    query: str,
    item: dict,
    weights: HeuristicWeights = DEFAULT_WEIGHTS,
) -> float:
    code = item.get("code", "") or ""
    meta = item.get("metadata", {}) or {}
    fn_name = meta.get("function", "") or ""
    path = meta.get("file", "") or ""

    retrieval_score = float(item.get("final_score", item.get("hybrid_score", item.get("similarity", 0.0))))
    q_score = query_match_score(query, code)
    fn_score = function_overlap_score(query, fn_name)
    s_score = structure_score(code, path)

    w = weights
    final = (
        w.retrieval   * retrieval_score +
        w.query_match * q_score +
        w.function    * fn_score +
        w.structure   * s_score
    )
    return round(final, 4)


# ── Heuristic pruning (single item) ───────────────────────────

def prune_single_item_heuristic(query: str, item: dict) -> Optional[ContextChunk]:
    """Heuristic fallback: noise removal + keyword windowing."""
    code = item.get("code", "") or ""
    meta = item.get("metadata", {}) or {}
    path = meta.get("file", "") or "(unknown path)"
    fn_name = meta.get("function", "") or ""

    if not code.strip():
        return None

    compact = remove_noise_lines(code)
    if not compact:
        return None

    # NOTE: find_best_window() returns a line number relative to `compact`.
    # We remap it to the original file's absolute line using retrieval metadata.
    pruned_text, start_line = find_best_window(compact, query, radius=3, max_lines=40)
    if not pruned_text.strip():
        return None

    chunk: ContextChunk = {
        "text": pruned_text,
        "path": path,
        "symbol": fn_name,
        "language": infer_language_from_path(path),
    }

    dep_note = detect_dependencies(pruned_text)
    if dep_note:
        chunk["dependencies"] = dep_note

    if start_line is not None:
        meta_start = meta.get("start_line", 0)
        try:
            base_start = int(meta_start)
        except (TypeError, ValueError):
            base_start = 0

        # If retrieval metadata has a valid file start line, convert from
        # snippet-local numbering to absolute file numbering.
        if base_start > 0:
            chunk["start_line"] = base_start + start_line - 1
        else:
            chunk["start_line"] = start_line

    return chunk


# Keep old name as alias for backwards compat
prune_single_item = prune_single_item_heuristic


# ── Selective Context pruning (single item) ────────────────────

def _load_selective_pruner(model_name: str) -> tuple[Optional[Any], Optional[str]]:
    """Load the Selective Context model; on failure return (None, reason) and log it."""
    try:
        from .selective_context import SelectiveContextPruner

        return SelectiveContextPruner.get_instance(model_name), None
    except ImportError as e:
        logger.warning("Selective Context unavailable, using heuristic pruning: %s", e)
        return None, f"torch/transformers not installed: {e}"
    except Exception as e:
        logger.exception(
            "Failed to load Selective Context model %r, using heuristic pruning", model_name
        )
        return None, f"failed to load model {model_name!r}: {e}"


SELECTIVE_MODES = ("code", "paper")
# Fraction of tokens kept per chunk when target_ratio isn't given. Code mode
# protects structure and needs more room to keep meaning (0.5 dropped
# repeated subjects, e.g. "if pruner is None" -> "if is None"); paper mode
# keeps the paper's 0.5.
DEFAULT_TARGET_RATIO = {"code": 0.7, "paper": 0.5}


def _selective_chunk(
    item: dict,
    pruner: Any,
    target_ratio: float,
    query: str = "",
    selective_mode: str = "code",
    code_weights: Optional[Any] = None,
) -> Optional[ContextChunk]:
    """Selective Context pruning of one item. Model errors propagate to the caller.

    "code": comment filter + weighted lexical-unit pruning (prune_code).
    "paper": raw token-level pruning as in Li et al. (prune_text).
    """
    code = item.get("code", "") or ""
    meta = item.get("metadata", {}) or {}
    path = meta.get("file", "") or "(unknown path)"
    fn_name = meta.get("function", "") or ""
    language = infer_language_from_path(path)

    if not code.strip():
        return None

    # Light cleanup first (remove decorative noise)
    compact = remove_noise_lines(code)
    if not compact:
        return None

    if selective_mode == "code":
        result = pruner.prune_code(
            compact, target_ratio=target_ratio, language=language, query=query, weights=code_weights
        )
    else:
        result = pruner.prune_text(compact, target_ratio=target_ratio)
    pruned_text = result.pruned_text
    if not pruned_text.strip():
        return None

    chunk: ContextChunk = {
        "text": pruned_text,
        "path": path,
        "symbol": fn_name,
        "language": language,
    }

    dep_note = detect_dependencies(pruned_text)
    if dep_note:
        chunk["dependencies"] = dep_note

    return chunk


def prune_single_item_selective(
    query: str,
    item: dict,
    target_ratio: Optional[float] = None,
    model_name: str = "Salesforce/codegen-350M-multi",
    selective_mode: str = "code",
) -> Optional[ContextChunk]:
    """
    Selective Context: prune tokens with low self-information.

    Falls back to heuristic (with a logged warning) if the model is unavailable
    or fails on this item.
    """
    pruner, _reason = _load_selective_pruner(model_name)
    if pruner is None:
        return prune_single_item_heuristic(query, item)
    try:
        ratio = target_ratio if target_ratio is not None else DEFAULT_TARGET_RATIO[selective_mode]
        return _selective_chunk(item, pruner, ratio, query, selective_mode)
    except Exception:
        logger.exception("Selective Context failed on chunk, using heuristic pruning")
        return prune_single_item_heuristic(query, item)


# ── Main API ───────────────────────────────────────────────────

def prune_context(
    query: str,
    retrieved_items: list[dict],
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    method: str = "heuristic",
    target_ratio: Optional[float] = None,
    weights: Optional[HeuristicWeights] = None,
    model_name: str = "Salesforce/codegen-350M-multi",
    selective_mode: str = "code",
    code_weights: Optional[Any] = None,
) -> tuple[list[ContextChunk], PruningStats]:
    """
    Main API for Person 4.

    Args:
        query: the user's natural-language question / task.
        retrieved_items: output from Person 3's retrieve_context().
        token_budget: max estimated tokens to keep.
        method: "heuristic" (weighted scoring, default) or
                "selective" (Selective Context; opt-in, needs torch + transformers).
        target_ratio: fraction of tokens to keep per chunk (selective only).
                Defaults to 0.7 in "code" mode and 0.5 in "paper" mode.
        weights: override heuristic signal weights (heuristic only).
        model_name: HuggingFace causal LM for self-information (selective only).
        selective_mode: "code" (default; comment filter + weighted lexical units)
                or "paper" (raw token-level pruning, Li et al. baseline).
        code_weights: CodeUnitWeights override for "code" mode.

    Returns:
        (pruned_chunks, stats) — chunks are directly consumable by
        PromptBuilder.build(...).
    """
    if selective_mode not in SELECTIVE_MODES:
        raise ValueError(f"selective_mode must be one of {SELECTIVE_MODES}, got {selective_mode!r}")
    if target_ratio is None:
        target_ratio = DEFAULT_TARGET_RATIO[selective_mode]
    w = weights or DEFAULT_WEIGHTS
    original_est_tokens = sum(estimate_tokens((x.get("code", "") or "")) for x in retrieved_items)

    # ── Step 1: Score and rank all candidates ──────────────────
    # Scoring uses the heuristic signals for RANKING regardless of method.
    # When method="selective", we can also incorporate chunk informativeness
    # from the causal LM as an additional ranking signal.

    scored_items = []
    for item in retrieved_items:
        item_copy = dict(item)
        item_copy["pruning_score"] = score_candidate(query, item_copy, weights=w)
        scored_items.append(item_copy)

    # If selective, load the model once. If that fails, the whole call is
    # heuristic and the stats say so.
    pruner = None
    fallback_reason: Optional[str] = None
    if method == "selective":
        pruner, fallback_reason = _load_selective_pruner(model_name)
    method_used = "selective" if pruner is not None else "heuristic"

    # If selective, add informativeness bonus to ranking
    if pruner is not None:
        try:
            for item in scored_items:
                code = item.get("code", "") or ""
                if code.strip():
                    # Score the same text the pruning step will, so the model's
                    # forward pass is cached and shared between ranking and pruning.
                    text = remove_noise_lines(code)
                    if selective_mode == "code":
                        path = (item.get("metadata") or {}).get("file", "") or ""
                        text = pruner.prepare_code(text, infer_language_from_path(path), query)
                    info_score = pruner.chunk_informativeness(text)
                    # Normalize to [0, 1] range (self-info in nats, typical range 0-15)
                    normalized = min(info_score / 10.0, 1.0)
                    # Blend: 60% heuristic rank + 40% informativeness
                    item["pruning_score"] = round(
                        0.6 * item["pruning_score"] + 0.4 * normalized, 4
                    )
        except Exception as e:
            logger.exception("Selective Context ranking failed, using heuristic ranking")
            fallback_reason = f"informativeness ranking failed: {e}"
            for item in scored_items:
                item["pruning_score"] = score_candidate(query, item, weights=w)

    scored_items.sort(key=lambda x: x["pruning_score"], reverse=True)

    # ── Step 2: Prune content of each chunk ────────────────────
    kept_chunks: list[ContextChunk] = []
    kept_est_tokens = 0
    total_compression = 0.0
    compression_count = 0
    fallback_chunks = 0

    for item in scored_items:
        if pruner is not None:
            try:
                chunk = _selective_chunk(
                    item, pruner, target_ratio, query, selective_mode, code_weights
                )
            except Exception as e:
                logger.exception("Selective Context failed on chunk, using heuristic pruning")
                fallback_chunks += 1
                fallback_reason = f"selective pruning failed on {fallback_chunks} chunk(s): {e}"
                chunk = prune_single_item_heuristic(query, item)
        else:
            chunk = prune_single_item_heuristic(query, item)

        if chunk is None:
            continue

        t = estimate_tokens(chunk["text"])
        if kept_est_tokens + t > token_budget:
            continue

        kept_chunks.append(chunk)
        kept_est_tokens += t

        code = item.get("code", "") or ""
        if code:
            total_compression += len(chunk["text"]) / len(code)
            compression_count += 1

    avg_compression = (
        total_compression / compression_count if compression_count > 0 else 1.0
    )

    stats = PruningStats(
        original_chunks=len(retrieved_items),
        kept_chunks=len(kept_chunks),
        dropped_chunks=max(0, len(retrieved_items) - len(kept_chunks)),
        original_est_tokens=original_est_tokens,
        kept_est_tokens=kept_est_tokens,
        method=method_used,
        compression_ratio=round(avg_compression, 4),
        requested_method=method,
        selective_mode=selective_mode if method_used == "selective" else None,
        fallback_chunks=fallback_chunks,
        fallback_reason=fallback_reason,
    )

    return kept_chunks, stats
