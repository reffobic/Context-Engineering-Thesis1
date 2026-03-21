"""
Prompt builder & structured context injection.

Consumes pruned/retrieved code chunks (from P3/P4) and builds prompts for the LLM.
Does not implement retrieval or pruning.

Features:
  - TypedDict contract for chunks (IDE-friendly)
  - Group chunks by file path (context cohesion for the LLM)
  - Optional line numbers when ``start_line`` is set on a chunk
  - Optional token budget with pluggable ``estimate_tokens`` (default: ~chars/4)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Optional

try:
    from typing import TypedDict  # Python 3.8+
except ImportError:  # pragma: no cover
    from typing_extensions import TypedDict  # type: ignore

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class ContextChunk(TypedDict, total=False):
    """Contract for chunks from upstream retrieval/pruning."""

    text: str  # required in practice for non-empty snippets
    path: str  # file path
    symbol: str  # function/class name
    language: str  # e.g. python, c
    dependencies: str  # free-text dependency note
    start_line: int  # 1-based start line for this snippet (enables numbered display)


DEFAULT_SYSTEM_PROMPT = """You are an expert software engineer assisting with code changes.
Rules:
- Use only the information in the CONTEXT sections when discussing the codebase.
- If context is insufficient, state what is missing instead of inventing files or APIs.
- Prefer concise, correct code and explain non-obvious choices briefly."""

CONTEXT_TRUNCATED_TAG = "<CONTEXT_TRUNCATED>"
CONTEXT_TRUNCATED_MSG = (
    "Some context was omitted to stay within the configured token budget "
    "(least-recent files / trailing snippets dropped first)."
)


def default_token_estimate(text: str) -> int:
    """Rough token count without external libs (~4 chars per token)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# PromptBuilder
# ---------------------------------------------------------------------------


