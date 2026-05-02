"""
Code Graph Builder
==================
Extracts function call relationships, dependencies, and builds a call graph.

The call graph stores:
- Nodes: functions/classes and their metadata
- Edges: call relationships, imports, inheritance
- Metrics: in-degree, out-degree, centrality (importance in the codebase)

This information is used to compute structural position embeddings that
capture where a function sits in the call hierarchy.
"""

import ast
import re
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field


@dataclass
class CallGraphNode:
    """A node in the call graph (function, class, etc.)."""
    name: str                          # function/class name
    file_path: str                     # relative file path
    start_line: int
    end_line: int
    node_type: str                     # "function" | "class" | "route"
    
    # Graph metrics
    callers: set[str] = field(default_factory=set)        # who calls this
    calls: set[str] = field(default_factory=set)          # what this calls
    imported_by: set[str] = field(default_factory=set)    # who imports this
    imports: set[str] = field(default_factory=set)        # what this imports
    inherits_from: set[str] = field(default_factory=set)  # parent classes
    inherited_by: set[str] = field(default_factory=set)   # child classes
    
    # Computed centrality metrics (0.0 to 1.0)
    in_degree: float = 0.0             # normalized: how many things call this
    out_degree: float = 0.0            # normalized: how many things this calls
    pagerank: float = 0.0              # PageRank centrality
    
    def get_identifier(self) -> str:
        """Unique identifier for this node: file:name."""
        return f"{self.file_path}:{self.name}"


@dataclass
class CallGraph:
    """A directed graph of code entities and their relationships."""
    nodes: dict[str, CallGraphNode] = field(default_factory=dict)
    
    def add_node(self, node: CallGraphNode) -> None:
        """Add or update a node in the graph."""
        self.nodes[node.get_identifier()] = node
    
    def get_node(self, file_path: str, name: str) -> Optional[CallGraphNode]:
        """Get a node by file and name."""
        identifier = f"{file_path}:{name}"
        return self.nodes.get(identifier)
    
    def add_edge(self, caller_id: str, callee_id: str, edge_type: str = "calls") -> None:
        """
        Add a directed edge from caller to callee.
        edge_type: "calls" | "imports" | "inherits_from"
        """
        if caller_id not in self.nodes or callee_id not in self.nodes:
            return
        
        caller = self.nodes[caller_id]
        callee = self.nodes[callee_id]
        
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
        """Normalize in-degree and out-degree (0.0 to 1.0)."""
        if not self.nodes:
            return
        
        max_in = max((len(n.callers) for n in self.nodes.values()), default=1)
        max_out = max((len(n.calls) for n in self.nodes.values()), default=1)
        
        for node in self.nodes.values():
            node.in_degree = len(node.callers) / max(max_in, 1)
            node.out_degree = len(node.calls) / max(max_out, 1)
    
    def compute_pagerank(self, iterations: int = 10, damping: float = 0.85) -> None:
        """
        Simplified PageRank: measure node importance by call frequency.
        Nodes called by many other functions get higher rank.
        """
        if not self.nodes:
            return
        
        # Initialize ranks equally
        ranks = {nid: 1.0 / len(self.nodes) for nid in self.nodes}
        
        for _ in range(iterations):
            new_ranks = {}
            for nid, node in self.nodes.items():
                rank = (1.0 - damping) / len(self.nodes)
                
                # Distribute rank from all callers
                for caller_id in node.callers:
                    caller = self.nodes[caller_id]
                    out_degree = max(len(caller.calls), 1)
                    rank += damping * (ranks[caller_id] / out_degree)
                
                new_ranks[nid] = rank
            
            ranks = new_ranks
        
        # Normalize to 0-1 range
        max_rank = max(ranks.values(), default=1)
        for node in self.nodes.values():
            node.pagerank = min(ranks[node.get_identifier()] / max_rank, 1.0)


