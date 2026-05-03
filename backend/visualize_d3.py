#!/usr/bin/env python3
"""
Graph Visualization with D3.js
================================

Create an interactive graph visualization using D3.js,
which is more reliable than Cosmograph.

Run: python backend/visualize_d3.py
"""

import sys
import json
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))


def generate_d3_html(graph, output_file="call_graph_d3.html"):
    """Generate an interactive D3.js visualization."""
    
    # Prepare nodes data
    nodes = []
    node_map = {}  # Map from node_id to index for edges
    
    for idx, (node_id, node) in enumerate(graph.nodes.items()):
        node_map[node_id] = idx
        
        # Node size based on PageRank
        size = node.pagerank * 50 + 15
        
        # Color based on function type and importance
        if node.out_degree > 0.5:  # Hub (calls many things)
            color = "#FF6B6B"  # Red
        elif node.in_degree > 0.5:  # Popular (called by many)
            color = "#4ECDC4"  # Teal
        else:  # Leaf function
            color = "#95E1D3"  # Light green
        
        nodes.append({
            "id": idx,
            "label": node.name,
            "size": size,
            "color": color,
            "file": node.file_path,
            "pagerank": round(node.pagerank, 3),
            "in_degree": round(node.in_degree, 3),
            "out_degree": round(node.out_degree, 3),
            "callers": len(node.callers),
            "calls": len(node.calls),
        })
    
    # Prepare edges data
    links = []
    for node_id, node in graph.nodes.items():
        source_idx = node_map.get(node_id)
        if source_idx is None:
            continue
        
        for call_id in node.calls:
            target_idx = node_map.get(call_id)
            if target_idx is not None:
                links.append({
                    "source": source_idx,
                    "target": target_idx,
                })
    
    graph_data = json.dumps({"nodes": nodes, "links": links})
    
    # Generate HTML with D3.js
    html_content = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Call Graph - D3 Visualization</title>
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: #0a0e27;
            color: #e0e0e0;
            overflow: hidden;
        }}
        
        #graph {{
            width: 100vw;
            height: 100vh;
        }}
        
        svg {{
            background: #0a0e27;
        }}
        
        .controls {{
            position: absolute;
            top: 20px;
            left: 20px;
            background: rgba(10, 14, 39, 0.95);
            padding: 20px;
            border-radius: 8px;
            border: 1px solid #4ECDC4;
            max-width: 350px;
            z-index: 100;
        }}
        
        .controls h1 {{
            font-size: 18px;
            margin-bottom: 15px;
            color: #4ECDC4;
        }}
        
        .info {{
            font-size: 13px;
            line-height: 1.6;
            margin-bottom: 15px;
            color: #b0b0b0;
        }}
        
        .legend {{
            font-size: 12px;
            margin-top: 15px;
            border-top: 1px solid #4ECDC4;
            padding-top: 15px;
        }}
        
        .legend-item {{
            display: flex;
            align-items: center;
            margin-bottom: 8px;
        }}
        
        .legend-color {{
            width: 16px;
            height: 16px;
            border-radius: 50%;
            margin-right: 10px;
        }}
        
        .node {{
            stroke: #333;
            stroke-width: 2px;
            cursor: pointer;
        }}
        
        .node:hover {{
            stroke: #fff;
            stroke-width: 3px;
        }}
        
        .link {{
            stroke: rgba(255, 255, 255, 0.2);
            stroke-width: 1.5px;
        }}
        
        .node-label {{
            font-size: 11px;
            pointer-events: none;
            fill: #fff;
            text-anchor: middle;
            dy: "0.3em";
        }}
        
        .node-info {{
            position: absolute;
            bottom: 20px;
            left: 20px;
            background: rgba(10, 14, 39, 0.95);
            padding: 15px;
            border-radius: 8px;
            border: 1px solid #4ECDC4;
            font-size: 12px;
            display: none;
            z-index: 100;
            min-width: 280px;
        }}
        
        .node-info.active {{
            display: block;
        }}
        
        .node-info h3 {{
            color: #4ECDC4;
            margin-bottom: 8px;
            font-size: 14px;
        }}
        
        .node-info p {{
            margin-bottom: 4px;
            color: #b0b0b0;
        }}
        
        .instructions {{
            position: absolute;
            bottom: 20px;
            right: 20px;
            background: rgba(10, 14, 39, 0.95);
            padding: 15px;
            border-radius: 8px;
            border: 1px solid #4ECDC4;
            font-size: 12px;
            max-width: 280px;
        }}
        
        .instructions h4 {{
            color: #4ECDC4;
            margin-bottom: 8px;
            font-size: 13px;
        }}
        
        .instructions p {{
            margin-bottom: 6px;
            color: #b0b0b0;
        }}
    </style>
