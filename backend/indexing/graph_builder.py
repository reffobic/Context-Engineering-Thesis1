"""
graph_builder.py — extend call graph to non-Python languages via regex AST.
Replace _extract_definitions and _extract_relationships with multi-language versions.
"""

import re
import ast
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# ── language dispatch table ──────────────────────────────────────────────────

LANG_EXT = {
    ".py":   "python",
    ".js":   "javascript", ".jsx": "javascript",
    ".ts":   "typescript", ".tsx": "typescript",
    ".java": "java",
    ".go":   "go",
    ".c":    "c", ".h": "c",
    ".cpp":  "cpp", ".cc": "cpp", ".hpp": "cpp",
}

# ── regex patterns per language ──────────────────────────────────────────────

# Each entry: (chunk_type, compiled_regex)
# Group 1 must capture the function/class name.
FUNC_PATTERNS: dict[str, list[tuple[str, re.Pattern]]] = {
    "javascript": [
        ("function", re.compile(r'\bfunction\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(')),
        ("function", re.compile(r'\bconst\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?\(')),
        ("function", re.compile(r'\bconst\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?\w+\s*=>')),
        ("class",    re.compile(r'\bclass\s+([A-Za-z_$][A-Za-z0-9_$]*)')),
    ],
    "typescript": [  # same as JS plus typed variants
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
            re.MULTILINE
        )),
    ],
    "cpp": [
        ("function", re.compile(
            r'^[A-Za-z_][A-Za-z0-9_:<>\s\*&]+\s+([A-Za-z_][A-Za-z0-9_:]*)\s*\([^;]*\)\s*(?:const\s*)?\{',
            re.MULTILINE
        )),
        ("class", re.compile(r'\bclass\s+([A-Za-z_][A-Za-z0-9_]*)')),
    ],
}

