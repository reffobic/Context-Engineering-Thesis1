#!/usr/bin/env python3
"""
Quick Start: Graph-Based Embeddings
===================================

This script demonstrates the hybrid embedding system that combines
semantic text similarity with code structure importance.

Run: python backend/test_graph_embeddings.py
"""

import json
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

def test_graph_building():
    """Test building a call graph from Python code."""
    print("\n" + "="*70)
    print("TEST 1: Building Call Graph")
    print("="*70)
    
    from indexing.graph_builder import CallGraphBuilder
    
    # Create test files
    test_repo = Path("/tmp/test_repo_graph")
    test_repo.mkdir(exist_ok=True)
    
    # Create a simple Python file with functions and calls
    (test_repo / "demo.py").write_text("""
def entry_point():
    '''Called from many places'''
    helper1()
    helper2()
    orchestrate()

def helper1():
    '''Simple utility'''
    pass

def helper2():
    '''Another utility'''
    pass

def orchestrate():
    '''Central hub that calls many things'''
    core_logic()
    cleanup()

def core_logic():
    pass

def cleanup():
    pass
""")
    
    # Build the graph
    builder = CallGraphBuilder()
    py_files = list(test_repo.glob("*.py"))
    graph = builder.build_from_files(py_files, test_repo)
    
    print(f"\n✓ Built call graph with {len(graph.nodes)} nodes")
    
    # Display metrics
    print("\nFunction Metrics:")
    print("-" * 70)
    for node_id, node in sorted(graph.nodes.items()):
        print(f"  {node.name:20} | "
              f"Callers: {len(node.callers):2} | "
              f"Calls: {len(node.calls):2} | "
              f"PageRank: {node.pagerank:.3f}")
    
    return graph


def test_hybrid_retrieval():
    """Test hybrid retrieval with semantic + structural scoring."""
    print("\n" + "="*70)
    print("TEST 2: Hybrid Retrieval Scoring")
    print("="*70)
    
    from retrieval.hybrid_retrieval import HybridRetrievalScorer
    
    # Create scorer with a test graph path
    scorer = HybridRetrievalScorer("./chroma_db")
    
    # Simulate some candidates
    candidates = [
        {
            "metadata": {
                "file": "agent.py",
                "function": "run_agent",
            },
            "similarity": 0.82,  # semantic similarity to "orchestration"
        },
        {
            "metadata": {
                "file": "utils.py",
                "function": "format_output",
            },
            "similarity": 0.89,  # higher semantic similarity (name match)
        },
    ]
    
    print(f"\nCandidates before structural enhancement:")
    for i, c in enumerate(candidates, 1):
        print(f"  [{i}] {c['metadata']['function']:20} | "
              f"Semantic: {c['similarity']:.2f}")
    
    # Compute hybrid scores
    print(f"\nComputing structural importance scores...")
    for item in candidates:
        meta = item["metadata"]
        struct_score = scorer.compute_structural_score(meta["file"], meta["function"])
        item["structural_score"] = struct_score
        item["hybrid_score"] = scorer.compute_hybrid_score(
            item["similarity"],
            meta["file"],
            meta["function"],
        )
    
    print(f"\nCandidates after structural enhancement:")
    for i, c in enumerate(candidates, 1):
        print(f"  [{i}] {c['metadata']['function']:20} | "
              f"Semantic: {c['similarity']:.2f} | "
              f"Structural: {c.get('structural_score', 0):.2f} | "
              f"Hybrid: {c.get('hybrid_score', 0):.2f}")
    
    # Re-rank by hybrid score
    candidates_sorted = sorted(candidates, key=lambda x: x.get("hybrid_score", 0), reverse=True)
    print(f"\nRe-ranked by hybrid score:")
    for i, c in enumerate(candidates_sorted, 1):
        print(f"  [{i}] {c['metadata']['function']} ({c.get('hybrid_score', 0):.2f})")


