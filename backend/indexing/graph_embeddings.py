"""
Graph-based Structural Embeddings
==================================
Computes structural position embeddings using code structure information.

Uses a second embedding model (GraphCodeBERT) that understands code relationships,
or derives embeddings from graph metrics (in-degree, out-degree, PageRank, etc.).

The structural embedding captures:
- Function importance in the call graph
- Where a function sits in the call hierarchy
- Relationships and dependencies

These are combined with semantic embeddings at query time for hybrid retrieval.
"""

import numpy as np
from pathlib import Path
from typing import Optional
import json
import logging

try:
    from transformers import AutoTokenizer, AutoModel
    TRANSFORMERS_AVAILABLE = True
except ImportError:
    TRANSFORMERS_AVAILABLE = False
    logging.warning("transformers not installed. Run: pip install transformers torch")

from .graph_builder import CallGraph, CallGraphBuilder

logger = logging.getLogger("graph_embeddings")

# GraphCodeBERT model (small version for efficiency)
GRAPHCODEBERT_MODEL = "microsoft/graphcodebert-base"

# Structural features dimension
STRUCTURAL_DIM = 64


class StructuralEmbeddingGenerator:
    """
    Generates structural position embeddings for code chunks.
    
    Two strategies:
    1. Use GraphCodeBERT (if available) to understand code structure
    2. Derive embeddings from graph metrics (in-degree, out-degree, PageRank, etc.)
    """
    
    def __init__(self, use_graphcodebert: bool = False):
        """
        Initialize the structural embedding generator.
        
        Args:
            use_graphcodebert: If True, use GraphCodeBERT model. Otherwise use graph metrics.
        """
        self.use_graphcodebert = use_graphcodebert and TRANSFORMERS_AVAILABLE
        self.tokenizer = None
        self.model = None
        self.graph: Optional[CallGraph] = None
        
        if self.use_graphcodebert:
            try:
                logger.info(f"Loading {GRAPHCODEBERT_MODEL}...")
                self.tokenizer = AutoTokenizer.from_pretrained(GRAPHCODEBERT_MODEL)
                self.model = AutoModel.from_pretrained(GRAPHCODEBERT_MODEL)
                self.model.eval()  # Set to evaluation mode
                logger.info("GraphCodeBERT loaded successfully")
            except Exception as e:
                logger.warning(f"Failed to load GraphCodeBERT: {e}. Falling back to graph metrics.")
                self.use_graphcodebert = False
    
    def set_call_graph(self, graph: CallGraph) -> None:
        """Set the call graph to use for structural metrics."""
        self.graph = graph
    
    def embed_chunk(self, code: str, file_path: str, func_name: str) -> np.ndarray:
        """
        Generate a structural embedding for a code chunk.
        
        Args:
            code: The code snippet
            file_path: Relative path to the file
            func_name: Name of the function/class
        
        Returns:
            Embedding vector of shape (STRUCTURAL_DIM,) or combined dimension if using both
        """
        if self.use_graphcodebert and self.model is not None:
            return self._embed_with_graphcodebert(code, file_path, func_name)
        else:
            return self._embed_with_graph_metrics(file_path, func_name)
    
    def _embed_with_graphcodebert(self, code: str, file_path: str, func_name: str) -> np.ndarray:
        """
        Use GraphCodeBERT to encode structural information from the code.
        GraphCodeBERT uses program dependence graphs to understand structure.
        """
        try:
            # Truncate very long code to avoid memory issues
            code = code[:512]
            
            inputs = self.tokenizer(code, return_tensors="pt", truncation=True, max_length=512)
            
            with __import__('torch').no_grad():
                outputs = self.model(**inputs)
                # Use the [CLS] token embedding as the sequence representation
                embedding = outputs.last_hidden_state[:, 0, :].cpu().numpy()[0]
            
            # Add graph metrics as additional features
            if self.graph:
                metrics = self._get_graph_metrics(file_path, func_name)
                metrics_features = self._metrics_to_vector(metrics)
                # Combine: GraphCodeBERT embedding + graph metrics
                embedding = np.concatenate([embedding[:STRUCTURAL_DIM-len(metrics_features)], metrics_features])
            
            return embedding.astype(np.float32)
        
        except Exception as e:
            logger.warning(f"GraphCodeBERT embedding failed: {e}. Using graph metrics.")
            return self._embed_with_graph_metrics(file_path, func_name)
    
    def _embed_with_graph_metrics(self, file_path: str, func_name: str) -> np.ndarray:
        """
        Create embedding from graph metrics.
        
        Features:
        - in_degree, out_degree, pagerank (normalized)
        - is_entry_point (binary)
        - depth_in_call_tree (normalized)
        - call graph position in various ways
        """
        embedding = np.zeros(STRUCTURAL_DIM, dtype=np.float32)
        
        if not self.graph:
            return embedding
        
        metrics = self._get_graph_metrics(file_path, func_name)
        
        # Map metrics to embedding dimensions
        idx = 0
        
        # Core graph metrics (6 values)
        embedding[idx] = metrics.get("in_degree", 0.0)
        idx += 1
        embedding[idx] = metrics.get("out_degree", 0.0)
        idx += 1
        embedding[idx] = metrics.get("pagerank", 0.0)
        idx += 1
        embedding[idx] = float(metrics.get("is_entry_point", False))
        idx += 1
        embedding[idx] = min(metrics.get("depth_in_call_tree", 0) / 10.0, 1.0)
        idx += 1
        embedding[idx] = min(metrics.get("num_callers", 0) / 20.0, 1.0)
        idx += 1
        
        # Derived features (rest of dimensions)
        # Connectivity: how connected is this node?
        total_connections = metrics.get("num_callers", 0) + metrics.get("num_callees", 0)
        embedding[idx] = min(total_connections / 30.0, 1.0)
        idx += 1
        
        # Fan-out ratio: calls vs callers (utility function vs hub)
        num_callers = metrics.get("num_callers", 0)
        num_callees = metrics.get("num_callees", 0)
        if num_callers + num_callees > 0:
            embedding[idx] = num_callees / (num_callers + num_callees)
        idx += 1
        
        # Repeat/fill remaining dimensions with patterns or zeros
        while idx < STRUCTURAL_DIM:
            embedding[idx] = 0.0
            idx += 1
        
        return embedding
    
    def _get_graph_metrics(self, file_path: str, func_name: str) -> dict:
        """Get structural metrics for a function from the call graph."""
        if not self.graph:
            return {}
        
        node = self.graph.get_node(file_path, func_name)
        if not node:
            return {}
        
        return {
            "in_degree": node.in_degree,
            "out_degree": node.out_degree,
            "pagerank": node.pagerank,
            "is_entry_point": len(node.callers) > 5 or (len(node.calls) == 0 and len(node.callers) > 0),
            "depth_in_call_tree": self._compute_depth(node.get_identifier()),
            "num_callers": len(node.callers),
            "num_callees": len(node.calls),
        }
    
    def _compute_depth(self, start_node_id: str, memo: Optional[dict] = None, visited: Optional[set] = None) -> int:
        """
        Compute depth in call tree iteratively to avoid RecursionError on cyclic graphs.

        Uses an explicit post-order DFS stack. A node's depth is 0 if it has no
        callees (or all callees are part of a cycle back to an ancestor).
        Cycles are broken by tracking nodes that are 'on the current path'.

        The `memo` and `visited` parameters are accepted for API compatibility but
        only `memo` is used (as a shared results cache across multiple calls).
        """
        if not self.graph:
            return 0

        if memo is None:
            memo = {}

        if start_node_id in memo:
            return memo[start_node_id]

        # Iterative post-order DFS.
        # Stack frames: [node_id, child_iterator, running_max_child_depth]
        # on_stack tracks the active DFS path for cycle detection (back-edges).
        on_stack: set = set()
        stack: list = []

        def _push(nid: str) -> None:
            node = self.graph.nodes.get(nid)
            children = list(node.calls) if (node and node.calls) else []
            stack.append([nid, iter(children), 0])
            on_stack.add(nid)

        _push(start_node_id)

        while stack:
            frame = stack[-1]
            nid, children_iter, _ = frame

            if nid in memo:
                # Resolved by a nested push — pop and propagate upward
                stack.pop()
                on_stack.discard(nid)
                if stack:
                    stack[-1][2] = max(stack[-1][2], memo[nid] + 1)
                continue

            try:
                child_id = next(children_iter)
                if child_id in on_stack:
                    # Back-edge → cycle; skip to avoid infinite loop
                    continue
                if child_id in memo:
                    # Already computed; propagate without pushing
                    frame[2] = max(frame[2], memo[child_id] + 1)
                else:
                    _push(child_id)
            except StopIteration:
                # All children visited — finalise this node
                memo[nid] = frame[2]
                stack.pop()
                on_stack.discard(nid)
                if stack:
                    stack[-1][2] = max(stack[-1][2], memo[nid] + 1)

        return memo.get(start_node_id, 0)
    
    def _metrics_to_vector(self, metrics: dict) -> np.ndarray:
        """Convert metrics dict to a short feature vector."""
        features = [
            metrics.get("in_degree", 0.0),
            metrics.get("out_degree", 0.0),
            metrics.get("pagerank", 0.0),
            float(metrics.get("is_entry_point", False)),
        ]
        return np.array(features, dtype=np.float32)


