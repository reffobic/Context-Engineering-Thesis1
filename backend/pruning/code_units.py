# code_units.py
# Person 4 — lexical units for code-aware Selective Context pruning.
#
# Splits a code chunk into lexical units (identifiers, keywords, literals,
# operators, punctuation, comments, docstrings, whitespace) so pruning can
#   1. keep or drop whole identifiers instead of BPE fragments, and
#   2. weight units by kind (code logic > syntax), on top of the causal LM's
#      self-information.
# Also provides the line-level comment filter. No torch dependency.

from __future__ import annotations

import ast
import io
import keyword
import re
import tokenize
from dataclasses import dataclass
from typing import Optional


# ── Types ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class Unit:
    start: int  # char offset into the text
    end: int
    text: str
    kind: str


# Never pruned: layout, and comments/docstrings that survived filter_comments()
# (token-pruning English inside them only garbles it).
PROTECTED_KINDS = frozenset({"whitespace", "newline", "comment", "docstring"})


@dataclass
class CodeUnitWeights:
    """Multipliers applied to a unit's self-information, by kind.

    Values > 1 make a kind harder to prune; < 1 easier. Kept moderate so the
    causal LM still decides *which* identifiers/calls/syntax are redundant.
    """

    definition: float = 1.5   # name right after def/class/function/...
    call: float = 1.5         # identifier followed by "("
    identifier: float = 1.3
    control_kw: float = 1.3
    logic_op: float = 1.3     # not/and/or/is/in, comparisons: dropping them flips meaning
    literal: float = 1.2
    other_kw: float = 0.8
    operator: float = 0.7
    punct: float = 0.5
    # Prior strength: each unit's self-information is smoothed by adding
    # prior * (mean unit self-information of the chunk) before weighting, so a
    # near-zero (very predictable) unit still gets its kind's weight. 0 = pure
    # model scores; large values = kind alone decides.
    prior: float = 1.0
    # Kinds never pruned in addition to PROTECTED_KINDS. The causal LM finds
    # these predictable *because* they are structural, so its scores can't be
    # trusted for them; they are also few tokens and the code's meaning depends
    # on them. The model decides among the remaining kinds.
    keep_kinds: tuple[str, ...] = ("control_kw", "logic_op", "definition")

    def for_kind(self, kind: str) -> float:
        return getattr(self, kind, 1.0)


DEFAULT_CODE_WEIGHTS = CodeUnitWeights()


# ── Language tables ────────────────────────────────────────────

CONTROL_KEYWORDS = frozenset({
    "if", "elif", "else", "for", "while", "do", "switch", "case", "match",
    "return", "raise", "throw", "try", "except", "catch", "finally", "with",
    "yield", "await", "break", "continue", "assert", "goto", "loop", "defer",
    "select", "when", "unless", "until", "rescue", "ensure",
})

DEFINITION_KEYWORDS = frozenset({
    "def", "class", "function", "func", "fn", "struct", "interface", "enum",
    "trait", "impl", "type", "module",
})

LITERAL_KEYWORDS = frozenset({
    "True", "False", "None", "true", "false", "null", "nil", "undefined",
    "NaN", "Infinity",
})

GENERIC_OTHER_KEYWORDS = frozenset({
    "import", "from", "export", "package", "use", "require", "include",
    "const", "let", "var", "val", "mut", "static", "final", "public",
    "private", "protected", "internal", "abstract", "virtual", "override",
    "async", "new", "delete", "this", "self", "cls", "super", "extends",
    "implements", "void", "int", "float", "double", "char", "bool",
    "boolean", "string", "long", "short", "byte", "unsigned", "signed",
    "in", "of", "is", "as", "not", "and", "or", "pub", "crate", "where",
    "end", "begin", "then", "typeof", "instanceof", "go", "chan", "map",
    "lambda", "global", "nonlocal", "pass", "del", "auto", "extern",
})

PY_OTHER_KEYWORDS = (frozenset(keyword.kwlist) | {"self", "cls"}) - CONTROL_KEYWORDS \
    - DEFINITION_KEYWORDS - LITERAL_KEYWORDS

LINE_COMMENT = {
    "python": "#", "ruby": "#", "shell": "#", "yaml": "#", "toml": "#",
    "sql": "--",
}
C_STYLE = {"javascript", "typescript", "tsx", "jsx", "java", "c", "cpp",
           "csharp", "go", "rust", "php", "kotlin", "swift"}

PUNCT = frozenset("()[]{},:;.@") | {"->", "=>", "::", "..."}

LOGIC_WORDS = frozenset({"not", "and", "or", "is", "in", "instanceof", "typeof"})
LOGIC_OPS = frozenset({"==", "!=", "===", "!==", "<", ">", "<=", ">=", "!", "&&", "||"})


