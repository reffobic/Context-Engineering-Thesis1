# pruning_tool.py
# Person 4 — Structure-Aware Context Pruning
#
# Two pruning methods:
#   "selective" — Selective Context (Li et al., 2023): token-level
#                 self-information via a causal LM.  Primary method.
#   "heuristic" — Weighted scoring + keyword windowing.  Fallback when
#                 torch/transformers are unavailable or for speed.
#
# Input:  retrieval results from Person 3
# Output: injection-compatible ContextChunk list for PromptBuilder

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
import os

# ── Config ─────────────────────────────────────────────────────

DEFAULT_TOKEN_BUDGET = 1800


@dataclass
class HeuristicWeights:
    """Configurable weights for the heuristic scoring fallback.

    All four weights should sum to 1.0 for interpretability, but this
    is not enforced — the final score is just a weighted sum.
    """
    retrieval: float = float(os.getenv("PRUNE_W_RETRIEVAL", "0.7741918328343507"))
    query_match: float = float(os.getenv("PRUNE_W_QUERY_MATCH", "0.2887302647124196"))
    function: float = float(os.getenv("PRUNE_W_FUNCTION", "0.824755791189501"))
    structure: float = float(os.getenv("PRUNE_W_STRUCTURE", "0.42360256074513347"))

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
    method: str = "heuristic"
    compression_ratio: float = 1.0


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
    code = item.get("code") or item.get("text") or ""
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
    code = item.get("code") or item.get("text") or ""
    meta = item.get("metadata", {}) or {}
    path = meta.get("file", "") or "(unknown path)"
    fn_name = meta.get("function", "") or ""

    if not code.strip():
        return None

    compact = remove_noise_lines(code)
    if not compact:
        return None

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
        chunk["start_line"] = start_line

    return chunk


# Keep old name as alias for backwards compat
prune_single_item = prune_single_item_heuristic


# ── Selective Context pruning (single item) ────────────────────

def prune_single_item_selective(
    query: str,
    item: dict,
    target_ratio: float = 0.5,
    model_name: str = "Salesforce/codegen-350M-multi",
) -> Optional[ContextChunk]:
    """
    Selective Context: prune tokens with low self-information.

    Falls back to heuristic if torch/transformers are not available.
    """
    code = item.get("code") or item.get("text") or ""
    meta = item.get("metadata", {}) or {}
    path = meta.get("file", "") or "(unknown path)"
    fn_name = meta.get("function", "") or ""

    if not code.strip():
        return None

    # Light cleanup first (remove decorative noise)
    compact = remove_noise_lines(code)
    if not compact:
        return None

    try:
        from .selective_context import SelectiveContextPruner

        pruner = SelectiveContextPruner.get_instance(model_name)
        result = pruner.prune_text(compact, target_ratio=target_ratio)
        pruned_text = result.pruned_text
    except (ImportError, Exception):
        # Fallback to heuristic windowing if model unavailable
        return prune_single_item_heuristic(query, item)

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

    return chunk


# ── Main API ───────────────────────────────────────────────────

def prune_context(
    query: str,
    retrieved_items: list[dict],
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    method: str = "selective",
    target_ratio: float = 0.5,
    weights: Optional[HeuristicWeights] = None,
    model_name: str = "Salesforce/codegen-350M-multi",
) -> tuple[list[ContextChunk], PruningStats]:
    """
    Main API for Person 4.

    Args:
        query: the user's natural-language question / task.
        retrieved_items: output from Person 3's retrieve_context().
        token_budget: max estimated tokens to keep.
        method: "selective" (Selective Context, primary) or
                "heuristic" (weighted scoring fallback).
        target_ratio: fraction of tokens to keep per chunk (selective only).
        weights: override heuristic signal weights (heuristic only).
        model_name: HuggingFace causal LM for self-information (selective only).

    Returns:
        (pruned_chunks, stats) — chunks are directly consumable by
        PromptBuilder.build(...).
    """
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

    # If selective, add informativeness bonus to ranking
    if method == "selective":
        try:
            from .selective_context import SelectiveContextPruner

            pruner = SelectiveContextPruner.get_instance(model_name)
            for item in scored_items:
                code = item.get("code", "") or ""
                if code.strip():
                    info_score = pruner.chunk_informativeness(code)
                    # Normalize to [0, 1] range (self-info in nats, typical range 0-15)
                    normalized = min(info_score / 10.0, 1.0)
                    # Blend: 60% heuristic rank + 40% informativeness
                    item["pruning_score"] = round(
                        0.6 * item["pruning_score"] + 0.4 * normalized, 4
                    )
        except (ImportError, Exception):
            pass  # Fall through — heuristic scores remain

    scored_items.sort(key=lambda x: x["pruning_score"], reverse=True)

    # ── Step 2: Prune content of each chunk ────────────────────
    kept_chunks: list[ContextChunk] = []
    kept_est_tokens = 0
    total_compression = 0.0
    compression_count = 0

    for item in scored_items:
        if method == "selective":
            chunk = prune_single_item_selective(
                query, item, target_ratio=target_ratio, model_name=model_name
            )
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
        method=method,
        compression_ratio=round(avg_compression, 4),
    )

    return kept_chunks, stats


def _item_code(item: dict) -> str:
    # Graph expand / offline stubs sometimes put body in ``text`` only;
    # retrieval always uses ``code``. Normalize so scoring never sees "".
    return (item.get("code") or item.get("text") or "") or ""


def _pack_scored(
    query: str,
    scored_items: list[dict],
    token_budget: int,
    kept_chunks: list[ContextChunk],
    kept_est_tokens: int,
    *,
    method: str,
    target_ratio: float,
    model_name: str,
) -> tuple[list[ContextChunk], int, float, int]:
    """Greedy pack pruned windows into remaining budget. Returns compression accumulators."""
    total_compression = 0.0
    compression_count = 0
    for item in scored_items:
        if method == "selective":
            chunk = prune_single_item_selective(
                query, item, target_ratio=target_ratio, model_name=model_name
            )
        else:
            chunk = prune_single_item_heuristic(query, item)
        if chunk is None:
            continue
        t = estimate_tokens(chunk["text"])
        if kept_est_tokens + t > token_budget:
            continue
        kept_chunks.append(chunk)
        kept_est_tokens += t
        code = _item_code(item)
        if code:
            total_compression += len(chunk["text"]) / max(len(code), 1)
            compression_count += 1
    return kept_chunks, kept_est_tokens, total_compression, compression_count
