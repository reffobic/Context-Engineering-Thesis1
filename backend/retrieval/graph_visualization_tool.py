"""
Graph visualization tools for the pipeline.
_generate_d3_html now includes the full Call Graph Explorer UI from visualize_d3.py:
  - Sidebar with search, filter pills (All / Hubs / Popular / Leaves), stats row
  - Scrollable function list sorted by type + PageRank
  - Detail panel (PageRank, callers, calls, in/out-degree, file path)
  - Connected-node highlight on click, dimming of unrelated nodes/edges
  - Clear-selection button
  - Hints overlay
  - Collision-aware force simulation with zoom/pan
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
        chroma_path = Path(__file__).resolve().parent.parent / "superset_chroma_db"
        graph_file  = chroma_path / "call_graph.json"

        if graph_file.exists():
            logger.info("Loading graph from: %s", graph_file)
            graph_data = json.loads(graph_file.read_text(encoding="utf-8"))
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


# ─────────────────────────────────────────────────────────────────────────────
# HTML (full explorer)
# ─────────────────────────────────────────────────────────────────────────────

def _generate_d3_html(graph_data: dict) -> Path:
    """Generate full interactive D3.js Call Graph Explorer."""
    nodes_list = []
    node_map   = {}

    raw_nodes = graph_data.get("nodes", {})
    if isinstance(raw_nodes, list):
        raw_nodes = {str(n.get("id", i)): n for i, n in enumerate(raw_nodes)}

    for idx, (node_id, node) in enumerate(raw_nodes.items()):
        if not isinstance(node, dict):
            continue
        node_map[str(node_id)] = idx

        pagerank   = node.get("pagerank",   0) or 0
        in_degree  = node.get("in_degree",  0) or 0
        out_degree = node.get("out_degree", 0) or 0
        callers    = node.get("callers", []) or []
        calls      = node.get("calls",   []) or []

        if out_degree > 0.5:
            color = "#e07b54"   # hub — coral
        elif in_degree > 0.5:
            color = "#5bb8a0"   # popular — teal
        else:
            color = "#7b8ab8"   # leaf — muted blue

        nodes_list.append({
            "id":         idx,
            "label":      node.get("name", str(node_id)),
            "size":       max(8, pagerank * 60 + 10),
            "color":      color,
            "file":       node.get("file_path", "—"),
            "pagerank":   round(pagerank,   5),
            "in_degree":  round(in_degree,  4),
            "out_degree": round(out_degree, 4),
            "callers":    len(callers) if isinstance(callers, list) else callers,
            "calls":      len(calls)   if isinstance(calls,   list) else calls,
        })

    links_list = []
    raw_edges  = graph_data.get("edges", [])
    if raw_edges:
        for edge in raw_edges:
            if not isinstance(edge, dict):
                continue
            s = str(edge.get("source", ""))
            t = str(edge.get("target", ""))
            if s in node_map and t in node_map:
                links_list.append({"source": node_map[s], "target": node_map[t]})
    else:
        for node_id, node in raw_nodes.items():
            if not isinstance(node, dict):
                continue
            src = node_map.get(str(node_id))
            for call_id in (node.get("calls") or []):
                tgt = node_map.get(str(call_id))
                if src is not None and tgt is not None:
                    links_list.append({"source": src, "target": tgt})

    graph_json = json.dumps({"nodes": nodes_list, "links": links_list})

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Call Graph Explorer</title>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  :root {{
    --bg:        #0f1117;
    --surface:   #1a1d27;
    --surface2:  #22263a;
    --border:    rgba(255,255,255,.1);
    --text:      #e8eaf0;
    --muted:     #8890a8;
    --accent:    #5bb8a0;
    --hub:       #e07b54;
    --leaf:      #7b8ab8;
    --highlight: #f0d060;
    --radius:    10px;
  }}

  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
          background: var(--bg); color: var(--text); overflow: hidden; font-size: 13px; }}

  svg {{ display: block; }}

  /* ── sidebar ── */
  #sidebar {{
    position: absolute; top: 0; left: 0; bottom: 0; width: 280px;
    background: var(--surface);
    border-right: 1px solid var(--border);
    display: flex; flex-direction: column;
    z-index: 10; overflow: hidden;
  }}

  #sidebar-header {{
    padding: 18px 16px 14px;
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }}

  #sidebar-header h1 {{
    font-size: 15px; font-weight: 600; color: var(--accent); margin-bottom: 12px;
    letter-spacing: .3px;
  }}

  /* search box */
  #search-wrap {{ position: relative; margin-bottom: 10px; }}
  #search-wrap svg.icon {{
    position: absolute; left: 9px; top: 50%; transform: translateY(-50%);
    width: 14px; height: 14px; stroke: var(--muted); fill: none;
    stroke-width: 2; stroke-linecap: round; pointer-events: none;
  }}
  #search {{
    width: 100%; background: var(--surface2); border: 1px solid var(--border);
    border-radius: 6px; padding: 7px 10px 7px 30px;
    color: var(--text); font-size: 13px; outline: none; transition: border-color .2s;
  }}
  #search:focus {{ border-color: var(--accent); }}
  #search::placeholder {{ color: var(--muted); }}

  /* filter pills */
  #filters {{ display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 4px; }}
  .pill {{
    padding: 4px 10px; border-radius: 20px; font-size: 11px; cursor: pointer;
    border: 1px solid var(--border); background: transparent;
    color: var(--muted); transition: all .15s; user-select: none;
  }}
  .pill:hover {{ border-color: var(--accent); color: var(--accent); }}
  .pill.active {{ background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 500; }}
  .pill.hub.active  {{ background: var(--hub);  border-color: var(--hub);  }}
  .pill.leaf.active {{ background: var(--leaf); border-color: var(--leaf); }}

  /* stats row */
  #stats {{ display: flex; gap: 8px; margin-top: 8px; }}
  .stat {{ flex: 1; background: var(--surface2); border-radius: 6px; padding: 8px; text-align: center; }}
  .stat-num {{ font-size: 17px; font-weight: 600; color: var(--text); }}
  .stat-lbl {{ font-size: 10px; color: var(--muted); margin-top: 1px; text-transform: uppercase; letter-spacing: .4px; }}

  /* results list */
  #results-label {{
    padding: 10px 16px 5px; font-size: 11px; color: var(--muted);
    text-transform: uppercase; letter-spacing: .5px; flex-shrink: 0;
  }}
  #results {{ flex: 1; overflow-y: auto; padding: 0 8px 12px; }}
  #results::-webkit-scrollbar {{ width: 4px; }}
  #results::-webkit-scrollbar-track {{ background: transparent; }}
  #results::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 4px; }}

  .result-item {{
    display: flex; align-items: center; gap: 10px;
    padding: 8px; border-radius: var(--radius); cursor: pointer;
    transition: background .12s; border: 1px solid transparent; margin-bottom: 2px;
  }}
  .result-item:hover {{ background: var(--surface2); }}
  .result-item.selected {{ background: var(--surface2); border-color: var(--accent); }}
  .result-dot {{ width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }}
  .result-name {{ font-size: 12px; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; }}
  .result-badge {{ font-size: 10px; color: var(--muted); background: var(--bg); padding: 1px 6px; border-radius: 10px; flex-shrink: 0; }}

  /* ── canvas ── */
  #canvas {{ position: absolute; left: 280px; top: 0; right: 0; bottom: 0; }}

  /* ── detail panel ── */
  #detail {{
    position: absolute; bottom: 20px; right: 20px;
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 16px; min-width: 240px; max-width: 300px;
    display: none; z-index: 10;
  }}
  #detail.show {{ display: block; }}
  #detail h3 {{ font-size: 13px; font-weight: 600; color: var(--accent); margin-bottom: 10px;
                word-break: break-all; line-height: 1.4; }}
  .detail-row {{ display: flex; justify-content: space-between; align-items: baseline;
                 padding: 4px 0; border-bottom: 1px solid var(--border); gap: 10px; }}
  .detail-row:last-child {{ border-bottom: none; }}
  .detail-key {{ color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .4px; }}
  .detail-val {{ color: var(--text); font-size: 12px; font-weight: 500; text-align: right;
                 word-break: break-all; max-width: 160px; }}
  #detail-file {{ font-size: 11px; color: var(--muted); margin-top: 8px; word-break: break-all; line-height: 1.5; }}

  /* ── hints ── */
  #hints {{
    position: absolute; top: 16px; right: 16px;
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 10px 14px;
    font-size: 11px; color: var(--muted); line-height: 1.9;
    pointer-events: none;
  }}

  /* ── graph elements ── */
  .link {{ stroke: rgba(255,255,255,.1); stroke-width: 1px; }}
  .link.highlight {{ stroke: var(--highlight); stroke-width: 2px; opacity: .9; }}
  .link.dimmed {{ opacity: .05; }}

  .node {{ cursor: pointer; transition: opacity .15s; }}
  .node.dimmed {{ opacity: .15; }}
  .node.selected circle {{ stroke: #fff; stroke-width: 2.5px; }}

  .label {{ font-size: 10px; fill: var(--text); text-anchor: middle;
            pointer-events: none; paint-order: stroke;
            stroke: var(--bg); stroke-width: 3px; }}

  /* clear btn */
  #clear-btn {{
    display: none; position: absolute; top: 16px; left: 296px;
    background: var(--surface); border: 1px solid var(--border);
    color: var(--muted); border-radius: 6px; padding: 5px 10px;
    font-size: 12px; cursor: pointer; z-index: 10; transition: all .15s;
  }}
  #clear-btn:hover {{ border-color: var(--accent); color: var(--accent); }}
</style>
</head>
<body>

<!-- Sidebar -->
<div id="sidebar">
  <div id="sidebar-header">
    <h1>Call Graph Explorer</h1>
    <div id="search-wrap">
      <svg class="icon" viewBox="0 0 24 24">
        <circle cx="11" cy="11" r="7"/><line x1="16.5" y1="16.5" x2="22" y2="22"/>
      </svg>
      <input id="search" type="text" placeholder="Search functions…" autocomplete="off" spellcheck="false">
    </div>
    <div id="filters">
      <div class="pill active" data-type="all">All</div>
      <div class="pill hub"    data-type="hub">Hubs</div>
      <div class="pill"        data-type="popular">Popular</div>
      <div class="pill leaf"   data-type="leaf">Leaves</div>
    </div>
    <div id="stats">
      <div class="stat"><div class="stat-num" id="st-nodes">0</div><div class="stat-lbl">Nodes</div></div>
      <div class="stat"><div class="stat-num" id="st-edges">0</div><div class="stat-lbl">Edges</div></div>
      <div class="stat"><div class="stat-num" id="st-shown">0</div><div class="stat-lbl">Shown</div></div>
    </div>
  </div>
  <div id="results-label">Functions</div>
  <div id="results"></div>
</div>

<!-- Canvas -->
<div id="canvas">
  <svg id="graph"></svg>
</div>

<!-- Detail panel -->
<div id="detail">
  <h3 id="d-name"></h3>
  <div class="detail-row"><span class="detail-key">PageRank</span>  <span class="detail-val" id="d-pr"></span></div>
  <div class="detail-row"><span class="detail-key">Callers</span>   <span class="detail-val" id="d-callers"></span></div>
  <div class="detail-row"><span class="detail-key">Calls</span>     <span class="detail-val" id="d-calls"></span></div>
  <div class="detail-row"><span class="detail-key">In-degree</span> <span class="detail-val" id="d-in"></span></div>
  <div class="detail-row"><span class="detail-key">Out-degree</span><span class="detail-val" id="d-out"></span></div>
  <div id="detail-file"></div>
</div>

<!-- Hints -->
<div id="hints">
  Scroll to zoom &nbsp;·&nbsp; Drag to pan &nbsp;·&nbsp; Click node for details
</div>

<!-- Clear selection -->
<button id="clear-btn">&#x2715; Clear selection</button>

<script>
const graphData = {graph_json};
const HUB_COLOR = "#e07b54", POP_COLOR = "#5bb8a0", LEAF_COLOR = "#7b8ab8";

document.getElementById("st-nodes").textContent = graphData.nodes.length;
document.getElementById("st-edges").textContent = graphData.links.length;

// ── Canvas setup ──────────────────────────────────────────
const container = document.getElementById("canvas");
const W = () => container.clientWidth;
const H = () => container.clientHeight;

const svg = d3.select("#graph").attr("width", W()).attr("height", H());
window.addEventListener("resize", () => {{ svg.attr("width", W()).attr("height", H()); }});

const g = svg.append("g");

// ── Simulation ────────────────────────────────────────────
const sim = d3.forceSimulation(graphData.nodes)
    .force("link",    d3.forceLink(graphData.links).id(d => d.id).distance(90).strength(0.4))
    .force("charge",  d3.forceManyBody().strength(-350))
    .force("center",  d3.forceCenter(W()/2, H()/2))
    .force("collide", d3.forceCollide().radius(d => d.size + 6));

// ── Links ─────────────────────────────────────────────────
const linkSel = g.selectAll(".link")
    .data(graphData.links).enter()
    .append("line").attr("class", "link");

// ── Nodes ─────────────────────────────────────────────────
const nodeG = g.selectAll(".node")
    .data(graphData.nodes).enter()
    .append("g").attr("class", "node")
    .call(d3.drag()
        .on("start", (ev, d) => {{ if (!ev.active) sim.alphaTarget(0.3).restart(); d.fx=d.x; d.fy=d.y; }})
        .on("drag",  (ev, d) => {{ d.fx=ev.x; d.fy=ev.y; }})
        .on("end",   (ev, d) => {{ if (!ev.active) sim.alphaTarget(0); d.fx=null; d.fy=null; }}));

nodeG.append("circle")
    .attr("r",    d => d.size)
    .attr("fill", d => d.color)
    .attr("stroke", "rgba(255,255,255,.25)")
    .attr("stroke-width", 1.5);

nodeG.append("text")
    .attr("class", "label")
    .attr("dy", d => d.size + 12)
    .text(d => d.label.length > 22 ? d.label.slice(0,20) + "\u2026" : d.label);

// ── Tick ──────────────────────────────────────────────────
sim.on("tick", () => {{
    linkSel
        .attr("x1", d => d.source.x).attr("y1", d => d.source.y)
        .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
    nodeG.attr("transform", d => `translate(${{d.x}},${{d.y}})`);
}});

// ── Zoom ──────────────────────────────────────────────────
svg.call(d3.zoom()
    .scaleExtent([0.08, 6])
    .on("zoom", ev => g.attr("transform", ev.transform)));

// ── Selection state ───────────────────────────────────────
let selectedId  = null;
let activeFilter = "all";
let searchTerm   = "";

function nodeType(d) {{
    if (d.color === HUB_COLOR)  return "hub";
    if (d.color === POP_COLOR)  return "popular";
    return "leaf";
}}

function matchesFilter(d) {{
    if (activeFilter === "all") return true;
    return nodeType(d) === activeFilter;
}}

function matchesSearch(d) {{
    if (!searchTerm) return true;
    return d.label.toLowerCase().includes(searchTerm) ||
           d.file.toLowerCase().includes(searchTerm);
}}

function isVisible(d) {{ return matchesFilter(d) && matchesSearch(d); }}

function getConnected(nodeId) {{
    const connected = new Set([nodeId]);
    const edgeSet   = new Set();
    graphData.links.forEach((l, i) => {{
        const s = l.source.id ?? l.source;
        const t = l.target.id ?? l.target;
        if (s === nodeId || t === nodeId) {{
            connected.add(s); connected.add(t); edgeSet.add(i);
        }}
    }});
    return {{ connected, edgeSet }};
}}

function applySelection(d) {{
    if (selectedId === d.id) {{ clearSelection(); return; }}
    selectedId = d.id;
    const {{ connected, edgeSet }} = getConnected(d.id);

    nodeG.classed("dimmed",   n => !connected.has(n.id))
         .classed("selected", n => n.id === d.id);
    linkSel.classed("dimmed",    (l,i) => !edgeSet.has(i))
           .classed("highlight", (l,i) =>  edgeSet.has(i));

    document.getElementById("d-name").textContent    = d.label;
    document.getElementById("d-pr").textContent      = d.pagerank;
    document.getElementById("d-callers").textContent = d.callers;
    document.getElementById("d-calls").textContent   = d.calls;
    document.getElementById("d-in").textContent      = d.in_degree;
    document.getElementById("d-out").textContent     = d.out_degree;
    document.getElementById("detail-file").textContent = d.file !== "\u2014" ? d.file : "";
    document.getElementById("detail").classList.add("show");
    document.getElementById("clear-btn").style.display = "block";

    document.querySelectorAll(".result-item").forEach(el => {{
        el.classList.toggle("selected", +el.dataset.id === d.id);
    }});
}}

function clearSelection() {{
    selectedId = null;
    nodeG.classed("dimmed", false).classed("selected", false);
    linkSel.classed("dimmed", false).classed("highlight", false);
    document.getElementById("detail").classList.remove("show");
    document.getElementById("clear-btn").style.display = "none";
    document.querySelectorAll(".result-item").forEach(el => el.classList.remove("selected"));
}}

nodeG.on("click", (ev, d) => {{ ev.stopPropagation(); if (!isVisible(d)) return; applySelection(d); }});
svg.on("click", clearSelection);
document.getElementById("clear-btn").addEventListener("click", clearSelection);

// ── Sidebar list ──────────────────────────────────────────
function renderList() {{
    const results = document.getElementById("results");
    const label   = document.getElementById("results-label");
    const visible  = graphData.nodes.filter(isVisible);
    document.getElementById("st-shown").textContent = visible.length;

    if (visible.length === 0) {{
        results.innerHTML = `<div style="padding:20px 8px;color:var(--muted);text-align:center;font-size:12px">No matches found</div>`;
        label.textContent = "Functions";
        return;
    }}

    label.textContent = `Functions (${{visible.length}})`;

    const sorted = [...visible].sort((a,b) => {{
        const ta = nodeType(a), tb = nodeType(b);
        if (ta === "hub" && tb !== "hub") return -1;
        if (tb === "hub" && ta !== "hub") return 1;
        return b.pagerank - a.pagerank;
    }});

    results.innerHTML = sorted.map(d => {{
        const badge = nodeType(d) === "hub"
            ? `<span class="result-badge" style="background:rgba(224,123,84,.2);color:var(--hub)">hub</span>`
            : nodeType(d) === "popular"
            ? `<span class="result-badge" style="background:rgba(91,184,160,.2);color:var(--accent)">pop</span>`
            : "";
        return `<div class="result-item${{d.id===selectedId?" selected":""}}" data-id="${{d.id}}" title="${{d.file}}">
          <div class="result-dot" style="background:${{d.color}}"></div>
          <span class="result-name">${{d.label}}</span>
          ${{badge}}
        </div>`;
    }}).join("");

    results.querySelectorAll(".result-item").forEach(el => {{
        el.addEventListener("click", () => {{
            const d = graphData.nodes.find(n => n.id === +el.dataset.id);
            if (d) {{
                applySelection(d);
                const t = d3.zoomTransform(svg.node());
                const nx = W()/2 - t.k * d.x;
                const ny = H()/2 - t.k * d.y;
                svg.transition().duration(450)
                   .call(d3.zoom().transform, d3.zoomIdentity.translate(nx, ny).scale(t.k));
            }}
        }});
    }});
}}

// ── Node visibility ───────────────────────────────────────
function applyVisibility() {{
    nodeG.style("display", d => isVisible(d) ? null : "none");
    linkSel.style("display", l => {{
        const s  = l.source.id ?? l.source;
        const t  = l.target.id ?? l.target;
        const sn = graphData.nodes[s], tn = graphData.nodes[t];
        return (sn && tn && isVisible(sn) && isVisible(tn)) ? null : "none";
    }});
    if (selectedId !== null) {{
        const d = graphData.nodes[selectedId];
        if (!d || !isVisible(d)) clearSelection();
    }}
    renderList();
}}

// ── Search ────────────────────────────────────────────────
document.getElementById("search").addEventListener("input", ev => {{
    searchTerm = ev.target.value.toLowerCase().trim();
    applyVisibility();
}});

// ── Filter pills ──────────────────────────────────────────
document.querySelectorAll(".pill").forEach(pill => {{
    pill.addEventListener("click", () => {{
        document.querySelectorAll(".pill").forEach(p => p.classList.remove("active"));
        pill.classList.add("active");
        activeFilter = pill.dataset.type;
        applyVisibility();
    }});
}});

// Initial render
renderList();
console.log(`Loaded ${{graphData.nodes.length}} nodes, ${{graphData.links.length}} edges`);
</script>
</body>
</html>'''

    output_file = Path(__file__).resolve().parent.parent / "call_graph_visualization.html"
    output_file.write_text(html, encoding="utf-8")
    logger.info("D3 Explorer saved: %s", output_file)
    return output_file


# ─────────────────────────────────────────────────────────────────────────────
# JSON export
# ─────────────────────────────────────────────────────────────────────────────

def _generate_json(graph_data: dict) -> Path:
    output_file = Path(__file__).resolve().parent.parent / "call_graph_export.json"
    output_file.write_text(json.dumps(graph_data, indent=2), encoding="utf-8")
    logger.info("JSON export saved: %s", output_file)
    return output_file


# ─────────────────────────────────────────────────────────────────────────────
# Graphviz DOT export
# ─────────────────────────────────────────────────────────────────────────────

def _generate_graphviz(graph_data: dict) -> Path:
    lines = ["digraph CallGraph {", "  rankdir=LR;"]

    nodes = graph_data.get("nodes", {})
    if isinstance(nodes, list):
        nodes = {str(n.get("id", i)): n for i, n in enumerate(nodes)}

    for node_id, node in nodes.items():
        if not isinstance(node, dict):
            continue
        label = f"{node.get('name', node_id)}\\n(PR: {node.get('pagerank', 0):.2f})"
        lines.append(f'  "{node_id}" [label="{label}"];')

    for edge in graph_data.get("edges", []):
        if not isinstance(edge, dict):
            continue
        lines.append(f'  "{edge.get("source")}" -> "{edge.get("target")}";')

    lines.append("}")
    output_file = Path(__file__).resolve().parent.parent / "call_graph.dot"
    output_file.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Graphviz DOT saved: %s", output_file)
    return output_file


# ─────────────────────────────────────────────────────────────────────────────
# ASCII overview
# ─────────────────────────────────────────────────────────────────────────────

def _generate_ascii(graph_data: dict) -> dict:
    nodes = graph_data.get("nodes", {})
    if isinstance(nodes, list):
        nodes = {str(n.get("id", i)): n for i, n in enumerate(nodes)}

    all_targets = set()
    for edge in graph_data.get("edges", []):
        if not isinstance(edge, dict):
            continue
        target = edge.get("target")
        if target is not None and not isinstance(target, dict):
            all_targets.add(str(target))

    entry_points = [
        n for nid, n in nodes.items()
        if isinstance(n, dict) and str(nid) not in all_targets
    ]

    if not entry_points:
        entry_points = sorted(
            [n for n in nodes.values() if isinstance(n, dict)],
            key=lambda n: n.get("pagerank", 0),
            reverse=True,
        )[:3]

    output = "Call Graph (ASCII Tree):\n\n"
    for ep in entry_points[:5]:
        output += f"{ep.get('name', '')} (PageRank: {ep.get('pagerank', 0):.2f})\n"

    return {"status": "success", "format": "ascii", "output": output}