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
