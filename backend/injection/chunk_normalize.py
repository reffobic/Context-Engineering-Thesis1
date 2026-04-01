"""
Normalize raw chunk text before PromptBuilder adds its own line-number column.

If upstream text already uses an editor-style ``  NN | `` prefix on most lines,
stripping one column avoids ``  10 |   10 | code`` double numbering.
"""

from __future__ import annotations

import re

# Line that starts with optional spaces, digits, pipe (common editor / copy-paste)
_LINE_NUM_PREFIX = re.compile(r"^\s*\d+\s*\|\s?")


def strip_redundant_line_number_column(text: str) -> str:
    """
    If ≥70% of non-empty lines match a leading ``NN |`` column, strip that prefix once per line.
    Otherwise return ``text`` unchanged.
    """
    lines = text.splitlines()
    nonempty = [ln for ln in lines if ln.strip()]
    if len(nonempty) < 2:
        return text
    hits = sum(1 for ln in nonempty if _LINE_NUM_PREFIX.match(ln))
    if hits / len(nonempty) < 0.7:
        return text
    out = []
    for ln in lines:
        if _LINE_NUM_PREFIX.match(ln):
            out.append(_LINE_NUM_PREFIX.sub("", ln, count=1))
        else:
            out.append(ln)
    return "\n".join(out)
