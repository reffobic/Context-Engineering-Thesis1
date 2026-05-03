"""
Graph visualization tools for the pipeline
"""

from pathlib import Path
import json
import logging

logger = logging.getLogger("graph_tools")


def visualize_graph(output_format: str = "html") -> dict:
    """
    Generate graph visualization in the specified format.
    
    Args:
        output_format: One of 'html', 'json', 'dot', 'ascii'
    
    Returns:
        Status dict with file path and format info
    """
    try:
        from indexing.graph_builder import CallGraphBuilder
        
        # Try to load existing graph or rebuild
        chroma_path = Path(__file__).resolve().parent.parent / "chroma_db"
        graph_file = chroma_path / "call_graph.json"
        
        if graph_file.exists():
            logger.info("Loading graph from: %s", graph_file)
            graph_data = json.loads(graph_file.read_text())
            nodes = len(graph_data.get("nodes", {}))
            edges = len(graph_data.get("edges", []))
        else:
            logger.warning("No call graph found at %s", graph_file)
            return {
                "status": "error",
                "message": "No call graph found. Run index_codebase first.",
                "output_file": None,
            }
        
        output_file = None
        
        if output_format == "html":
            output_file = _generate_d3_html(graph_data)
        elif output_format == "json":
            output_file = _generate_json(graph_data)
        elif output_format == "dot":
            output_file = _generate_graphviz(graph_data)
        elif output_format == "ascii":
            return _generate_ascii(graph_data)
        else:
            return {
                "status": "error",
                "message": f"Unknown format: {output_format}. Use: html, json, dot, ascii",
            }
        
        return {
            "status": "success",
            "format": output_format,
            "nodes": nodes,
            "edges": edges,
            "output_file": str(output_file.absolute()) if output_file else None,
        }
        
    except Exception as e:
        logger.error("Graph visualization failed: %s", e)
        return {
            "status": "error",
            "message": str(e),
        }


