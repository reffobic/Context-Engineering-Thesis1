"""Lenient reader for the one-object JSON replies of the routing/sufficiency judges.

The 9B judge often writes valid-looking JSON that ``json.loads`` rejects: unescaped double quotes inside "reason"
(``transform_output="pandas"``), a raw newline inside the string, or a missing closing quote. The answer fields
(sufficient / need / confidence / route) are intact in those replies, so a strict parse throws away a real decision
and the caller falls back to LC. ``loads_lenient`` returns exactly what ``json.loads`` returns when that works; only on
failure does it read the answer fields with regexes. It never invents a decision: no recognisable key -> ValueError,
and the callers' existing fail-safe fallback applies.
"""

from __future__ import annotations

import json
import re

_BOOL = re.compile(r'"sufficient"\s*:\s*(true|false)', re.I)
_WORD = {k: re.compile(rf'"{k}"\s*:\s*"([A-Za-z_]+)"', re.I) for k in ("confidence", "need", "route")}
_REASON = re.compile(r'"reason"\s*:\s*"(.*)', re.S)
# With the reason-first prompt the reason is followed by more fields: stop it at the next `", "key"` boundary.
_REASON_END = re.compile(r'"\s*,\s*"(?:sufficient|confidence|need|route)"\s*:', re.I)


def loads_lenient(text: str) -> dict:
    try:
        return json.loads(text, strict=False)          # strict=False accepts raw control characters inside strings
    except json.JSONDecodeError as exc:
        err = exc
    out: dict = {}
    m = _BOOL.search(text)
    if m:
        out["sufficient"] = m.group(1).lower() == "true"
    for key, rx in _WORD.items():
        m = rx.search(text)
        if m:
            out[key] = m.group(1)
    if not out:
        raise err
    m = _REASON.search(text)
    if m:
        rest = m.group(1)
        e = _REASON_END.search(rest)
        if e:
            rest = rest[:e.start()]
        out["reason"] = re.sub(r'["}\s]+$', "", rest).strip()
    return out