@dataclass
class PromptBuilder:
    """
    Assembles system + user messages with labeled sections.
    P1 can pass this output to OllamaAgent; P6 can log token counts on the built strings.
    """

    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    max_context_tokens: Optional[int] = None
    estimate_tokens: Callable[[str], int] = field(default=default_token_estimate)
    add_line_numbers_when_no_start_line: bool = False
    """If True, number lines starting at 1 even when ``start_line`` is omitted."""

    @staticmethod
    def _add_line_numbers(text: str, start_line: int = 1) -> str:
        """Inject ``  N | `` prefixes for precise edit references."""
        lines = text.split("\n")
        return "\n".join(f"{start_line + i:4d} | {line}" for i, line in enumerate(lines))

    def _format_snippet(self, c: ContextChunk) -> str:
        code = (c.get("text") or "").strip()
        if not code:
            return ""
        if "start_line" in c and c["start_line"] is not None:
            code = self._add_line_numbers(code, int(c["start_line"]))
        elif self.add_line_numbers_when_no_start_line:
            code = self._add_line_numbers(code, 1)
        sym = f" (Symbol: {c['symbol']})" if c.get("symbol") else ""
        lang = f" [lang={c['language']}]" if c.get("language") else ""
        return f"--- Snippet{sym}{lang} ---\n{code}"

    def _file_block(self, path: str, file_chunks: list[ContextChunk]) -> str:
        header = f"=== File: {path} ==="
        parts = [self._format_snippet(c) for c in file_chunks]
        parts = [p for p in parts if p]
        body = "\n\n".join(parts) if parts else "(empty)"
        return f"{header}\n{body}"

    def _group_chunks_by_path(self, chunks: list[ContextChunk]) -> dict[str, list[ContextChunk]]:
        files: dict[str, list[ContextChunk]] = defaultdict(list)
        for chunk in chunks:
            path = chunk.get("path") or "(unknown path)"
            files[path].append(chunk)
        return files

    def format_context_body(
        self,
        chunks: list[ContextChunk],
        *,
        max_context_tokens: Optional[int] = None,
    ) -> str:
        """
        Group chunks by file, render cohesive file blocks, optional token truncation.
        """
        if not chunks:
            return "(no context chunks — retrieval/pruning may not have run yet)"

        budget = max_context_tokens if max_context_tokens is not None else self.max_context_tokens
        grouped = self._group_chunks_by_path(chunks)
        # Stable order: sorted paths so output is reproducible
        paths_sorted = sorted(grouped.keys(), key=lambda p: (p == "(unknown path)", p))

        file_blocks: list[str] = []
        for path in paths_sorted:
            file_blocks.append(self._file_block(path, grouped[path]))

        body = "\n\n\n".join(file_blocks)
        if budget is None:
            return body

        return self._truncate_file_blocks(file_blocks, budget)

    def _truncate_file_blocks(self, file_blocks: list[str], max_tokens: int) -> str:
        """Keep leading file blocks until token budget; append truncation notice if needed."""
        if not file_blocks:
            return "(no context chunks — retrieval/pruning may not have run yet)"

        kept: list[str] = []
        total = 0
        est = self.estimate_tokens

        for block in file_blocks:
            t = est(block)
            if not kept:
                if t <= max_tokens:
                    kept.append(block)
                    total = t
                else:
                    # Single file exceeds budget — hard-truncate content inside block
                    kept.append(self._truncate_single_block(block, max_tokens))
                    total = est(kept[0])
                continue
            if total + t <= max_tokens:
                kept.append(block)
                total += t
            else:
                break

        out = "\n\n\n".join(kept)
        first_was_oversized = bool(file_blocks) and est(file_blocks[0]) > max_tokens
        dropped_files = len(kept) < len(file_blocks)
        if dropped_files or (len(kept) >= 1 and first_was_oversized):
            notice = (
                f"{CONTEXT_TRUNCATED_TAG}\n{CONTEXT_TRUNCATED_MSG}\n"
                "</CONTEXT_TRUNCATED>\n\n"
            )
            return notice + out
        return out

    def _truncate_single_block(self, block: str, max_tokens: int) -> str:
        """Remove lines from the bottom until estimated tokens <= max_tokens."""
        est = self.estimate_tokens
        if est(block) <= max_tokens:
            return block
        lines = block.split("\n")
        lo, hi = 0, len(lines)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            candidate = "\n".join(lines[:mid])
            if est(candidate) <= max_tokens:
                lo = mid
            else:
                hi = mid - 1
        trimmed = "\n".join(lines[:lo]) if lo > 0 else lines[0][: max_tokens * 4] if lines else ""
        return trimmed + "\n\n[... truncated ...]"

    def build_user_message(
        self,
        user_task: str,
        chunks: list[ContextChunk],
        *,
        extra_dependencies: Optional[str] = None,
        max_context_tokens: Optional[int] = None,
    ) -> str:
        """Structured user message with XML-style tags."""
        ctx = self.format_context_body(chunks, max_context_tokens=max_context_tokens)

        dep_notes = [str(c["dependencies"]) for c in chunks if c.get("dependencies")]
        if extra_dependencies:
            dep_notes.append(extra_dependencies.strip())

        dep_block = ""
        if dep_notes:
            dep_text = "\n".join(dep_notes)
            dep_block = f"<DEPENDENCIES>\n{dep_text}\n</DEPENDENCIES>\n"

        return f"""<CONTEXT>
<FILES_AND_CODE>
{ctx}
</FILES_AND_CODE>
{dep_block}</CONTEXT>

<TASK>
{user_task.strip()}
</TASK>"""

    def build(
        self,
        user_task: str,
        chunks: list[ContextChunk],
        *,
        extra_dependencies: Optional[str] = None,
        max_context_tokens: Optional[int] = None,
    ) -> tuple[str, str]:
        """Return (system_prompt, user_message) for chat APIs."""
        user = self.build_user_message(
            user_task,
            chunks,
            extra_dependencies=extra_dependencies,
            max_context_tokens=max_context_tokens,
        )
        return self.system_prompt, user

    def build_single_prompt(
        self,
        user_task: str,
        chunks: list[ContextChunk],
        *,
        extra_dependencies: Optional[str] = None,
        max_context_tokens: Optional[int] = None,
    ) -> str:
        """One string for /api/generate (legacy) — system and user concatenated."""
        sys_p, user = self.build(
            user_task,
            chunks,
            extra_dependencies=extra_dependencies,
            max_context_tokens=max_context_tokens,
        )
        return f"<SYSTEM>\n{sys_p}\n</SYSTEM>\n\n<USER>\n{user}\n</USER>"


def build_coding_prompt(
    user_task: str,
    chunks: list[ContextChunk],
    *,
    system_prompt: Optional[str] = None,
    extra_dependencies: Optional[str] = None,
    max_context_tokens: Optional[int] = None,
) -> tuple[str, str]:
    """Functional helper: same as PromptBuilder(...).build(...)."""
    b = PromptBuilder(system_prompt=system_prompt or DEFAULT_SYSTEM_PROMPT)
    return b.build(
        user_task,
        chunks,
        extra_dependencies=extra_dependencies,
        max_context_tokens=max_context_tokens,
    )