def _generate_d3_html(graph_data: dict) -> Path:
    """Generate interactive D3.js visualization"""
    nodes_list = []
    edges_list = []
    node_map = {}
    
    nodes = graph_data.get("nodes", {})
    
    for idx, (node_id, node) in enumerate(nodes.items()):
        node_map[node_id] = idx
        nodes_list.append({
            "id": idx,
            "label": node.get("name", ""),
            "size": node.get("pagerank", 0.5) * 50 + 15,
            "color": _get_node_color(node),
            "pagerank": node.get("pagerank", 0),
        })
    
    for edge in graph_data.get("edges", []):
        source_id = edge.get("source")
        target_id = edge.get("target")
        if source_id in node_map and target_id in node_map:
            edges_list.append({
                "source": node_map[source_id],
                "target": node_map[target_id],
            })
    
    html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Call Graph - D3 Visualization</title>
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <style>
        body {{ font-family: Arial; background: #0a0e27; color: #e0e0e0; margin: 0; overflow: hidden; }}
        svg {{ background: #0a0e27; }}
        .node {{ stroke: #333; stroke-width: 2px; cursor: pointer; }}
        .node:hover {{ stroke: #fff; stroke-width: 3px; }}
        .link {{ stroke: rgba(255, 255, 255, 0.2); stroke-width: 1.5px; }}
        .label {{ font-size: 11px; pointer-events: none; fill: #fff; text-anchor: middle; }}
        .info {{ position: absolute; top: 20px; left: 20px; background: rgba(10, 14, 39, 0.95); padding: 15px; border-radius: 8px; border: 1px solid #4ECDC4; color: #e0e0e0; z-index: 100; }}
    </style>
</head>
<body>
    <svg id="graph" width="100%" height="100%"></svg>
    <div class="info">
        <h3>Call Graph</h3>
        <p>Nodes: {len(nodes_list)} | Edges: {len(edges_list)}</p>
        <p style="font-size: 11px;">Drag nodes • Scroll to zoom</p>
    </div>
    <script>
        const graphData = {json.dumps({{"nodes": nodes_list, "links": edges_list}})};
        const width = window.innerWidth;
        const height = window.innerHeight;
        const svg = d3.select('#graph').attr('width', width).attr('height', height);
        const g = svg.append('g');
        
        const simulation = d3.forceSimulation(graphData.nodes)
            .force('link', d3.forceLink(graphData.links).id(d => d.id).distance(100))
            .force('charge', d3.forceManyBody().strength(-300))
            .force('center', d3.forceCenter(width / 2, height / 2));
        
        const links = g.selectAll('.link').data(graphData.links).enter()
            .append('line').attr('class', 'link');
        
        const nodes = g.selectAll('.node').data(graphData.nodes).enter()
            .append('circle').attr('class', 'node').attr('r', d => d.size)
            .attr('fill', d => d.color).call(d3.drag()
            .on('start', d => {{ if (!d.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; }})
            .on('drag', d => {{ d.fx = d3.event.x; d.fy = d3.event.y; }})
            .on('end', d => {{ if (!d.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; }}));
        
        const labels = g.selectAll('.label').data(graphData.nodes).enter()
            .append('text').attr('class', 'label').attr('font-size', '10px').text(d => d.label);
        
        const zoom = d3.zoom().on('zoom', e => g.attr('transform', e.transform));
        svg.call(zoom);
        
        simulation.on('tick', () => {{
            links.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
                 .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
            nodes.attr('cx', d => d.x).attr('cy', d => d.y);
            labels.attr('x', d => d.x).attr('y', d => d.y);
        }});
    </script>
</body>
</html>'''
    
    output_file = Path(__file__).resolve().parent.parent / "call_graph_visualization.html"
    output_file.write_text(html, encoding='utf-8')
    logger.info("D3 visualization saved: %s", output_file)
    return output_file


def _generate_json(graph_data: dict) -> Path:
    """Export graph as JSON"""
    output_file = Path(__file__).resolve().parent.parent / "call_graph_export.json"
    output_file.write_text(json.dumps(graph_data, indent=2), encoding='utf-8')
    logger.info("JSON export saved: %s", output_file)
    return output_file


def _generate_graphviz(graph_data: dict) -> Path:
    """Export as Graphviz DOT format"""
    lines = ["digraph CallGraph {", "  rankdir=LR;"]
    
    for node_id, node in graph_data.get("nodes", {}).items():
        label = f"{node.get('name', '')}\\n(PR: {node.get('pagerank', 0):.2f})"
        lines.append(f'  "{node_id}" [label="{label}"];')
    
    for edge in graph_data.get("edges", []):
        lines.append(f'  "{edge.get("source")}" -> "{edge.get("target")}";')
    
    lines.append("}")
    
    output_file = Path(__file__).resolve().parent.parent / "call_graph.dot"
    output_file.write_text("\n".join(lines), encoding='utf-8')
    logger.info("Graphviz DOT saved: %s", output_file)
    return output_file


def _generate_ascii(graph_data: dict) -> dict:
    """Generate ASCII tree view"""
    nodes = graph_data.get("nodes", {})
    
    # Find entry points
    all_targets = set()
    for edge in graph_data.get("edges", []):
        all_targets.add(edge.get("target"))
    
    entry_points = [n for nid, n in nodes.items() if nid not in all_targets]
    
    if not entry_points:
        entry_points = sorted(nodes.values(), key=lambda n: n.get("pagerank", 0), reverse=True)[:3]
    
    output = "Call Graph (ASCII Tree):\n\n"
    for ep in entry_points[:5]:
        output += f"{ep.get('name', '')} (PageRank: {ep.get('pagerank', 0):.2f})\n"
    
    return {
        "status": "success",
        "format": "ascii",
        "output": output,
    }


def _get_node_color(node: dict) -> str:
    """Determine node color based on metrics"""
    out_degree = node.get("out_degree", 0)
    in_degree = node.get("in_degree", 0)
    
    if out_degree > 0.5:
        return "#FF6B6B"  # Red - hub
    elif in_degree > 0.5:
        return "#4ECDC4"  # Teal - popular
    else:
        return "#95E1D3"  # Green - leaf
