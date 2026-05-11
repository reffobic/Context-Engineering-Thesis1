"""
graph_builder.py
================

Multi-language call-graph builder. Walks a repository, finds function/class
definitions, resolves intra-repo call / import / inheritance edges, and emits
a JSON sidecar (`call_graph.json`) that the graph-aware retrieval reranker
consumes at query time.

Supported languages (extension based):
    Python (AST), JavaScript / TypeScript, Java, Go, C, C++ (regex).

Public entry point:
    ``build_call_graph(repo_path, languages=None, output_path=None) -> dict``

Lower-level surface (kept for reuse / testing):
    ``CallGraph``, ``CallGraphNode``, ``CallGraphBuilder``.

Ported from the ``graph-embeddings`` branch's structural pipeline. The
embedding-fusion side of that branch is intentionally *not* ported here -
the call graph is stored as a separate sidecar so the same Chroma index can
serve both ``vector`` and ``graph`` retrieval modes.
"""

from __future__ import annotations

import ast
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional


logger = logging.getLogger("graph_builder")


# ── language dispatch table ──────────────────────────────────────────────────

LANG_EXT: dict[str, str] = {
    ".py":   "python",
    ".js":   "javascript", ".jsx": "javascript",
    ".ts":   "typescript", ".tsx": "typescript",
    ".java": "java",
    ".go":   "go",
    ".c":    "c", ".h": "c",
    ".cpp":  "cpp", ".cc": "cpp", ".hpp": "cpp",
}


# ── regex patterns per language ──────────────────────────────────────────────

FUNC_PATTERNS: dict[str, list[tuple[str, re.Pattern]]] = {
    "javascript": [
        ("function", re.compile(r'\bfunction\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(')),
        ("function", re.compile(r'\bconst\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?\(')),
        ("function", re.compile(r'\bconst\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?\w+\s*=>')),
        ("class",    re.compile(r'\bclass\s+([A-Za-z_$][A-Za-z0-9_$]*)')),
    ],
    "typescript": [
        ("function", re.compile(r'\bfunction\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*[<(]')),
        ("function", re.compile(r'\bconst\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?\(')),
        ("function", re.compile(r'\b(?:public|private|protected|static|async)[\s\w]*\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(')),
        ("class",    re.compile(r'\bclass\s+([A-Za-z_$][A-Za-z0-9_$]*)')),
    ],
    "java": [
        ("function", re.compile(
            r'\b(?:public|private|protected|static|final|abstract|synchronized)'
            r'(?:\s+(?:public|private|protected|static|final|abstract|synchronized))*'
            r'\s+\S+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\('
        )),
        ("class",    re.compile(r'\bclass\s+([A-Za-z_][A-Za-z0-9_]*)')),
    ],
    "go": [
        ("function", re.compile(r'\bfunc\s+(?:\([^)]*\)\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*\(')),
    ],
    "c": [
        ("function", re.compile(
            r'^[A-Za-z_][A-Za-z0-9_\s\*]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*\{',
            re.MULTILINE,
        )),
    ],
    "cpp": [
        ("function", re.compile(
            r'^[A-Za-z_][A-Za-z0-9_:<>\s\*&]+\s+([A-Za-z_][A-Za-z0-9_:]*)\s*\([^;]*\)\s*(?:const\s*)?\{',
            re.MULTILINE,
        )),
        ("class", re.compile(r'\bclass\s+([A-Za-z_][A-Za-z0-9_]*)')),
    ],
}