# ── Lexing ─────────────────────────────────────────────────────

def lex_units(text: str, language: str) -> list[Unit]:
    """Split *text* into contiguous units covering every character."""
    units: Optional[list[Unit]] = None
    if language == "python":
        units = _lex_python(text)
    if units is None:
        units = _lex_regex(text, language)
    units = _classify_names(units, language)
    if language == "python":
        units = _mark_docstrings(units, text)
    return units


def _fill_gaps(text: str, spans: list[tuple[int, int, str]]) -> list[Unit]:
    """Turn sorted (start, end, kind) spans into contiguous units; gaps become layout."""
    units: list[Unit] = []
    pos = 0

    def add_layout(a: int, b: int) -> None:
        for m in re.finditer(r"\n|[^\n]+", text[a:b]):
            kind = "newline" if m.group() == "\n" else "whitespace"
            units.append(Unit(a + m.start(), a + m.end(), m.group(), kind))

    for start, end, kind in spans:
        if start < pos:
            continue  # overlapping span (shouldn't happen) — skip
        if start > pos:
            add_layout(pos, start)
        units.append(Unit(start, end, text[start:end], kind))
        pos = end
    if pos < len(text):
        add_layout(pos, len(text))
    return units


_PY_SKIP = {tokenize.NEWLINE, tokenize.NL, tokenize.INDENT, tokenize.DEDENT,
            tokenize.ENDMARKER, tokenize.ENCODING}
_FSTRING_TYPES = {getattr(tokenize, n) for n in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END")
                  if hasattr(tokenize, n)}


def _lex_python(text: str) -> Optional[list[Unit]]:
    """Lex with the stdlib tokenizer; None if the fragment doesn't tokenize."""
    line_starts = [0]
    for m in re.finditer(r"\n", text):
        line_starts.append(m.end())

    def offset(row: int, col: int) -> int:
        return line_starts[row - 1] + col

    spans: list[tuple[int, int, str]] = []
    fstring_start: Optional[int] = None
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type in _PY_SKIP:
                continue
            start, end = offset(*tok.start), offset(*tok.end)
            if tok.type in _FSTRING_TYPES:
                # Python 3.12+ splits f-strings; merge back into one literal.
                if tok.type == getattr(tokenize, "FSTRING_START", None) and fstring_start is None:
                    fstring_start = start
                elif tok.type == getattr(tokenize, "FSTRING_END", None) and fstring_start is not None:
                    spans.append((fstring_start, end, "literal"))
                    fstring_start = None
                continue
            if fstring_start is not None:
                continue  # tokens inside an f-string replacement field
            if tok.type == tokenize.COMMENT:
                kind = "comment"
            elif tok.type in (tokenize.STRING, tokenize.NUMBER):
                kind = "literal"
            elif tok.type == tokenize.NAME:
                kind = "name"
            elif tok.type == tokenize.OP:
                kind = _op_kind(tok.string)
            else:
                kind = "operator"
            spans.append((start, end, kind))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None
    return _fill_gaps(text, spans)


