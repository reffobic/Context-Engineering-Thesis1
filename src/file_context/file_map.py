"""
File map: list of (path, purpose) describing a codebase.
Used as input to the context selector — no actual code content, just metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class FileEntry:
    path: str
    purpose: str

    def __str__(self) -> str:
        return f"{self.path}\t{self.purpose}"


@dataclass
class FileMap:
    """A project's file map: name and one-line purpose per file."""

    name: str
    entries: List[FileEntry] = field(default_factory=list)

    def to_prompt_text(self) -> str:
        """Format for inclusion in the LLM prompt."""
        lines = [f"File\tPurpose", ""]
        for e in self.entries:
            lines.append(f"{e.path}\t{e.purpose}")
        return "\n".join(lines)

    def paths(self) -> List[str]:
        return [e.path for e in self.entries]