CALL_PATTERNS: dict[str, list[re.Pattern]] = {
    "javascript": [re.compile(r'\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\(')],
    "typescript": [re.compile(r'\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\(')],
    "java":       [re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*\(')],
    "go":         [re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*\(')],
    "c":          [re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*\(')],
    "cpp":        [re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*\(')],
    "python":     [re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*\(')],
}


# ── core data classes ───────────────────────────────────────────────────────

@dataclass
class CallGraphNode:
    name: str
    file_path: str
    start_line: int
    end_line: int
    node_type: str
    language: str = "unknown"

    callers: set = field(default_factory=set)
    calls: set = field(default_factory=set)
    imported_by: set = field(default_factory=set)
    imports: set = field(default_factory=set)
    inherits_from: set = field(default_factory=set)
    inherited_by: set = field(default_factory=set)

    in_degree: float = 0.0
    out_degree: float = 0.0
    pagerank: float = 0.0

    def get_identifier(self) -> str:
        return f"{self.file_path}:{self.name}"

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()}
        for key in ("callers", "calls", "imported_by", "imports",
                    "inherits_from", "inherited_by"):
            d[key] = sorted(d[key])
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "CallGraphNode":
        obj = cls(**{k: v for k, v in data.items()
                     if k not in ("callers", "calls", "imported_by", "imports",
                                  "inherits_from", "inherited_by")})
        for key in ("callers", "calls", "imported_by", "imports",
                    "inherits_from", "inherited_by"):
            setattr(obj, key, set(data.get(key, [])))
        return obj


@dataclass
class CallGraph:
    nodes: dict = field(default_factory=dict)

    def add_node(self, node: CallGraphNode) -> None:
        self.nodes[node.get_identifier()] = node

    def get_node(self, file_path: str, func_name: str) -> Optional[CallGraphNode]:
        return self.nodes.get(f"{file_path}:{func_name}")

    def add_edge(self, caller_id: str, callee_id: str, edge_type: str) -> None:
        caller = self.nodes.get(caller_id)
        callee = self.nodes.get(callee_id)
        if not caller or not callee:
            return
        if edge_type == "calls":
            caller.calls.add(callee_id)
            callee.callers.add(caller_id)
        elif edge_type == "imports":
            caller.imports.add(callee_id)
            callee.imported_by.add(caller_id)
        elif edge_type == "inherits_from":
            caller.inherits_from.add(callee_id)
            callee.inherited_by.add(caller_id)

    def compute_degrees(self) -> None:
        if not self.nodes:
            return
        max_in  = max((len(n.callers) for n in self.nodes.values()), default=1) or 1
        max_out = max((len(n.calls)   for n in self.nodes.values()), default=1) or 1
        for n in self.nodes.values():
            n.in_degree  = len(n.callers) / max_in
            n.out_degree = len(n.calls)   / max_out

    def compute_pagerank(self, iterations: int = 10, damping: float = 0.85) -> None:
        N = len(self.nodes)
        if N == 0:
            return
        ids   = list(self.nodes)
        rank  = {i: 1 / N for i in ids}
        for _ in range(iterations):
            new_rank = {}
            for nid in ids:
                node = self.nodes[nid]
                incoming = sum(
                    rank[c] / max(len(self.nodes[c].calls), 1)
                    for c in node.callers if c in self.nodes
                )
                new_rank[nid] = (1 - damping) / N + damping * incoming
            rank = new_rank
        max_r = max(rank.values()) or 1
        for nid, node in self.nodes.items():
            node.pagerank = rank[nid] / max_r

    def to_serializable(self) -> dict:
        nodes = {k: v.to_dict() for k, v in self.nodes.items()}
        edges: list[dict] = []
        for nid, node in self.nodes.items():
            for callee in node.calls:
                edges.append({"source": nid, "target": callee, "type": "calls"})
            for imp in node.imports:
                edges.append({"source": nid, "target": imp,    "type": "imports"})
            for base in node.inherits_from:
                edges.append({"source": nid, "target": base,   "type": "inherits_from"})
        return {"nodes": nodes, "edges": edges}

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_serializable(), indent=2))

    @classmethod
    def load(cls, path: str | Path) -> "CallGraph":
        data = json.loads(Path(path).read_text())
        g = cls()
        for k, v in data["nodes"].items():
            g.nodes[k] = CallGraphNode.from_dict(v)
        return g


# ── builder ──────────────────────────────────────────────────────────────────

class CallGraphBuilder:
    """Walk a list of source files and assemble a CallGraph."""

    def __init__(self):
        self.graph = CallGraph()
        self._name_index: dict[str, set] = {}

    def build_from_files(self, file_paths: Iterable[str | Path], repo_root: str | Path) -> CallGraph:
        root = Path(repo_root).resolve()
        file_paths = [Path(fp) for fp in file_paths]

        # Phase 1 – definitions
        for fp in file_paths:
            lang = LANG_EXT.get(fp.suffix.lower())
            if not lang:
                continue
            try:
                rel = str(fp.resolve().relative_to(root))
            except ValueError:
                rel = str(fp)
            try:
                source = fp.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                logger.debug("graph_builder: cannot read %s: %s", fp, e)
                continue
            if lang == "python":
                self._extract_python_definitions(source, rel)
            else:
                self._extract_generic_definitions(source, rel, lang)

        # Build name → identifier index for relationship resolution
        for nid, node in self.graph.nodes.items():
            self._name_index.setdefault(node.name, set()).add(nid)

        # Phase 2 – relationships
        for fp in file_paths:
            lang = LANG_EXT.get(fp.suffix.lower())
            if not lang:
                continue
            try:
                rel = str(fp.resolve().relative_to(root))
            except ValueError:
                rel = str(fp)
            try:
                source = fp.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if lang == "python":
                self._extract_python_relationships(source, rel)
            else:
                self._extract_generic_relationships(source, rel, lang)

        # Phase 3 – structural metrics
        self.graph.compute_degrees()
        self.graph.compute_pagerank()
        return self.graph

    # ── Python (AST) ─────────────────────────────────────────────────────────

    def _extract_python_definitions(self, source: str, rel_path: str) -> None:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                end = getattr(node, "end_lineno", node.lineno)
                ntype = "class" if isinstance(node, ast.ClassDef) else "function"
                cgn = CallGraphNode(
                    name=node.name, file_path=rel_path,
                    start_line=node.lineno, end_line=end,
                    node_type=ntype, language="python",
                )
                self.graph.add_node(cgn)

    def _extract_python_relationships(self, source: str, rel_path: str) -> None:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return

        func_ranges: list[tuple[int, int, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end = getattr(node, "end_lineno", node.lineno)
                func_ranges.append((node.lineno, end, f"{rel_path}:{node.name}"))

        def enclosing(lineno: int) -> Optional[str]:
            for s, e, ident in func_ranges:
                if s <= lineno <= e:
                    return ident
            return None

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                caller_id = enclosing(node.lineno)
                if not caller_id:
                    continue
                callee_name = None
                if isinstance(node.func, ast.Name):
                    callee_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    callee_name = node.func.attr
                if callee_name:
                    for callee_id in self._name_index.get(callee_name, []):
                        self.graph.add_edge(caller_id, callee_id, "calls")

            elif isinstance(node, ast.ClassDef):
                child_id = f"{rel_path}:{node.name}"
                for base in node.bases:
                    base_name = base.id if isinstance(base, ast.Name) else None
                    if base_name:
                        for base_id in self._name_index.get(base_name, []):
                            self.graph.add_edge(child_id, base_id, "inherits_from")

    # ── Generic (regex) ──────────────────────────────────────────────────────

    def _extract_generic_definitions(self, source: str, rel_path: str, lang: str) -> None:
        patterns = FUNC_PATTERNS.get(lang, [])
        lines = source.splitlines()
        hits: list[tuple[int, str, str]] = []
        for ntype, pat in patterns:
            for m in pat.finditer(source):
                lineno = source[:m.start()].count("\n") + 1
                hits.append((lineno, m.group(1), ntype))

        hits.sort(key=lambda x: x[0])
        for i, (lineno, name, ntype) in enumerate(hits):
            end = hits[i + 1][0] - 1 if i + 1 < len(hits) else len(lines)
            cgn = CallGraphNode(
                name=name, file_path=rel_path,
                start_line=lineno, end_line=end,
                node_type=ntype, language=lang,
            )
            self.graph.add_node(cgn)

    def _extract_generic_relationships(self, source: str, rel_path: str, lang: str) -> None:
        call_pats = CALL_PATTERNS.get(lang, [])

        file_nodes = [n for n in self.graph.nodes.values() if n.file_path == rel_path]
        file_nodes.sort(key=lambda n: n.start_line)

        def enclosing(lineno: int) -> Optional[str]:
            best = None
            for n in file_nodes:
                if n.start_line <= lineno <= n.end_line:
                    best = n.get_identifier()
            return best

        for pat in call_pats:
            for m in pat.finditer(source):
                lineno = source[:m.start()].count("\n") + 1
                callee_name = m.group(1)
                caller_id = enclosing(lineno)
                if not caller_id:
                    continue
                if callee_name in {"if", "for", "while", "return", "class",
                                   "function", "const", "let", "var", "new",
                                   "import", "from", "require", "print", "len"}:
                    continue
                for callee_id in self._name_index.get(callee_name, []):
                    if callee_id != caller_id:
                        self.graph.add_edge(caller_id, callee_id, "calls")


# ── module-level helpers ─────────────────────────────────────────────────────

_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    "dist", "build", ".next", ".mypy_cache", ".pytest_cache",
    ".tox", "target", "out",
}


def _scan_files(repo_path: Path, languages: Optional[list[str]] = None) -> list[Path]:
    """Walk ``repo_path`` and return source files matching ``LANG_EXT``.

    If ``languages`` is given, restrict to extensions whose language is in the
    list. Skips common vendor / build / cache directories.
    """
    if languages:
        wanted = {lang.lower() for lang in languages}
        exts = {ext for ext, lang in LANG_EXT.items() if lang in wanted}
    else:
        exts = set(LANG_EXT.keys())

    out: list[Path] = []
    for fp in sorted(repo_path.rglob("*")):
        if any(p in _SKIP_DIRS for p in fp.parts):
            continue
        if fp.is_file() and fp.suffix.lower() in exts:
            out.append(fp)
    return out


def build_call_graph(
    repo_path: str | Path,
    languages: Optional[list[str]] = None,
    output_path: Optional[str | Path] = None,
    files: Optional[Iterable[str | Path]] = None,
) -> dict:
    """Build a call graph for ``repo_path`` and optionally persist it.

    Parameters
    ----------
    repo_path:
        Path to the repository root. Used both for scanning (when ``files`` is
        omitted) and as the base for computing relative file paths.
    languages:
        Optional list of language names (``"python"``, ``"javascript"``...). When
        omitted, every language in ``LANG_EXT`` is included.
    output_path:
        Optional location to write ``call_graph.json``. If omitted, the graph
        is built but not persisted (callers may still consume the returned
        ``CallGraph``).
    files:
        Optional precomputed iterable of source files. When provided, the
        internal scanner is skipped - useful for sharing the indexer's
        ``scan_repository`` result.

    Returns
    -------
    dict with keys ``nodes``, ``edges``, ``files_scanned``, ``output_path``,
    and ``graph`` (the in-memory :class:`CallGraph`).
    """
    repo_path = Path(repo_path).resolve()
    file_list: list[Path]
    if files is not None:
        file_list = [Path(fp) for fp in files]
    else:
        file_list = _scan_files(repo_path, languages=languages)

    builder = CallGraphBuilder()
    graph = builder.build_from_files(file_list, repo_path)

    edge_count = sum(
        len(n.calls) + len(n.imports) + len(n.inherits_from)
        for n in graph.nodes.values()
    )

    saved_to: Optional[str] = None
    if output_path is not None:
        graph.save(output_path)
        saved_to = str(output_path)
        logger.info(
            "Call graph saved -> %s (%d nodes, %d edges)",
            saved_to, len(graph.nodes), edge_count,
        )

    return {
        "nodes": len(graph.nodes),
        "edges": edge_count,
        "files_scanned": len(file_list),
        "output_path": saved_to,
        "graph": graph,
    }


__all__ = [
    "LANG_EXT",
    "CallGraph",
    "CallGraphNode",
    "CallGraphBuilder",
    "build_call_graph",
]