</head>
<body>
    <svg id="graph"></svg>
    
    <div class="controls">
        <h1>Call Graph Explorer</h1>
        <div class="info">
            <p><strong>Nodes:</strong> {len(nodes)}</p>
            <p><strong>Edges:</strong> {len(links)}</p>
            <p style="margin-top: 10px; font-size: 11px; color: #888;">
                Hover over nodes to see details. 
                Drag to move, scroll to zoom.
            </p>
        </div>
        <div class="legend">
            <strong style="color: #4ECDC4;">Legend:</strong>
            <div class="legend-item">
                <div class="legend-color" style="background: #FF6B6B;"></div>
                <span>Hub (calls many)</span>
            </div>
            <div class="legend-item">
                <div class="legend-color" style="background: #4ECDC4;"></div>
                <span>Popular (called by many)</span>
            </div>
            <div class="legend-item">
                <div class="legend-color" style="background: #95E1D3;"></div>
                <span>Leaf (utility)</span>
            </div>
        </div>
    </div>
    
    <div class="node-info" id="nodeInfo">
        <h3 id="nodeName"></h3>
        <div id="nodeDetails"></div>
    </div>
    
    <div class="instructions">
        <h4>Controls</h4>
        <p>🖱️ Drag nodes to move</p>
        <p>🖱️ Scroll to zoom</p>
        <p>🖱️ Hover for info</p>
        <p style="margin-top: 8px; font-size: 11px; color: #888;">
            Larger nodes = higher PageRank
        </p>
    </div>
    
    <script>
        const graphData = {graph_data};
        
        const width = window.innerWidth;
        const height = window.innerHeight;
        
        const svg = d3.select('#graph')
            .attr('width', width)
            .attr('height', height);
        
        const g = svg.append('g');
        
        // Create simulation
        const simulation = d3.forceSimulation(graphData.nodes)
            .force('link', d3.forceLink(graphData.links)
                .id(d => d.id)
                .distance(100)
                .strength(0.5))
            .force('charge', d3.forceManyBody().strength(-300))
            .force('center', d3.forceCenter(width / 2, height / 2))
            .force('collide', d3.forceCollide().radius(d => d.size + 10));
        
        // Create links
        const link = g.selectAll('.link')
            .data(graphData.links)
            .enter()
            .append('line')
            .attr('class', 'link')
            .attr('stroke', '#999')
            .attr('stroke-opacity', 0.2)
            .attr('stroke-width', 1.5);
        
        // Create nodes
        const node = g.selectAll('.node')
            .data(graphData.nodes)
            .enter()
            .append('circle')
            .attr('class', 'node')
            .attr('r', d => d.size)
            .attr('fill', d => d.color)
            .attr('stroke', '#333')
            .attr('stroke-width', 2)
            .call(d3.drag()
                .on('start', dragstarted)
                .on('drag', dragged)
                .on('end', dragended));
        
        // Create labels
        const label = g.selectAll('.node-label')
            .data(graphData.nodes)
            .enter()
            .append('text')
            .attr('class', 'node-label')
            .attr('font-size', '10px')
            .attr('fill', '#fff')
            .attr('text-anchor', 'middle')
            .attr('dy', '0.3em')
            .text(d => d.label);
        
        // Node hover info
        const nodeInfo = document.getElementById('nodeInfo');
        const nodeName = document.getElementById('nodeName');
        const nodeDetails = document.getElementById('nodeDetails');
        
        node.on('mouseenter', function(e, d) {{
            d3.select(this)
                .attr('stroke', '#fff')
                .attr('stroke-width', 3);
            
            nodeName.textContent = d.label;
            nodeDetails.innerHTML = `
                <p><strong>File:</strong> ${{d.file}}</p>
                <p><strong>PageRank:</strong> ${{d.pagerank}}</p>
                <p><strong>Callers:</strong> ${{d.callers}}</p>
                <p><strong>Calls:</strong> ${{d.calls}}</p>
                <p><strong>In-Degree:</strong> ${{d.in_degree}}</p>
                <p><strong>Out-Degree:</strong> ${{d.out_degree}}</p>
            `;
            nodeInfo.classList.add('active');
        }})
        .on('mouseleave', function() {{
            d3.select(this)
                .attr('stroke', '#333')
                .attr('stroke-width', 2);
            nodeInfo.classList.remove('active');
        }});
        
        // Zoom behavior
        const zoom = d3.zoom()
            .on('zoom', (event) => {{
                g.attr('transform', event.transform);
            }});
        
        svg.call(zoom);
        
        // Update positions on tick
        simulation.on('tick', () => {{
            link
                .attr('x1', d => d.source.x)
                .attr('y1', d => d.source.y)
                .attr('x2', d => d.target.x)
                .attr('y2', d => d.target.y);
            
            node
                .attr('cx', d => d.x)
                .attr('cy', d => d.y);
            
            label
                .attr('x', d => d.x)
                .attr('y', d => d.y);
        }});
        
        function dragstarted(event, d) {{
            if (!event.active) simulation.alphaTarget(0.3).restart();
            d.fx = d.x;
            d.fy = d.y;
        }}
        
        function dragged(event, d) {{
            d.fx = event.x;
            d.fy = event.y;
        }}
        
        function dragended(event, d) {{
            if (!event.active) simulation.alphaTarget(0);
            d.fx = null;
            d.fy = null;
        }}
        
        console.log('Graph loaded with ' + graphData.nodes.length + ' nodes and ' + graphData.links.length + ' edges');
    </script>
