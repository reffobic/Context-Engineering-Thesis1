# pruning_tool.py
# Person 4 — Structure-Aware Context Pruning
#
# Input: retrieval results from Person 3
# Output: injection-compatible ContextChunk list for PromptBuilder

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


# ── Config ─────────────────────────────────────────────────────

DEFAULT_TOKEN_BUDGET = 1800

W_RETRIEVAL   = 0.40
W_QUERY_MATCH = 0.25
W_FUNCTION    = 0.20
W_STRUCTURE   = 0.15


# ── Types ──────────────────────────────────────────────────────

@dataclass
class PruningStats:
    original_chunks: int
    kept_chunks: int
    dropped_chunks: int
    original_est_tokens: int
    kept_est_tokens: int


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
    """
    Prefer software-meaningful chunks over noise.
    """
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
    """
    Convert import/include lines into free-text dependency notes for PromptBuilder.
    PromptBuilder expects `dependencies` as a string, not a list. :contentReference[oaicite:1]{index=1}
    """
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
    """
    Conservative cleanup:
    - remove blank lines
    - remove decorative separators
    - keep real comments/docstrings for now
    """
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
    """
    Keep a focused code window around query-relevant lines.
    Returns (pruned_text, start_line)
    """
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

    # If nothing matched, keep leading section
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

def score_candidate(query: str, item: dict) -> float:
    code = item.get("code", "") or ""
    meta = item.get("metadata", {}) or {}
    fn_name = meta.get("function", "") or ""
    path = meta.get("file", "") or ""

    retrieval_score = float(item.get("final_score", item.get("hybrid_score", item.get("similarity", 0.0))))
    q_score = query_match_score(query, code)
    fn_score = function_overlap_score(query, fn_name)
    s_score = structure_score(code, path)

    final = (
        W_RETRIEVAL   * retrieval_score +
        W_QUERY_MATCH * q_score +
        W_FUNCTION    * fn_score +
        W_STRUCTURE   * s_score
    )
    return round(final, 4)


# ── Core pruning ───────────────────────────────────────────────

def prune_single_item(query: str, item: dict) -> Optional[ContextChunk]:
    code = item.get("code", "") or ""
    meta = item.get("metadata", {}) or {}
    path = meta.get("file", "") or "(unknown path)"
    fn_name = meta.get("function", "") or ""

    if not code.strip():
        return None

    # Step 1: light cleanup
    compact = remove_noise_lines(code)
    if not compact:
        return None

    # Step 2: focus on relevant window
    pruned_text, start_line = find_best_window(compact, query, radius=3, max_lines=40)
    if not pruned_text.strip():
        return None

    # Step 3: convert to PromptBuilder-compatible chunk
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


def prune_context(
    query: str,
    retrieved_items: list[dict],
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> tuple[list[ContextChunk], PruningStats]:
    """
    Main API for Person 4.

    Input: retrieval output from Person 3
    Output:
      - pruned_chunks: directly consumable by PromptBuilder.build(...)
      - stats: evaluation/debug info
    """
    original_est_tokens = sum(estimate_tokens((x.get("code", "") or "")) for x in retrieved_items)

    # Score all retrieval candidates
    scored_items = []
    for item in retrieved_items:
        item_copy = dict(item)
        item_copy["pruning_score"] = score_candidate(query, item_copy)
        scored_items.append(item_copy)

    scored_items.sort(key=lambda x: x["pruning_score"], reverse=True)

    kept_chunks: list[ContextChunk] = []
    kept_est_tokens = 0

    for item in scored_items:
        chunk = prune_single_item(query, item)
        if chunk is None:
            continue

        t = estimate_tokens(chunk["text"])
        if kept_est_tokens + t > token_budget:
            continue

        kept_chunks.append(chunk)
        kept_est_tokens += t

    stats = PruningStats(
        original_chunks=len(retrieved_items),
        kept_chunks=len(kept_chunks),
        dropped_chunks=max(0, len(retrieved_items) - len(kept_chunks)),
        original_est_tokens=original_est_tokens,
        kept_est_tokens=kept_est_tokens,
    )

    return kept_chunks, stats