class CallGraphBuilder:
    """Build a call graph from Python source code."""
    
    def __init__(self):
        self.graph = CallGraph()
        self.file_ast_cache = {}  # path -> AST for quick lookup
    
    def build_from_files(self, file_paths: list[Path], repo_root: Path) -> CallGraph:
        """
        Parse multiple files and build the call graph.
        
        Args:
            file_paths: List of Python file paths to analyze
            repo_root: Repository root for computing relative paths
        
        Returns:
            CallGraph with nodes and edges populated
        """
        # Phase 1: Extract all functions/classes as nodes
        for fpath in file_paths:
            if not fpath.exists():
                continue
            try:
                rel_path = str(fpath.relative_to(repo_root))
                source = fpath.read_text(encoding="utf-8", errors="replace")
                self._extract_definitions(source, rel_path, fpath)
            except Exception as e:
                print(f"[graph_builder] Error parsing {fpath}: {e}")
        
        # Phase 2: Extract relationships (calls, imports, inheritance)
        for fpath in file_paths:
            if not fpath.exists():
                continue
            try:
                rel_path = str(fpath.relative_to(repo_root))
                source = fpath.read_text(encoding="utf-8", errors="replace")
                self._extract_relationships(source, rel_path, fpath)
            except Exception as e:
                print(f"[graph_builder] Error extracting relationships from {fpath}: {e}")
        
        # Phase 3: Compute graph metrics
        self.graph.compute_degrees()
        self.graph.compute_pagerank()
        
        return self.graph
    
    def _extract_definitions(self, source: str, rel_path: str, fpath: Path) -> None:
        """Extract function and class definitions from source code."""
        try:
            tree = ast.parse(source)
            self.file_ast_cache[rel_path] = tree
        except SyntaxError:
            return
        
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                func_node = CallGraphNode(
                    name=node.name,
                    file_path=rel_path,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    node_type="function",
                )
                self.graph.add_node(func_node)
            
            elif isinstance(node, ast.ClassDef):
                class_node = CallGraphNode(
                    name=node.name,
                    file_path=rel_path,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    node_type="class",
                )
                self.graph.add_node(class_node)
                
                # Handle inheritance
                for base in node.bases:
                    base_name = self._ast_to_name(base)
                    if base_name:
                        class_node.inherits_from.add(base_name)
    
    def _extract_relationships(self, source: str, rel_path: str, fpath: Path) -> None:
        """Extract calls, imports, and other relationships."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return
        
        # Extract imports
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    # For imports, we track the module name
                    pass
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    pass
        
        # Extract function calls within each function
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                func_id = f"{rel_path}:{node.name}"
                caller = self.graph.get_node(rel_path, node.name)
                if not caller:
                    continue
                
                # Find all function calls inside this function
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        called_name = self._ast_to_name(child.func)
                        if called_name:
                            # Try to resolve to a known function in the graph
                            # First: check same file
                            callee_id = f"{rel_path}:{called_name}"
                            if callee_id in self.graph.nodes:
                                self.graph.add_edge(func_id, callee_id, "calls")
    
    def _ast_to_name(self, node: ast.expr) -> Optional[str]:
        """Convert an AST node to a name string."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            value = self._ast_to_name(node.value)
            if value:
                return f"{value}.{node.attr}"
        return None
    
    def get_structural_features(self, file_path: str, func_name: str) -> dict:
        """
        Get structural features for a function to be used in embedding.
        
        Returns:
            Dict with keys: in_degree, out_degree, pagerank, is_entry_point, depth_in_call_tree
        """
        node = self.graph.get_node(file_path, func_name)
        if not node:
            return {
                "in_degree": 0.0,
                "out_degree": 0.0,
                "pagerank": 0.0,
                "is_entry_point": False,
                "depth_in_call_tree": 0,
                "num_callers": 0,
                "num_callees": 0,
            }
        
        # Entry point: called from many places or has low out-degree
        is_entry = len(node.callers) > 5 or (len(node.calls) == 0 and len(node.callers) > 0)
        
        # Depth: how deep in call tree (BFS distance to leaf)
        depth = self._compute_depth(node.get_identifier())
        
        return {
            "in_degree": node.in_degree,
            "out_degree": node.out_degree,
            "pagerank": node.pagerank,
            "is_entry_point": is_entry,
            "depth_in_call_tree": depth,
            "num_callers": len(node.callers),
            "num_callees": len(node.calls),
        }
    
    def _compute_depth(self, node_id: str, memo: Optional[dict] = None, visited: Optional[set] = None) -> int:
        """Compute depth in call tree (distance to leaf node)."""
        if memo is None:
            memo = {}
            visited = set()
        
        if node_id in memo:
            return memo[node_id]
        
        if node_id in visited:
            return 0  # Cycle detected
        
        visited.add(node_id)
        node = self.graph.nodes.get(node_id)
        if not node or not node.calls:
            return 0
        
        max_depth = max(
            (self._compute_depth(callee_id, memo, visited) for callee_id in node.calls),
            default=0
        )
        
        memo[node_id] = max_depth + 1
        return memo[node_id]
