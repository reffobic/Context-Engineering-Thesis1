"""
Person 5 — Prompt builder & structured context injection.

Consumes pruned/retrieved code chunks (from P3/P4) and builds prompts for the LLM.
Does not implement retrieval or pruning.

Contract for chunks (dicts from upstream):
  - text (str, required): code or snippet body
  - path (str, optional): file path
  - symbol (str, optional): function/class name
  - language (str, optional): e.g. python, c
  - dependencies (str, optional): free-text dependency note
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

ContextChunk = Dict[str, Any]


DEFAULT_SYSTEM_PROMPT = """You are an expert software engineer assisting with code changes.
Rules:
- Use only the information in the CONTEXT sections when discussing the codebase.
- If context is insufficient, state what is missing instead of inventing files or APIs.
- Prefer concise, correct code and explain non-obvious choices briefly."""


# ---------------------------------------------------------------------------
# PromptBuilder
# ---------------------------------------------------------------------------


@dataclass
class PromptBuilder:
    """
    Assembles system + user messages with labeled sections (FILES / FUNCTIONS / DEPENDENCIES / TASK).
    P1 can pass this output to OllamaAgent; P6 can log token counts on the built strings.
    """

    system_prompt: str = DEFAULT_SYSTEM_PROMPT

    def format_context_body(self, chunks: List[ContextChunk]) -> str:
        """Render chunks as FILES:/code blocks (plan: FILES, FUNCTIONS, DEPENDENCIES, TASK)."""
        if not chunks:
            return "(no context chunks — retrieval/pruning may not have run yet)"

        blocks: List[str] = []
        for i, c in enumerate(chunks, 1):
            path = c.get("path") or "(unknown path)"
            symbol = c.get("symbol") or ""
            lang = c.get("language") or ""
            text = (c.get("text") or "").strip()
            meta = f"#{i} path={path}"
            if symbol:
                meta += f" symbol={symbol}"
            if lang:
                meta += f" lang={lang}"
            blocks.append(f"{meta}\n{text}")
        return "\n\n".join(blocks)

    def collect_dependencies_note(self, chunks: List[ContextChunk]) -> Optional[str]:
        """Optional DEPENDENCIES section from chunk metadata or explicit field."""
        notes = [c.get("dependencies") for c in chunks if c.get("dependencies")]
        if not notes:
            return None
        return "\n".join(str(n) for n in notes)

    def build_user_message(
        self,
        user_task: str,
        chunks: List[ContextChunk],
        *,
        extra_dependencies: Optional[str] = None,
    ) -> str:
        """
        Structured user message with XML-style tags (easy to parse and safe boundaries).
        """
        ctx = self.format_context_body(chunks)
        dep_from_chunks = self.collect_dependencies_note(chunks)
        dep_parts: List[str] = []
        if dep_from_chunks:
            dep_parts.append(dep_from_chunks)
        if extra_dependencies:
            dep_parts.append(extra_dependencies.strip())
        dep_block = "\n".join(dep_parts) if dep_parts else None

        lines = [
            "<CONTEXT>",
            "<FILES_AND_CODE>",
            ctx,
            "</FILES_AND_CODE>",
        ]
        if dep_block:
            lines.extend(["<DEPENDENCIES>", dep_block, "</DEPENDENCIES>"])
        lines.extend(
            [
                "</CONTEXT>",
                "",
                "<TASK>",
                user_task.strip(),
                "</TASK>",
            ]
        )
        return "\n".join(lines)

    def build(
        self,
        user_task: str,
        chunks: List[ContextChunk],
        *,
        extra_dependencies: Optional[str] = None,
    ) -> tuple[str, str]:
        """Return (system_prompt, user_message) for chat APIs."""
        user = self.build_user_message(user_task, chunks, extra_dependencies=extra_dependencies)
        return self.system_prompt, user

    def build_single_prompt(
        self,
        user_task: str,
        chunks: List[ContextChunk],
        *,
        extra_dependencies: Optional[str] = None,
    ) -> str:
        """One string for /api/generate (legacy) — system and user concatenated."""
        sys_p, user = self.build(user_task, chunks, extra_dependencies=extra_dependencies)
        return f"<SYSTEM>\n{sys_p}\n</SYSTEM>\n\n<USER>\n{user}\n</USER>"


def build_coding_prompt(
    user_task: str,
    chunks: List[ContextChunk],
    *,
    system_prompt: Optional[str] = None,
    extra_dependencies: Optional[str] = None,
) -> tuple[str, str]:
    """
    Functional helper: same as PromptBuilder(...).build(...).
    """
    b = PromptBuilder(system_prompt=system_prompt or DEFAULT_SYSTEM_PROMPT)
    return b.build(user_task, chunks, extra_dependencies=extra_dependencies)