def test_structural_embeddings():
    """Test structural embedding generation."""
    print("\n" + "="*70)
    print("TEST 3: Structural Embeddings")
    print("="*70)
    
    from indexing.graph_embeddings import StructuralEmbeddingGenerator
    from indexing.graph_builder import CallGraphBuilder, CallGraphNode
    
    # Create a simple graph for testing
    from indexing.graph_builder import CallGraph
    
    graph = CallGraph()
    
    # Add some nodes
    hub = CallGraphNode("orchestrate", "main.py", 1, 10, "function")
    leaf = CallGraphNode("helper", "main.py", 20, 25, "function")
    
    # Simulate call relationships
    hub.calls.add("main.py:helper")
    leaf.callers.add("main.py:orchestrate")
    hub.in_degree = 0.8
    hub.out_degree = 0.5
    hub.pagerank = 0.9
    
    leaf.in_degree = 0.2
    leaf.out_degree = 0.0
    leaf.pagerank = 0.1
    
    graph.add_node(hub)
    graph.add_node(leaf)
    
    # Generate structural embeddings
    generator = StructuralEmbeddingGenerator(use_graphcodebert=False)
    generator.set_call_graph(graph)
    
    print(f"\nGenerating structural embeddings (64-dim)...")
    
    hub_emb = generator.embed_chunk("def orchestrate(): ...", "main.py", "orchestrate")
    leaf_emb = generator.embed_chunk("def helper(): ...", "main.py", "helper")
    
    print(f"  Hub function embedding: {hub_emb[:5]}... (first 5 dims)")
    print(f"  Leaf function embedding: {leaf_emb[:5]}... (first 5 dims)")
    
    # Compare embeddings
    import numpy as np
    similarity = np.dot(hub_emb, leaf_emb) / (np.linalg.norm(hub_emb) * np.linalg.norm(leaf_emb) + 1e-8)
    print(f"  Embedding similarity (hub vs leaf): {similarity:.3f}")
    print(f"    (Should be moderate since they're different types)")


def test_integration():
    """Test full integration (if indexing has been run)."""
    print("\n" + "="*70)
    print("TEST 4: Full Integration (if indexed)")
    print("="*70)
    
    from pathlib import Path
    import json
    
    graph_path = Path("./chroma_db/call_graph.json")
    
    if not graph_path.exists():
        print("  ⚠ No call graph found (need to index first)")
        print("  Run: python -m indexing.code_indexer index ./backend")
        return
    
    graph_data = json.loads(graph_path.read_text())
    nodes = graph_data.get("nodes", {})
    
    print(f"\n✓ Loaded call graph with {len(nodes)} nodes")
    
    # Show top hubs (by PageRank)
    sorted_nodes = sorted(
        nodes.items(),
        key=lambda x: x[1].get("pagerank", 0),
        reverse=True
    )
    
    print(f"\nTop 5 Hub Functions (by PageRank):")
    for node_id, node in sorted_nodes[:5]:
        print(f"  {node['name']:30} | "
              f"PageRank: {node.get('pagerank', 0):.3f} | "
              f"Callers: {len(node.get('callers', []))} | "
              f"Calls: {len(node.get('calls', []))}")


if __name__ == "__main__":
    print("\n" + "="*70)
    print("Graph-Based Embeddings Quick Start Tests")
    print("="*70)
    
    try:
        test_graph_building()
    except Exception as e:
        print(f"  ❌ Graph building test failed: {e}")
        import traceback
        traceback.print_exc()
    
    try:
        test_hybrid_retrieval()
    except Exception as e:
        print(f"  ❌ Hybrid retrieval test failed: {e}")
        import traceback
        traceback.print_exc()
    
    try:
        test_structural_embeddings()
    except Exception as e:
        print(f"  ❌ Structural embeddings test failed: {e}")
        import traceback
        traceback.print_exc()
    
    try:
        test_integration()
    except Exception as e:
        print(f"  ❌ Integration test failed: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "="*70)
    print("Tests complete!")
    print("="*70 + "\n")