_STRING_RE = r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|`(?:\\.|[^`\\])*`'
_NUMBER_RE = r"\b(?:0[xXbBoO][0-9a-fA-F_]+|\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?)\b"
_OPERATOR_RE = r"===|!==|\*\*=|//=|>>=|<<=|->|=>|::|\.\.\.|[-+*/%&|^<>=!]=|&&|\|\||<<|>>|\*\*|//|\+\+|--|[-+*/%&|^~<>=!?]"


def _lex_regex(text: str, language: str) -> list[Unit]:
    """Generic lexer for non-Python languages and Python fragments tokenize rejects."""
    comment_parts = []
    if language in C_STYLE:
        comment_parts += [r"/\*[\s\S]*?\*/", r"//[^\n]*"]
    line = LINE_COMMENT.get(language)
    if line:
        comment_parts.append(re.escape(line) + r"[^\n]*")
    if not comment_parts:
        comment_parts = [r"#[^\n]*", r"//[^\n]*"]

    pattern = re.compile(
        "|".join([
            f"(?P<comment>{'|'.join(comment_parts)})",
            f"(?P<literal>{_STRING_RE}|{_NUMBER_RE})",
            r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)",
            f"(?P<op>{_OPERATOR_RE})",
            r"(?P<punct>[()\[\]{},:;.@])",
            r"(?P<other>[^\s])",
        ])
    )
    spans: list[tuple[int, int, str]] = []
    for m in pattern.finditer(text):
        kind = m.lastgroup
        if kind == "op":
            kind = _op_kind(m.group())
        elif kind == "other":
            kind = "punct"
        spans.append((m.start(), m.end(), kind))
    return _fill_gaps(text, spans)


def _op_kind(op: str) -> str:
    if op in LOGIC_OPS:
        return "logic_op"
    return "punct" if op in PUNCT else "operator"


def _classify_names(units: list[Unit], language: str) -> list[Unit]:
    """Resolve generic 'name' units into keyword / definition / call / identifier."""
    other_kw = PY_OTHER_KEYWORDS if language == "python" else GENERIC_OTHER_KEYWORDS
    significant = [i for i, u in enumerate(units) if u.kind not in ("whitespace", "newline", "comment")]
    position = {idx: n for n, idx in enumerate(significant)}
    out: list[Unit] = []
    for i, u in enumerate(units):
        if u.kind != "name":
            out.append(u)
            continue
        word = u.text
        n = position[i]
        prev = units[significant[n - 1]] if n > 0 else None
        nxt = units[significant[n + 1]] if n + 1 < len(significant) else None
        if word in LITERAL_KEYWORDS:
            # "is None", "== null": the operand is part of the condition's meaning.
            follows_logic = prev is not None and (prev.text in LOGIC_WORDS or prev.text in LOGIC_OPS)
            kind = "logic_op" if follows_logic else "literal"
        elif word in LOGIC_WORDS:
            kind = "logic_op"
        elif word in CONTROL_KEYWORDS:
            kind = "control_kw"
        elif word in DEFINITION_KEYWORDS or word in other_kw:
            kind = "other_kw"
        elif prev is not None and prev.text in DEFINITION_KEYWORDS:
            kind = "definition"
        elif nxt is not None and nxt.text == "(":
            kind = "call"
        else:
            kind = "identifier"
        out.append(Unit(u.start, u.end, u.text, kind))
    return out


def _mark_docstrings(units: list[Unit], text: str) -> list[Unit]:
    """A string literal that is a whole statement on its own lines is a docstring."""
    out: list[Unit] = []
    for u in units:
        if u.kind == "literal" and u.text.lstrip("rRbBuUfF")[:1] in ("'", '"'):
            line_start = text.rfind("\n", 0, u.start) + 1
            line_end = text.find("\n", u.end)
            line_end = len(text) if line_end == -1 else line_end
            if not text[line_start:u.start].strip() and not text[u.end:line_end].strip():
                u = Unit(u.start, u.end, u.text, "docstring")
        out.append(u)
    return out


# ── Comment filter (line-level, rule-based) ────────────────────

STOPWORDS = frozenset("""
a an the and or but if then else of to in on at by for with from as is are was
were be been being it its this that these those there here we you i he she they
them our your their do does did doing how what which who whom why when where
can could should would will shall may might must not no yes so than too very
just also into over under out up down about all any each few more most other
some such only own same get got use used using make made
""".split())

_WHY_WORDS = re.compile(
    r"\b(because|must|should|note|warning|warn|todo|fixme|hack|xxx|assum\w*|"
    r"invariant|otherwise|never|always|only|unless|ensure|important|caveat|why|"
    r"deprecated|workaround|bug)\b",
    re.IGNORECASE,
)
_PRAGMA = re.compile(
    r"(\bnoqa\b|type:\s*ignore|\bpylint:|\bfmt:\s*(on|off|skip)|\bpragma\b|"
    r"eslint-disable|\bnolint\b|@ts-ignore|@ts-expect-error|istanbul ignore|"
    r"^\s*-\*-.*-\*-\s*$|^!\s*/)",
    re.IGNORECASE,
)
_LICENSE = re.compile(r"(copyright|\blicen[cs]e\b|all rights reserved|spdx-license)", re.IGNORECASE)
_CODE_START = re.compile(
    r"^\s*(def|class|return|import|from|if|for|while|print|var|let|const|"
    r"function|elif|else|try|except|raise|yield|await)\b"
)


def _comment_body(unit: Unit) -> str:
    """Strip comment / docstring delimiters."""
    t = unit.text
    if unit.kind == "docstring":
        t = t.lstrip("rRbBuUfF")
        for q in ('"""', "'''", '"', "'"):
            if t.startswith(q) and t.endswith(q) and len(t) >= 2 * len(q):
                return t[len(q):-len(q)]
        return t
    if t.startswith("/*"):
        return t[2:-2] if t.endswith("*/") else t[2:]
    for marker in ("//", "#", "--"):
        if t.startswith(marker):
            return t[len(marker):]
    return t


def _content_words(text: str) -> set[str]:
    """Lower-cased content words, with snake_case/camelCase identifiers split into parts."""
    words: set[str] = set()
    for raw in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text):
        parts = re.split(r"_+|(?<=[a-z0-9])(?=[A-Z])", raw)
        for w in [raw, *parts]:
            w = w.lower()
            if len(w) >= 3 and w not in STOPWORDS:
                words.add(w)
    return words


def _looks_like_code(body: str, language: str) -> bool:
    s = body.strip()
    if not s:
        return False
    if language == "python":
        # Must parse as Python *and* look like code (a bare word like "TODO" parses too).
        if not (re.search(r"[=()\[\]{}:]", s) or _CODE_START.match(s)):
            return False
        try:
            ast.parse(s)
            return True
        except SyntaxError:
            return False
    return bool(re.search(r"[;{}]\s*$", s) or re.match(r"^[\w.$]+\s*(=|\()", s))


def comment_keep_reason(body: str, language: str, query_words: set[str],
                        neighbour_words: set[str]) -> Optional[str]:
    """Why a comment line is worth keeping, or None to drop it."""
    stripped = body.strip()
    if not re.search(r"[A-Za-z0-9]", stripped):
        return None  # empty or decorative separator
    if _PRAGMA.search(stripped) or _LICENSE.search(stripped):
        return None
    if _looks_like_code(stripped, language):
        return None  # commented-out code
    if _WHY_WORDS.search(stripped):
        return "why"
    words = _content_words(stripped)
    if words & query_words:
        return "query"
    if words & neighbour_words:
        return "code"
    return None


def filter_comments(text: str, language: str, query: str = "") -> str:
    """
    Drop comments / docstring lines that add no semantic value for the assessor.

    Comments are judged per unit; docstrings keep their summary line and the
    rest is judged line by line. Lines left empty by a removal are dropped.
    """
    units = lex_units(text, language)
    query_words = _content_words(query)

    # Content words of each line's code (non-comment units), for "adjacent code".
    lines = text.split("\n")
    line_code_words: list[set[str]] = [set() for _ in lines]
    line_starts = [0]
    for ln in lines[:-1]:
        line_starts.append(line_starts[-1] + len(ln) + 1)

    def line_of(pos: int) -> int:
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo

    for u in units:
        if u.kind not in PROTECTED_KINDS:
            line_code_words[line_of(u.start)] |= _content_words(u.text)

    def neighbour_words(line_no: int) -> set[str]:
        # Same line (trailing comment) plus the next line that has code.
        words = set(line_code_words[line_no])
        for j in range(line_no + 1, len(lines)):
            if line_code_words[j]:
                words |= line_code_words[j]
                break
        return words

    replacements: list[tuple[int, int, str]] = []
    for u in units:
        if u.kind == "comment":
            reason = comment_keep_reason(
                _comment_body(u), language, query_words, neighbour_words(line_of(u.start))
            )
            if reason is None:
                replacements.append(_removal_span(text, u.start, u.end))
        elif u.kind == "docstring":
            new = _filter_docstring(u, language, query_words, neighbour_words(line_of(u.end)))
            if new != u.text:
                replacements.append((u.start, u.end, new))

    out = text
    for start, end, new in reversed(replacements):
        out = out[:start] + new + out[end:]
    return out


def _removal_span(text: str, start: int, end: int) -> tuple[int, int, str]:
    """Span to delete for a dropped comment: its whole line if it is alone on it,
    otherwise the comment plus the spaces before it."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if not text[line_start:start].strip() and not text[end:(len(text) if line_end == -1 else line_end)].strip():
        if line_end != -1:
            return (line_start, line_end + 1, "")
        # Last line: also eat the preceding newline.
        return (max(0, line_start - 1), len(text), "")
    while start > line_start and text[start - 1] in " \t":
        start -= 1
    return (start, end, "")


def _filter_docstring(unit: Unit, language: str, query_words: set[str],
                      neighbour_words: set[str]) -> str:
    """Keep the summary line; keep other lines only if they carry a keep reason."""
    body = _comment_body(unit)
    body_lines = body.split("\n")
    first_idx = next((i for i, ln in enumerate(body_lines) if ln.strip()), None)
    if first_idx is None:
        return ""
    kept = [body_lines[first_idx]]
    for ln in body_lines[first_idx + 1:]:
        if comment_keep_reason(ln, language, query_words, neighbour_words):
            kept.append(ln)
    if len(kept) == len([ln for ln in body_lines if ln.strip()]):
        return unit.text  # nothing dropped
    prefix_len = len(unit.text) - len(unit.text.lstrip("rRbBuUfF"))
    quote_len = 3 if unit.text[prefix_len:prefix_len + 3] in ('"""', "'''") else 1
    opening = unit.text[:prefix_len + quote_len]
    closing = unit.text[len(unit.text) - quote_len:]
    # Keep the original layout: leading newline(s) and the closing-quote indentation.
    lead = body_lines[:first_idx]
    tail = [body_lines[-1]] if len(body_lines) > 1 and not body_lines[-1].strip() else []
    return opening + "\n".join(lead + kept + tail) + closing