</body>
</html>
'''
    
    output_path = Path(output_file)
    output_path.write_text(html_content, encoding='utf-8')
    print(f"\n[OK] Created D3 visualization: {output_path.absolute()}")
    return output_path


def main():
    print("\n" + "="*70)
    print("D3.js Graph Visualization Generator")
    print("="*70)
    
    # Build the graph
    from indexing.graph_builder import CallGraphBuilder
    
    test_repo = Path("C:/tmp/test_repo_graph")
    if not test_repo.exists():
        print("[ERROR] Test repo not found. Run test_simple.py first.")
        return
    
    py_files = list(test_repo.glob("*.py"))
    if not py_files:
        print("[ERROR] No Python files found in test repo")
        return
    
    print(f"[INFO] Building graph from {len(py_files)} files...")
    builder = CallGraphBuilder()
    graph = builder.build_from_files(py_files, test_repo)
    print(f"[OK] Graph built with {len(graph.nodes)} nodes\n")
    
    # Generate D3 HTML
    html_file = generate_d3_html(graph, "call_graph_d3.html")
    
    print("\n" + "="*70)
    print("D3 VISUALIZATION READY!")
    print("="*70)
    print(f"\n📊 Open in browser: {html_file.absolute()}")
    print("\nFeatures:")
    print("  • Interactive force-directed graph")
    print("  • Drag nodes to reposition")
    print("  • Scroll to zoom in/out")
    print("  • Hover for node details")
    print("  • Smooth physics simulation")
    print("\nNode Colors:")
    print("  🔴 Red:      Hub functions (call many others)")
    print("  🟢 Teal:     Popular functions (called by many)")
    print("  💎 Green:    Leaf functions (utilities)")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()