class HybridEmbeddingManager:
    """
    Manages both semantic and structural embeddings.
    Combines them at retrieval time using weighted averaging.
    """
    
    # How to combine semantic and structural embeddings
    SEMANTIC_WEIGHT = 0.75  # Semantic text similarity
    STRUCTURAL_WEIGHT = 0.25  # Structural importance
    
    def __init__(
        self,
        semantic_model=None,
        use_graphcodebert: bool = False,
    ):
        """
        Initialize the hybrid embedding manager.
        
        Args:
            semantic_model: Pre-initialized sentence transformer model
            use_graphcodebert: Whether to use GraphCodeBERT for structural embeddings
        """
        self.semantic_model = semantic_model
        self.structural_generator = StructuralEmbeddingGenerator(use_graphcodebert=use_graphcodebert)
    
    def set_call_graph(self, graph: CallGraph) -> None:
        """Set the call graph for structural metrics."""
        self.structural_generator.set_call_graph(graph)
    
    def embed_query(self, query: str) -> dict:
        """
        Generate both semantic and structural embeddings for a query.
        
        Returns:
            Dict with 'semantic' and 'structural' embedding vectors
        """
        semantic_emb = None
        if self.semantic_model:
            semantic_emb = self.semantic_model.encode([query])[0]
        
        # For query, structural embedding is a simple "query importance" score
        # Not a full embedding, but rather query characteristics
        structural_features = self._query_structural_features(query)
        
        return {
            "semantic": semantic_emb,
            "semantic_embedding_str": None,
            "structural_features": structural_features,
        }
    
    def _project_structural(self, structural_emb: np.ndarray, target_dim: int) -> np.ndarray:
        """
        Project a structural embedding (e.g. 64-d) up to target_dim (e.g. 384-d)
        using a deterministic random projection so the weighted sum is shape-compatible.
        The projection matrix is fixed (seed=42) so results are reproducible.
        """
        src_dim = structural_emb.shape[0]
        if src_dim == target_dim:
            return structural_emb
        rng = np.random.default_rng(seed=42)
        # Projection matrix: (src_dim, target_dim), columns normalised
        proj = rng.standard_normal((src_dim, target_dim)).astype(np.float32)
        proj /= np.linalg.norm(proj, axis=0, keepdims=True) + 1e-8
        return structural_emb @ proj  # (target_dim,)

    def embed_code(self, code: str, file_path: str, func_name: str) -> dict:
        """
        Generate both semantic and structural embeddings for code.
        
        Returns:
            Dict with 'semantic', 'structural', and 'combined' embeddings
        """
        semantic_emb = None
        if self.semantic_model:
            semantic_emb = self.semantic_model.encode([code])[0]
        
        structural_emb = self.structural_generator.embed_chunk(code, file_path, func_name)
        
        # Combine: weighted average of normalized embeddings
        combined = None
        if semantic_emb is not None and structural_emb is not None:
            # Project structural embedding to the same dimension as semantic
            # (e.g. STRUCTURAL_DIM=64 → 384 for all-MiniLM-L6-v2)
            structural_projected = self._project_structural(structural_emb, semantic_emb.shape[0])

            # Normalize both to unit length
            semantic_normalized    = semantic_emb        / (np.linalg.norm(semantic_emb)        + 1e-8)
            structural_normalized  = structural_projected / (np.linalg.norm(structural_projected) + 1e-8)
            
            # Weighted combination
            combined = (
                self.SEMANTIC_WEIGHT * semantic_normalized +
                self.STRUCTURAL_WEIGHT * structural_normalized
            )
            combined = combined / (np.linalg.norm(combined) + 1e-8)
        
        return {
            "semantic": semantic_emb,
            "structural": structural_emb,   # kept at original dim for introspection
            "combined": combined,
        }
    
    def _query_structural_features(self, query: str) -> dict:
        """Extract structural features from query text."""
        # Heuristics: is this a query about entry points, high-level functions, etc.?
        query_lower = query.lower()
        
        is_entry_point_query = any(word in query_lower for word in ["entry", "main", "start", "init", "route", "handler"])
        is_leaf_query = any(word in query_lower for word in ["utility", "helper", "parse", "format", "convert"])
        is_central_query = any(word in query_lower for word in ["core", "central", "hub", "orchestrate"])
        
        return {
            "is_entry_point_query": is_entry_point_query,
            "is_leaf_query": is_leaf_query,
            "is_central_query": is_central_query,
        }