# Call-site patterns: find what a function CALLS
# Group 1 = callee name
CALL_PATTERNS: dict[str, list[re.Pattern]] = {
    "javascript": [re.compile(r'\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\(')],
    "typescript": [re.compile(r'\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\(')],
    "java":       [re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*\(')],
    "go":         [re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*\(')],
    "c":          [re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*\(')],
    "cpp":        [re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*\(')],
    "python":     [re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*\(')],
}

# Import patterns: group 1 = module name
IMPORT_PATTERNS: dict[str, list[re.Pattern]] = {
    "javascript": [
        re.compile(r'\bimport\s+.*?\bfrom\s+["\']([^"\']+)["\']'),
        re.compile(r'\brequire\s*\(\s*["\']([^"\']+)["\']\s*\)'),
    ],
    "typescript": [
        re.compile(r'\bimport\s+.*?\bfrom\s+["\']([^"\']+)["\']'),
    ],
    "java": [
        re.compile(r'\bimport\s+([\w.]+);'),
    ],
    "go": [
        re.compile(r'"([^"]+)"'),  # inside import blocks
    ],
    "c":   [re.compile(r'#include\s+[<"]([^>"]+)[>"]')],
    "cpp": [re.compile(r'#include\s+[<"]([^>"]+)[>"]')],
    "python": [
        re.compile(r'^\s*import\s+([\w.]+)', re.MULTILINE),
        re.compile(r'^\s*from\s+([\w.]+)\s+import', re.MULTILINE),
    ],
}

# ── dataclasses (unchanged from original) ────────────────────────────────────

@dataclass
class CallGraphNode:
    name: str
    file_path: str
    start_line: int
    end_line: int
    node_type: str
    language: str = "unknown"          # ← NEW

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
                     if k not in ("callers","calls","imported_by","imports",
                                  "inherits_from","inherited_by")})
        for key in ("callers","calls","imported_by","imports",
                    "inherits_from","inherited_by"):
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
        edges = []
        for nid, node in self.nodes.items():
            for callee in node.calls:
                edges.append({"source": nid, "target": callee, "type": "calls"})
            for imp in node.imports:
                edges.append({"source": nid, "target": imp,    "type": "imports"})
            for base in node.inherits_from:
                edges.append({"source": nid, "target": base,   "type": "inherits_from"})
        return {"nodes": nodes, "edges": edges}

    def save(self, path: str) -> None:
        import json
        Path(path).write_text(json.dumps(self.to_serializable(), indent=2))

    @classmethod
    def load(cls, path: str) -> "CallGraph":
        import json
        data = json.loads(Path(path).read_text())
        g = cls()
        for k, v in data["nodes"].items():
            g.nodes[k] = CallGraphNode.from_dict(v)
        return g


# ── builder ──────────────────────────────────────────────────────────────────

class CallGraphBuilder:

    def __init__(self):
        self.graph = CallGraph()
        # name → set of identifiers (for cross-file call resolution)
        self._name_index: dict[str, set] = {}

    def build_from_files(self, file_paths: list, repo_root: str) -> CallGraph:
        root = Path(repo_root)

        # Phase 1 – definitions
        for fp in file_paths:
            lang = LANG_EXT.get(Path(fp).suffix.lower())
            if not lang:
                continue
            rel = str(Path(fp).relative_to(root))
            source = Path(fp).read_text(encoding="utf-8", errors="replace")
            if lang == "python":
                self._extract_python_definitions(source, rel)
            else:
                self._extract_generic_definitions(source, rel, lang)

        # Build name → identifier index for relationship resolution
        for nid, node in self.graph.nodes.items():
            self._name_index.setdefault(node.name, set()).add(nid)

        # Phase 2 – relationships
        for fp in file_paths:
            lang = LANG_EXT.get(Path(fp).suffix.lower())
            if not lang:
                continue
            rel = str(Path(fp).relative_to(root))
            source = Path(fp).read_text(encoding="utf-8", errors="replace")
            if lang == "python":
                self._extract_python_relationships(source, rel)
            else:
                self._extract_generic_relationships(source, rel, lang)

        # Phase 3 – metrics
        self.graph.compute_degrees()
        self.graph.compute_pagerank()
        return self.graph

    # ── Python (AST) ─────────────────────────────────────────────────────────

    def _extract_python_definitions(self, source: str, rel_path: str) -> None:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return
        lines = source.splitlines()
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

        # Map line → enclosing function identifier
        func_ranges: list[tuple[int,int,str]] = []
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
        # Collect all (line_no, name, type) then pair with next match for end_line
        hits: list[tuple[int,str,str]] = []
        for ntype, pat in patterns:
            for m in pat.finditer(source):
                lineno = source[:m.start()].count("\n") + 1
                hits.append((lineno, m.group(1), ntype))

        hits.sort(key=lambda x: x[0])
        for i, (lineno, name, ntype) in enumerate(hits):
            end = hits[i+1][0] - 1 if i + 1 < len(hits) else len(lines)
            cgn = CallGraphNode(
                name=name, file_path=rel_path,
                start_line=lineno, end_line=end,
                node_type=ntype, language=lang,
            )
            self.graph.add_node(cgn)

    def _extract_generic_relationships(self, source: str, rel_path: str, lang: str) -> None:
        call_pats  = CALL_PATTERNS.get(lang, [])
        lines      = source.splitlines()

        # Build line → enclosing node map from already-extracted nodes
        file_nodes = [
            n for n in self.graph.nodes.values() if n.file_path == rel_path
        ]
        file_nodes.sort(key=lambda n: n.start_line)

        def enclosing(lineno: int) -> Optional[str]:
            best = None
            for n in file_nodes:
                if n.start_line <= lineno <= n.end_line:
                    best = n.get_identifier()
            return best

        for pat in call_pats:
            for m in pat.finditer(source):
                lineno  = source[:m.start()].count("\n") + 1
                callee_name = m.group(1)
                caller_id   = enclosing(lineno)
                if not caller_id:
                    continue
                # Skip language keywords / builtins
                if callee_name in {"if","for","while","return","class",
                                   "function","const","let","var","new",
                                   "import","from","require","print","len"}:
                    continue
                for callee_id in self._name_index.get(callee_name, []):
                    if callee_id != caller_id:
                        self.graph.add_edge(caller_id, callee_id, "calls")