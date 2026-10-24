"""Chunk -> file / module / function lists, in the harness (no pipeline change; Appendix A row 13).

Rules (LocAgent naming, see datasets.derive_labels):
  file      = chunk file path
  function  = "path:Class.method" or "path:func"; a trailing ".__init__" is dropped
  module    = "path:" + first dotted component of the function name (top-level function = own module)
  nested functions map to their enclosing top-level function / method (the chunker emits them separately)
  class chunk  -> module and function "path:Class" (LocAgent collapses Class.__init__ to Class)
  anything else (module-level blocks, non-Python files) -> file only; logged as unmappable with a reason.
Lists are de-duplicated keeping the first (best) rank.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Def:
    qualname: str
    name: str
    kind: str          # "class" | "function"
    start: int
    end: int


def collect_defs(source: str) -> list[Def]:
    tree = ast.parse(source)
    out: list[Def] = []

    def walk(node, stack: list[str], in_func: bool) -> None:
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, ast.ClassDef):
                if in_func:                       # class defined inside a function: part of that function
                    walk(ch, stack, True)
                    continue
                q = ".".join(stack + [ch.name])
                out.append(Def(q, ch.name, "class", ch.lineno, ch.end_lineno or ch.lineno))
                walk(ch, stack + [ch.name], False)
            elif isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if in_func:                       # nested function: attributed to the enclosing one
                    walk(ch, stack, True)
                    continue
                q = ".".join(stack + [ch.name])
                out.append(Def(q, ch.name, "function", ch.lineno, ch.end_lineno or ch.lineno))
                walk(ch, stack, True)
            else:
                walk(ch, stack, in_func)

    walk(tree, [], False)
    return out


class Mapper:
    def __init__(self, checkout: Path) -> None:
        self.root = checkout
        self._defs: dict[str, list[Def] | str] = {}

    def defs(self, path: str):
        if path not in self._defs:
            f = self.root / path
            if not path.endswith(".py"):
                self._defs[path] = "not_python"
            elif not f.is_file():
                self._defs[path] = "file_not_in_checkout"
            else:
                try:
                    self._defs[path] = collect_defs(f.read_text(encoding="utf-8", errors="replace"))
                except SyntaxError:
                    self._defs[path] = "syntax_error"
        return self._defs[path]

    def map_item(self, item: dict) -> dict:
        path = (item.get("file") or "").replace("\\", "/")
        out = {"file": path or None, "module": None, "function": None, "unmappable": None}
        if not path:
            out["unmappable"] = "no_file"
            return out
        defs = self.defs(path)
        if isinstance(defs, str):
            out["unmappable"] = defs
            return out
        start, name, ctype = item.get("start_line"), item.get("name"), item.get("chunk_type")
        d = None
        if start is not None:
            exact = [x for x in defs if x.start == start and (not name or x.name == name)]
            if exact:
                d = exact[0]
            else:
                inside = [x for x in defs if x.start <= start <= x.end]
                if name:
                    named = [x for x in inside if x.name == name]
                    inside = named or inside
                if inside:
                    d = min(inside, key=lambda x: x.end - x.start)       # innermost
        if d is None:
            out["unmappable"] = "block_outside_any_definition" if ctype in ("block", None) else "no_enclosing_definition"
            return out
        qual = d.qualname[:-len(".__init__")] if d.qualname.endswith(".__init__") else d.qualname
        out["function"] = f"{path}:{qual}"
        out["module"] = f"{path}:{d.qualname.split('.')[0]}"
        return out

    def rank_lists(self, items: list[dict]) -> dict:
        files, modules, functions, unmapped = [], [], [], []
        for it in items:
            m = self.map_item(it)
            if m["file"] and m["file"] not in files:
                files.append(m["file"])
            if m["unmappable"]:
                unmapped.append({"file": m["file"], "name": it.get("name"), "start_line": it.get("start_line"),
                                 "chunk_type": it.get("chunk_type"), "reason": m["unmappable"]})
                continue
            if m["module"] not in modules:
                modules.append(m["module"])
            if m["function"] not in functions:
                functions.append(m["function"])
        return {"files": files, "modules": modules, "functions": functions, "unmapped": unmapped, "n_items": len(items)}


# ── LC scorer v2 (scorer-lc-fix) ─────────────────────────────────────────────
# _build_lc_chunks emits {text, path, symbol, language} with no start_line / chunk_type, so the line-based map_item
# cannot place an LC chunk. v2 gives each LC chunk the identifier the line-based mapper produced for the retrieved chunk
# it came from, by joining on (path, symbol) to the task's retrieved_top5 lists. The legacy behaviour stays available
# (metrics --lc-scorer legacy, the default).

def _split_id(fid: str) -> tuple[str, str]:
    path, qual = fid.split(":", 1)
    return path, qual


def _leaf(qual: str) -> str:
    return qual.split(".")[-1]


def resolve_lc_item(path: str | None, name: str | None, reason: str, retrieved: dict, mapper: "Mapper | None") -> dict:
    """-> {"function", "module", "how"}; how in join | join_unmapped | ast_retrieved | ast_unique | not_python | unresolved."""
    none = {"function": None, "module": None}
    if not path or reason in ("no_file", "not_python", "file_not_in_checkout", "syntax_error"):
        return {**none, "how": reason or "no_file"}
    funcs = [_split_id(f) for f in retrieved.get("functions") or [] if f.split(":", 1)[0] == path]
    cand = [f"{p}:{q}" for p, q in funcs if _leaf(q) == name]
    still_unmapped = any(u.get("file") == path and u.get("name") == name for u in retrieved.get("unmapped") or [])
    if len(cand) == 1 and not still_unmapped:
        q = _split_id(cand[0])[1]
        return {"function": cand[0], "module": f"{path}:{q.split('.')[0]}", "how": "join"}
    if not cand and still_unmapped:
        return {**none, "how": "join_unmapped"}               # the originating retrieved chunk was itself unmappable
    # ambiguous or missing: AST lookup in the checkout
    if mapper is None:
        return {**none, "how": "unresolved"}
    defs = mapper.defs(path)
    if isinstance(defs, str):
        return {**none, "how": "unresolved"}
    try:
        tree = ast.parse((mapper.root / path).read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return {**none, "how": "unresolved"}
    enc = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == name:
            inside = [d for d in defs if d.start <= n.lineno <= d.end]
            if inside:
                d = min(inside, key=lambda x: x.end - x.start)
                enc.add(d.qualname[:-len(".__init__")] if d.qualname.endswith(".__init__") else d.qualname)
    retrieved_q = {q for _, q in funcs}
    pick = enc & retrieved_q
    if len(pick) == 1:
        q = next(iter(pick))
        return {"function": f"{path}:{q}", "module": f"{path}:{q.split('.')[0]}", "how": "ast_retrieved"}
    if not pick and len(enc) == 1:
        q = next(iter(enc))
        return {"function": f"{path}:{q}", "module": f"{path}:{q.split('.')[0]}", "how": "ast_unique"}
    return {**none, "how": "unresolved"}


def rescore_lc_final(final: dict, retrieved: dict, mapper: "Mapper | None") -> tuple[dict, list[dict]]:
    """New final_context lists for an LC task + one resolution record per chunk. Files are unchanged."""
    if final["n_items"] != final["unmapped_n"] or final["modules"] or final["functions"]:
        raise ValueError("final_context is not an all-unmapped LC list")
    modules, functions, recs = [], [], []
    for u in final["unmapped"]:
        r = resolve_lc_item(u.get("file"), u.get("name"), u.get("reason") or "", retrieved, mapper)
        recs.append({"file": u.get("file"), "name": u.get("name"), **r})
        if r["function"]:
            if r["module"] not in modules:
                modules.append(r["module"])
            if r["function"] not in functions:
                functions.append(r["function"])
    new = {**final, "modules": modules, "functions": functions}
    return new, recs
