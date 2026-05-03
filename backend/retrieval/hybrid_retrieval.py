"""
Hybrid Embedding Retrieval
===========================
Combines semantic embeddings (text similarity) with structural embeddings 
(code structure/importance) for improved retrieval.

The hybrid approach:
1. Semantic: "all-MiniLM" embeddings for text similarity
2. Structural: GraphCodeBERT or graph metrics for code structure importance
3. Combined: Weighted blend of both at retrieval time

This gives better results for queries about central hubs (structural),
entry points, and utility functions across the codebase.
"""

import json
import numpy as np
from pathlib import Path
from typing import Optional
import logging

try:
    from indexing.graph_embeddings import HybridEmbeddingManager
    HYBRID_AVAILABLE = True
except ImportError:
    HYBRID_AVAILABLE = False
    logging.warning("Hybrid embeddings not fully configured")

logger = logging.getLogger("hybrid_retrieval")

# Weights for combining semantic and structural scores
SEMANTIC_WEIGHT = 0.65      # Text similarity
STRUCTURAL_WEIGHT = 0.35    # Code structure importance


class CallGraphLoader:
    """Load and cache the call graph from disk."""
    
    def __init__(self, chroma_path: str):
        self.chroma_path = Path(chroma_path)
        self.graph = None
        self._load_graph()
    
    def _load_graph(self) -> None:
        """Load call graph from JSON file if it exists."""
        graph_path = self.chroma_path / "call_graph.json"
        
        if not graph_path.exists():
            logger.info("No call graph found at %s (optional feature)", graph_path)
            return
        
        try:
            graph_data = json.loads(graph_path.read_text())
            # Convert back to node objects if needed
            self.graph = graph_data
            logger.info("Loaded call graph with %d nodes", len(self.graph.get("nodes", {})))
        except Exception as e:
            logger.warning("Failed to load call graph: %s", e)
    
    def get_node_metrics(self, file_path: str, func_name: str) -> dict:
        """Get structural metrics for a function."""
        if not self.graph:
            return {}
        
        node_id = f"{file_path}:{func_name}"
        node = self.graph.get("nodes", {}).get(node_id, {})
        
        return {
            "in_degree": node.get("in_degree", 0.0),
            "out_degree": node.get("out_degree", 0.0),
            "pagerank": node.get("pagerank", 0.0),
            "num_callers": len(node.get("callers", [])),
            "num_callees": len(node.get("calls", [])),
        }


class HybridRetrievalScorer:
    """Score chunks using both semantic and structural information."""
    
    def __init__(self, chroma_path: str, semantic_model=None):
        self.graph_loader = CallGraphLoader(chroma_path)
        self.semantic_model = semantic_model
    
    def compute_structural_score(self, file_path: str, func_name: str) -> float:
        """
        Compute a structural importance score (0.0 to 1.0).
        
        High scores for:
        - Functions with high PageRank (called frequently)
        - Functions with many callers (hubs)
        - Functions with specific patterns (entry points, etc.)
        """
        metrics = self.graph_loader.get_node_metrics(file_path, func_name)
        
        if not metrics:
            return 0.5  # Default neutral score
        
        # Combine metrics: PageRank + normalized in-degree
        pagerank = metrics.get("pagerank", 0.0)
        in_degree = metrics.get("in_degree", 0.0)
        
        # PageRank is already normalized, in_degree is also normalized
        structural_score = (pagerank + in_degree) / 2.0
        
        return min(structural_score, 1.0)
    
    def compute_hybrid_score(
        self,
        semantic_score: float,
        file_path: str,
        func_name: str,
    ) -> float:
        """
        Combine semantic and structural scores.
        
        Args:
            semantic_score: Similarity score from vector search (0 to 1)
            file_path: File path of the chunk
            func_name: Function name
        
        Returns:
            Combined score (0 to 1)
        """
        structural_score = self.compute_structural_score(file_path, func_name)
        
        hybrid_score = (
            SEMANTIC_WEIGHT * semantic_score +
            STRUCTURAL_WEIGHT * structural_score
        )
        
        return min(hybrid_score, 1.0)


def enhance_ranking_with_structure(
    candidates: list[dict],
    chroma_path: str,
) -> list[dict]:
    """
    Enhance existing candidate rankings with structural information.
    
    Takes candidates from semantic search and re-ranks using hybrid scores
    that combine text similarity with code structure importance.
    
    Args:
        candidates: List of dicts with 'metadata', 'similarity', etc.
        chroma_path: Path to ChromaDB where call_graph.json is stored
    
    Returns:
        Updated candidates with 'hybrid_score' added
    """
    scorer = HybridRetrievalScorer(chroma_path)
    
    for item in candidates:
        meta = item.get("metadata", {})
        file_path = meta.get("file", "")
        func_name = meta.get("function", "")
        semantic_score = item.get("similarity", 0.5)
        
        # Compute hybrid score
        item["hybrid_score"] = scorer.compute_hybrid_score(
            semantic_score,
            file_path,
            func_name,
        )
        
        # Store structural details for debugging/analysis
        item["structural_metrics"] = scorer.graph_loader.get_node_metrics(file_path, func_name)
    
    return candidates
