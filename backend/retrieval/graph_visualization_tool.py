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

        pagerank      = node.get("pagerank",   0) or 0
        in_degree     = node.get("in_degree",  0) or 0
        out_degree    = node.get("out_degree", 0) or 0
        callers       = node.get("callers",       []) or []
        calls         = node.get("calls",         []) or []
        inherits_from = node.get("inherits_from", []) or []
        inherited_by  = node.get("inherited_by",  []) or []
        imports       = node.get("imports",       []) or []
        imported_by   = node.get("imported_by",   []) or []
        node_type     = node.get("node_type", "function")   # "class" | "function" | …
        language      = node.get("language", "")
        start_line    = node.get("start_line", None)
        end_line      = node.get("end_line",   None)

        is_class = node_type == "class"

        # Base hue: class = purple family, function = blue/teal/coral family
        if is_class:
            if out_degree > 0.5:
                color = "#c47ed4"   # class hub — violet
            elif in_degree > 0.5:
                color = "#a78bfa"   # class popular — purple
            else:
                color = "#7c6fcd"   # class leaf — muted purple
        else:
            if out_degree > 0.5:
                color = "#e07b54"   # func hub — coral
            elif in_degree > 0.5:
                color = "#5bb8a0"   # func popular — teal
            else:
                color = "#7b8ab8"   # func leaf — muted blue

        nodes_list.append({
            "id":           idx,
            "label":        node.get("name", str(node_id)),
            "size":         max(8, pagerank * 60 + 10),
            "color":        color,
            "file":         node.get("file_path", "—"),
            "pagerank":     round(pagerank,   5),
            "in_degree":    round(in_degree,  4),
            "out_degree":   round(out_degree, 4),
            "callers":      len(callers)       if isinstance(callers,       list) else callers,
            "calls":        len(calls)         if isinstance(calls,         list) else calls,
            "inherits_from":len(inherits_from) if isinstance(inherits_from, list) else inherits_from,
            "inherited_by": len(inherited_by)  if isinstance(inherited_by,  list) else inherited_by,
            "imports":      len(imports)       if isinstance(imports,       list) else imports,
            "imported_by":  len(imported_by)   if isinstance(imported_by,   list) else imported_by,
            "node_type":    node_type,
            "language":     language,
            "start_line":   start_line,
            "end_line":     end_line,
            "is_class":     is_class,
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
                links_list.append({
                    "source":   node_map[s],
                    "target":   node_map[t],
                    "rel_type": edge.get("rel_type", edge.get("type", "calls")),
                })
    else:
        # Derive edges from node metadata, tagging by relationship kind
        for node_id, node in raw_nodes.items():
            if not isinstance(node, dict):
                continue
            src = node_map.get(str(node_id))
            if src is None:
                continue
            for call_id in (node.get("calls") or []):
                tgt = node_map.get(str(call_id))
                if tgt is not None:
                    links_list.append({"source": src, "target": tgt, "rel_type": "calls"})
            for inh_id in (node.get("inherits_from") or []):
                tgt = node_map.get(str(inh_id))
                if tgt is not None:
                    links_list.append({"source": src, "target": tgt, "rel_type": "inherits"})
            for imp_id in (node.get("imports") or []):
                tgt = node_map.get(str(imp_id))
                if tgt is not None:
                    links_list.append({"source": src, "target": tgt, "rel_type": "imports"})

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
    --bg:       #0f1117; --surface: #1a1d27; --surface2: #22263a;
    --border:   rgba(255,255,255,.1); --text: #e8eaf0; --muted: #8890a8;
    --accent:   #5bb8a0; --hub: #e07b54; --leaf: #7b8ab8;
    --cls-leaf: #7c6fcd; --radius: 10px;
  }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
          background: var(--bg); color: var(--text); overflow: hidden; font-size: 13px; }}

  /* ── sidebar ── */
  #sidebar {{
    position: absolute; top: 0; left: 0; bottom: 0; width: 280px;
    background: var(--surface); border-right: 1px solid var(--border);
    display: flex; flex-direction: column; z-index: 10; overflow: hidden;
  }}
  #sidebar-header {{ padding: 18px 16px 14px; border-bottom: 1px solid var(--border); flex-shrink: 0; }}
  #sidebar-header h1 {{ font-size: 15px; font-weight: 600; color: var(--accent); margin-bottom: 12px; letter-spacing: .3px; }}
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
  #filters {{ display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 4px; }}
  .pill {{
    padding: 4px 10px; border-radius: 20px; font-size: 11px; cursor: pointer;
    border: 1px solid var(--border); background: transparent;
    color: var(--muted); transition: all .15s; user-select: none;
  }}
  .pill:hover {{ border-color: var(--accent); color: var(--accent); }}
  .pill.active {{ background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 500; }}
  .pill.hub.active  {{ background: var(--hub);  border-color: var(--hub); }}
  .pill.leaf.active {{ background: var(--leaf); border-color: var(--leaf); }}
  #stats {{ display: flex; gap: 8px; margin-top: 8px; }}
  .stat {{ flex: 1; background: var(--surface2); border-radius: 6px; padding: 8px; text-align: center; }}
  .stat-num {{ font-size: 17px; font-weight: 600; color: var(--text); }}
  .stat-lbl {{ font-size: 10px; color: var(--muted); margin-top: 1px; text-transform: uppercase; letter-spacing: .4px; }}
  #results-label {{ padding: 10px 16px 5px; font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .5px; flex-shrink: 0; }}
  #results {{ flex: 1; overflow-y: auto; padding: 0 8px 12px; }}
  #results::-webkit-scrollbar {{ width: 4px; }}
  #results::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 4px; }}
  .result-item {{
    display: flex; align-items: center; gap: 10px; padding: 8px;
    border-radius: var(--radius); cursor: pointer; transition: background .12s;
    border: 1px solid transparent; margin-bottom: 2px;
  }}
  .result-item:hover {{ background: var(--surface2); }}
  .result-item.selected {{ background: var(--surface2); border-color: var(--accent); }}
  .result-dot {{ width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }}
  .result-name {{ font-size: 12px; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; }}
  .result-badge {{ font-size: 10px; color: var(--muted); background: var(--bg); padding: 1px 6px; border-radius: 10px; flex-shrink: 0; }}

  /* ── canvas area ── */
  #canvas-wrap {{ position: absolute; left: 280px; top: 0; right: 0; bottom: 0; }}
  #c {{ display: block; width: 100%; height: 100%; cursor: grab; }}
  #c:active {{ cursor: grabbing; }}

  /* ── detail panel ── */
  #detail {{
    position: absolute; bottom: 20px; right: 20px;
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 16px; min-width: 240px; max-width: 300px;
    display: none; z-index: 10;
  }}
  #detail.show {{ display: block; }}
  #detail h3 {{ font-size: 13px; font-weight: 600; color: var(--accent); margin-bottom: 10px; word-break: break-all; line-height: 1.4; }}
  .detail-row {{ display: flex; justify-content: space-between; align-items: baseline;
                 padding: 4px 0; border-bottom: 1px solid var(--border); gap: 10px; }}
  .detail-row:last-child {{ border-bottom: none; }}
  .detail-key {{ color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .4px; }}
  .detail-val {{ color: var(--text); font-size: 12px; font-weight: 500; text-align: right; word-break: break-all; max-width: 160px; }}
  #detail-file {{ font-size: 11px; color: var(--muted); margin-top: 8px; word-break: break-all; line-height: 1.5; }}

  /* ── legend ── */
  #legend {{
    position: absolute; bottom: 20px; left: 296px;
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 12px 14px;
    font-size: 11px; color: var(--muted); z-index: 9; line-height: 1.7; min-width: 200px;
  }}
  #legend h4 {{ font-size: 10px; text-transform: uppercase; letter-spacing: .5px; color: var(--muted); margin-bottom: 6px; }}
  .legend-row {{ display: flex; align-items: center; gap: 8px; margin-bottom: 3px; }}
  .leg-dot     {{ width: 11px; height: 11px; border-radius: 50%; flex-shrink: 0; }}
  .leg-diamond {{ width: 11px; height: 11px; flex-shrink: 0; transform: rotate(45deg); border-radius: 2px; }}
  .leg-line    {{ width: 22px; height: 2px; flex-shrink: 0; border-radius: 2px; }}
  .leg-dashed  {{ width: 22px; height: 0; border-top: 2px dashed; flex-shrink: 0; }}
  .leg-dotted  {{ width: 22px; height: 0; border-top: 2px dotted; flex-shrink: 0; }}

  /* ── clear btn ── */
  #clear-btn {{
    display: none; position: absolute; top: 16px; left: 296px;
    background: var(--surface); border: 1px solid var(--border);
    color: var(--muted); border-radius: 6px; padding: 5px 10px;
    font-size: 12px; cursor: pointer; z-index: 10; transition: all .15s;
  }}
  #clear-btn:hover {{ border-color: var(--accent); color: var(--accent); }}

  /* ── hints ── */
  #hints {{
    position: absolute; top: 16px; right: 16px;
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 10px 14px;
    font-size: 11px; color: var(--muted); line-height: 1.9; pointer-events: none; z-index: 9;
  }}
</style>
</head>
<body>

<div id="sidebar">
  <div id="sidebar-header">
    <h1>Call Graph Explorer</h1>
    <div id="search-wrap">
      <svg class="icon" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><line x1="16.5" y1="16.5" x2="22" y2="22"/></svg>
      <input id="search" type="text" placeholder="Search nodes…" autocomplete="off" spellcheck="false">
    </div>
    <div id="filters">
      <div class="pill active" data-type="all">All</div>
      <div class="pill hub"    data-type="hub">Hubs</div>
      <div class="pill"        data-type="popular">Popular</div>
      <div class="pill leaf"   data-type="leaf">Leaves</div>
      <div class="pill"        data-type="class" style="border-color:var(--cls-leaf)">Classes</div>
      <div class="pill"        data-type="function">Functions</div>
    </div>
    <div id="stats">
      <div class="stat"><div class="stat-num" id="st-nodes">0</div><div class="stat-lbl">Nodes</div></div>
      <div class="stat"><div class="stat-num" id="st-edges">0</div><div class="stat-lbl">Edges</div></div>
      <div class="stat"><div class="stat-num" id="st-shown">0</div><div class="stat-lbl">Shown</div></div>
    </div>
  </div>
  <div id="results-label">Nodes</div>
  <div id="results"></div>
</div>

<div id="canvas-wrap">
  <canvas id="c"></canvas>
</div>

<div id="detail">
  <h3 id="d-name"></h3>
  <div id="d-type-badge" style="font-size:11px;margin-bottom:8px;"></div>
  <div class="detail-row"><span class="detail-key">PageRank</span>   <span class="detail-val" id="d-pr"></span></div>
  <div class="detail-row"><span class="detail-key">Callers</span>    <span class="detail-val" id="d-callers"></span></div>
  <div class="detail-row"><span class="detail-key">Calls</span>      <span class="detail-val" id="d-calls"></span></div>
  <div class="detail-row"><span class="detail-key">In-degree</span>  <span class="detail-val" id="d-in"></span></div>
  <div class="detail-row"><span class="detail-key">Out-degree</span> <span class="detail-val" id="d-out"></span></div>
  <div class="detail-row" id="row-inherits"   style="display:none"><span class="detail-key">Inherits from</span><span class="detail-val" id="d-inherits"></span></div>
  <div class="detail-row" id="row-inherited"  style="display:none"><span class="detail-key">Inherited by</span> <span class="detail-val" id="d-inherited"></span></div>
  <div class="detail-row" id="row-imports"    style="display:none"><span class="detail-key">Imports</span>      <span class="detail-val" id="d-imports"></span></div>
  <div class="detail-row" id="row-importedby" style="display:none"><span class="detail-key">Imported by</span> <span class="detail-val" id="d-importedby"></span></div>
  <div class="detail-row" id="row-lines"      style="display:none"><span class="detail-key">Lines</span>       <span class="detail-val" id="d-lines"></span></div>
  <div id="detail-file"></div>
</div>

<div id="legend">
  <h4>Node Shape</h4>
  <div class="legend-row"><div class="leg-dot"     style="background:#7b8ab8"></div><span>Function (leaf)</span></div>
  <div class="legend-row"><div class="leg-dot"     style="background:#5bb8a0"></div><span>Function (popular)</span></div>
  <div class="legend-row"><div class="leg-dot"     style="background:#e07b54"></div><span>Function (hub)</span></div>
  <div class="legend-row"><div class="leg-diamond" style="background:#7c6fcd"></div><span>Class (leaf)</span></div>
  <div class="legend-row"><div class="leg-diamond" style="background:#a78bfa"></div><span>Class (popular)</span></div>
  <div class="legend-row"><div class="leg-diamond" style="background:#c47ed4"></div><span>Class (hub)</span></div>
  <h4 style="margin-top:10px">Edge Type</h4>
  <div class="legend-row"><div class="leg-line"   style="background:rgba(255,255,255,.35)"></div><span>Calls</span></div>
  <div class="legend-row"><div class="leg-dashed" style="border-color:#a78bfa"></div><span>Inherits</span></div>
  <div class="legend-row"><div class="leg-dotted" style="border-color:#5bb8a0"></div><span>Imports</span></div>
</div>

<div id="hints">Scroll to zoom &nbsp;·&nbsp; Drag to pan &nbsp;·&nbsp; Click node for details</div>
<button id="clear-btn">&#x2715; Clear selection</button>

<script>
// ═══════════════════════════════════════════════════════════════
// DATA
// ═══════════════════════════════════════════════════════════════
const graphData = {graph_json};

const HUB_COLOR  = "#e07b54", POP_COLOR  = "#5bb8a0", LEAF_COLOR  = "#7b8ab8";
const CHUB_COLOR = "#c47ed4", CPOP_COLOR = "#a78bfa", CLEAF_COLOR = "#7c6fcd";
const HL_COLOR   = "#f0d060";

// Pre-parse hex colors to [r,g,b] once for fast canvas use
function hexRGB(h) {{
  const v = parseInt(h.slice(1), 16);
  return [(v>>16)&255, (v>>8)&255, v&255];
}}
const COLOR_RGB = {{}};
[HUB_COLOR,POP_COLOR,LEAF_COLOR,CHUB_COLOR,CPOP_COLOR,CLEAF_COLOR,HL_COLOR].forEach(c => {{
  COLOR_RGB[c] = hexRGB(c);
}});

document.getElementById("st-nodes").textContent = graphData.nodes.length;
document.getElementById("st-edges").textContent = graphData.links.length;

// ═══════════════════════════════════════════════════════════════
// CANVAS SETUP
// ═══════════════════════════════════════════════════════════════
const wrap = document.getElementById("canvas-wrap");
const canvas = document.getElementById("c");
const ctx = canvas.getContext("2d");
let dpr = window.devicePixelRatio || 1;

function resizeCanvas() {{
  dpr = window.devicePixelRatio || 1;
  canvas.width  = wrap.clientWidth  * dpr;
  canvas.height = wrap.clientHeight * dpr;
  canvas.style.width  = wrap.clientWidth  + "px";
  canvas.style.height = wrap.clientHeight + "px";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  draw();
}}
window.addEventListener("resize", resizeCanvas);

const CW = () => wrap.clientWidth;
const CH = () => wrap.clientHeight;

// ═══════════════════════════════════════════════════════════════
// FORCE SIMULATION  (D3 still drives physics — no SVG involved)
// ═══════════════════════════════════════════════════════════════
const sim = d3.forceSimulation(graphData.nodes)
  .force("link",    d3.forceLink(graphData.links).id(d => d.id).distance(90).strength(0.35))
  .force("charge",  d3.forceManyBody().strength(-300))
  .force("center",  d3.forceCenter(CW()/2, CH()/2))
  .force("collide", d3.forceCollide().radius(d => d.size + 5))
  .alphaDecay(0.03)           // settle a little faster
  .velocityDecay(0.4);

sim.on("tick", draw);
sim.on("end",  draw);         // final repaint after cool-down

// ═══════════════════════════════════════════════════════════════
// ZOOM / PAN  (pure JS — no SVG transform)
// ═══════════════════════════════════════════════════════════════
let tx = 0, ty = 0, scale = 1;

const zoom = d3.zoom()
  .scaleExtent([0.05, 8])
  .on("zoom", ev => {{
    tx = ev.transform.x; ty = ev.transform.y; scale = ev.transform.k;
    draw();
  }});

d3.select(canvas).call(zoom);

// helper: canvas coords → world coords
function toWorld(cx, cy) {{
  return [(cx - tx) / scale, (cy - ty) / scale];
}}

// ═══════════════════════════════════════════════════════════════
// SELECTION / FILTER STATE
// ═══════════════════════════════════════════════════════════════
let selectedId   = null;
let activeFilter = "all";
let searchTerm   = "";
let connectedSet = new Set();
let connEdgeSet  = new Set();

function nodeType(d) {{
  if (d.color === HUB_COLOR  || d.color === CHUB_COLOR)  return "hub";
  if (d.color === POP_COLOR  || d.color === CPOP_COLOR)  return "popular";
  return "leaf";
}}

function matchesFilter(d) {{
  if (activeFilter === "all")      return true;
  if (activeFilter === "class")    return !!d.is_class;
  if (activeFilter === "function") return !d.is_class;
  return nodeType(d) === activeFilter;
}}
function matchesSearch(d) {{
  if (!searchTerm) return true;
  return d.label.toLowerCase().includes(searchTerm) || d.file.toLowerCase().includes(searchTerm);
}}
function isVisible(d) {{ return matchesFilter(d) && matchesSearch(d); }}

function computeConnected(nodeId) {{
  connectedSet = new Set([nodeId]);
  connEdgeSet  = new Set();
  graphData.links.forEach((l, i) => {{
    const s = l.source.id ?? l.source;
    const t = l.target.id ?? l.target;
    if (s === nodeId || t === nodeId) {{ connectedSet.add(s); connectedSet.add(t); connEdgeSet.add(i); }}
  }});
}}

// ═══════════════════════════════════════════════════════════════
// CANVAS DRAW  — one rAF-throttled repaint, zero DOM writes
// ═══════════════════════════════════════════════════════════════
let rafPending = false;
function draw() {{
  if (rafPending) return;
  rafPending = true;
  requestAnimationFrame(_draw);
}}

// Edge style helpers
const DASH_INHERITS = [6, 4];
const DASH_IMPORTS  = [2, 5];

function _draw() {{
  rafPending = false;
  ctx.clearRect(0, 0, CW(), CH());
  ctx.save();
  ctx.translate(tx, ty);
  ctx.scale(scale, scale);

  const hasSelection = selectedId !== null;

  // ── edges ──────────────────────────────────────────────
  graphData.links.forEach((l, i) => {{
    const s  = typeof l.source === "object" ? l.source : graphData.nodes[l.source];
    const t  = typeof l.target === "object" ? l.target : graphData.nodes[l.target];
    if (!s || !t) return;

    const sVis = isVisible(s), tVis = isVisible(t);
    if (!sVis || !tVis) return;

    const isHL    = hasSelection && connEdgeSet.has(i);
    const isDimmed = hasSelection && !connEdgeSet.has(i);

    ctx.beginPath();
    ctx.moveTo(s.x, s.y);
    ctx.lineTo(t.x, t.y);

    const rel = l.rel_type || "calls";
    if (isHL) {{
      ctx.strokeStyle = HL_COLOR;
      ctx.lineWidth   = 2.2 / scale;
      ctx.globalAlpha = 0.95;
      ctx.setLineDash([]);
    }} else if (isDimmed) {{
      ctx.strokeStyle = "rgba(255,255,255,0.04)";
      ctx.lineWidth   = 1 / scale;
      ctx.globalAlpha = 1;
      ctx.setLineDash([]);
    }} else if (rel === "inherits") {{
      ctx.strokeStyle = "rgba(167,139,250,0.55)";
      ctx.lineWidth   = 1.6 / scale;
      ctx.globalAlpha = 1;
      ctx.setLineDash(DASH_INHERITS);
    }} else if (rel === "imports") {{
      ctx.strokeStyle = "rgba(91,184,160,0.4)";
      ctx.lineWidth   = 1.3 / scale;
      ctx.globalAlpha = 1;
      ctx.setLineDash(DASH_IMPORTS);
    }} else {{
      ctx.strokeStyle = "rgba(255,255,255,0.12)";
      ctx.lineWidth   = 1 / scale;
      ctx.globalAlpha = 1;
      ctx.setLineDash([]);
    }}
    ctx.stroke();
  }});
  ctx.setLineDash([]);
  ctx.globalAlpha = 1;

  // ── nodes ──────────────────────────────────────────────
  const showLabels = scale > 0.45;

  graphData.nodes.forEach(d => {{
    if (!isVisible(d)) return;

    const dimmed   = hasSelection && !connectedSet.has(d.id);
    const selected = d.id === selectedId;
    const alpha    = dimmed ? 0.15 : 1;
    const r        = d.size;
    const [cr, cg, cb] = COLOR_RGB[d.color] || [120,138,184];

    ctx.globalAlpha = alpha;

    if (d.is_class) {{
      // diamond
      const s = r * 1.35;
      ctx.beginPath();
      ctx.moveTo(d.x,     d.y - s);
      ctx.lineTo(d.x + s, d.y    );
      ctx.lineTo(d.x,     d.y + s);
      ctx.lineTo(d.x - s, d.y    );
      ctx.closePath();
      ctx.fillStyle = `rgb(${{cr}},${{cg}},${{cb}})`;
      ctx.fill();
      ctx.strokeStyle = selected ? "#ffffff" : "rgba(255,255,255,0.3)";
      ctx.lineWidth   = selected ? 2.5 / scale : 1.5 / scale;
      ctx.stroke();
    }} else {{
      // circle
      ctx.beginPath();
      ctx.arc(d.x, d.y, r, 0, 2 * Math.PI);
      ctx.fillStyle = `rgb(${{cr}},${{cg}},${{cb}})`;
      ctx.fill();
      ctx.strokeStyle = selected ? "#ffffff" : "rgba(255,255,255,0.25)";
      ctx.lineWidth   = selected ? 2.5 / scale : 1.5 / scale;
      ctx.stroke();
    }}

    if (showLabels && !dimmed) {{
      const label = d.label.length > 22 ? d.label.slice(0,20) + "\u2026" : d.label;
      const fs    = Math.max(9, Math.min(12, 11 / scale));
      ctx.globalAlpha = 0.85;
      ctx.font        = `${{fs}}px -apple-system, BlinkMacSystemFont, sans-serif`;
      ctx.textAlign   = "center";
      ctx.textBaseline = "top";
      // shadow for readability
      ctx.strokeStyle = "#0f1117";
      ctx.lineWidth   = 3 / scale;
      ctx.strokeText(label, d.x, d.y + r + 3);
      ctx.fillStyle   = "#e8eaf0";
      ctx.fillText(label, d.x, d.y + r + 3);
    }}
  }});

  ctx.globalAlpha = 1;
  ctx.restore();
}}

// ═══════════════════════════════════════════════════════════════
// HIT TESTING  — pick node under pointer
// ═══════════════════════════════════════════════════════════════
function nodeAt(ex, ey) {{
  const [wx, wy] = toWorld(ex, ey);
  // iterate in reverse so top-rendered nodes are picked first
  for (let i = graphData.nodes.length - 1; i >= 0; i--) {{
    const d = graphData.nodes[i];
    if (!isVisible(d)) continue;
    const hit = d.is_class ? (Math.abs(wx - d.x) + Math.abs(wy - d.y)) < d.size * 1.35 * 1.1
                           : Math.hypot(wx - d.x, wy - d.y) < d.size;
    if (hit) return d;
  }}
  return null;
}}

// ═══════════════════════════════════════════════════════════════
// CLICK  (canvas, not SVG)
// ═══════════════════════════════════════════════════════════════
canvas.addEventListener("click", ev => {{
  const rect = canvas.getBoundingClientRect();
  const d = nodeAt(ev.clientX - rect.left, ev.clientY - rect.top);
  if (d) {{ ev.stopPropagation(); applySelection(d); }}
  else   {{ clearSelection(); }}
}});

// ═══════════════════════════════════════════════════════════════
// DRAG  (re-attach D3 drag to canvas element)
// ═══════════════════════════════════════════════════════════════
let dragNode = null;
d3.select(canvas).call(
  d3.drag()
    .filter(ev => ev.button === 0)
    .on("start", ev => {{
      const rect = canvas.getBoundingClientRect();
      dragNode = nodeAt(ev.sourceEvent.clientX - rect.left, ev.sourceEvent.clientY - rect.top);
      if (dragNode) {{ if (!ev.active) sim.alphaTarget(0.3).restart(); dragNode.fx = dragNode.x; dragNode.fy = dragNode.y; }}
    }})
    .on("drag", ev => {{
      if (!dragNode) return;
      const [wx, wy] = toWorld(ev.sourceEvent.clientX - canvas.getBoundingClientRect().left,
                               ev.sourceEvent.clientY - canvas.getBoundingClientRect().top);
      dragNode.fx = wx; dragNode.fy = wy;
    }})
    .on("end", ev => {{
      if (dragNode) {{ if (!ev.active) sim.alphaTarget(0); dragNode.fx = null; dragNode.fy = null; }}
      dragNode = null;
    }})
);

// ═══════════════════════════════════════════════════════════════
// SELECTION LOGIC
// ═══════════════════════════════════════════════════════════════
function applySelection(d) {{
  if (selectedId === d.id) {{ clearSelection(); return; }}
  selectedId = d.id;
  computeConnected(d.id);

  // detail panel
  document.getElementById("d-name").textContent    = d.label;
  document.getElementById("d-pr").textContent      = d.pagerank;
  document.getElementById("d-callers").textContent = d.callers;
  document.getElementById("d-calls").textContent   = d.calls;
  document.getElementById("d-in").textContent      = d.in_degree;
  document.getElementById("d-out").textContent     = d.out_degree;

  const badge = document.getElementById("d-type-badge");
  badge.innerHTML = `<span style="background:${{d.is_class?"rgba(124,111,205,.25)":"rgba(91,184,160,.15)"}};color:${{d.is_class?"#a78bfa":"#5bb8a0"}};padding:2px 8px;border-radius:10px;font-size:10px;text-transform:uppercase;letter-spacing:.4px">${{d.node_type||(d.is_class?"class":"function")}}</span>` +
    (d.language ? `<span style="margin-left:6px;color:#8890a8;font-size:11px">${{d.language}}</span>` : "");

  function showRow(rowId, valId, val) {{
    const show = val != null && val !== 0 && val !== "";
    document.getElementById(rowId).style.display = show ? "" : "none";
    if (show) document.getElementById(valId).textContent = val;
  }}
  showRow("row-inherits",   "d-inherits",   d.inherits_from);
  showRow("row-inherited",  "d-inherited",  d.inherited_by);
  showRow("row-imports",    "d-imports",    d.imports);
  showRow("row-importedby", "d-importedby", d.imported_by);
  if (d.start_line != null && d.end_line != null) {{
    document.getElementById("row-lines").style.display = "";
    document.getElementById("d-lines").textContent = `${{d.start_line}}–${{d.end_line}} (${{d.end_line - d.start_line + 1}} lines)`;
  }} else {{ document.getElementById("row-lines").style.display = "none"; }}

  document.getElementById("detail-file").textContent = (d.file && d.file !== "\u2014") ? d.file : "";
  document.getElementById("detail").classList.add("show");
  document.getElementById("clear-btn").style.display = "block";

  document.querySelectorAll(".result-item").forEach(el =>
    el.classList.toggle("selected", +el.dataset.id === d.id));
  draw();
}}

function clearSelection() {{
  selectedId = null; connectedSet.clear(); connEdgeSet.clear();
  document.getElementById("detail").classList.remove("show");
  document.getElementById("clear-btn").style.display = "none";
  document.querySelectorAll(".result-item").forEach(el => el.classList.remove("selected"));
  draw();
}}

document.getElementById("clear-btn").addEventListener("click", clearSelection);

// ═══════════════════════════════════════════════════════════════
// SIDEBAR LIST  (DOM-only, no canvas involvement)
// ═══════════════════════════════════════════════════════════════
function renderList() {{
  const results = document.getElementById("results");
  const label   = document.getElementById("results-label");
  const visible = graphData.nodes.filter(isVisible);
  document.getElementById("st-shown").textContent = visible.length;

  if (!visible.length) {{
    results.innerHTML = `<div style="padding:20px 8px;color:#8890a8;text-align:center;font-size:12px">No matches found</div>`;
    label.textContent = "Nodes"; return;
  }}

  label.textContent = `Nodes (${{visible.length}})`;
  const sorted = [...visible].sort((a,b) => {{
    const ta = nodeType(a), tb = nodeType(b);
    if (ta === "hub" && tb !== "hub") return -1;
    if (tb === "hub" && ta !== "hub") return  1;
    return b.pagerank - a.pagerank;
  }});

  results.innerHTML = sorted.map(d => {{
    const typeBadge = d.is_class
      ? `<span class="result-badge" style="background:rgba(124,111,205,.2);color:#a78bfa">class</span>`
      : `<span class="result-badge" style="background:rgba(91,184,160,.1);color:#8890a8">fn</span>`;
    const tierBadge = nodeType(d) === "hub"
      ? `<span class="result-badge" style="background:rgba(224,123,84,.2);color:#e07b54">hub</span>`
      : nodeType(d) === "popular"
      ? `<span class="result-badge" style="background:rgba(91,184,160,.2);color:#5bb8a0">pop</span>`
      : "";
    return `<div class="result-item${{d.id===selectedId?" selected":""}}" data-id="${{d.id}}" title="${{d.file}}">
      <div class="result-dot" style="background:${{d.color}};${{d.is_class?"border-radius:2px;transform:rotate(45deg);width:9px;height:9px":""}}"></div>
      <span class="result-name">${{d.label}}</span>
      ${{typeBadge}}${{tierBadge}}
    </div>`;
  }}).join("");

  results.querySelectorAll(".result-item").forEach(el => {{
    el.addEventListener("click", () => {{
      const d = graphData.nodes.find(n => n.id === +el.dataset.id);
      if (!d) return;
      applySelection(d);
      // pan canvas to centre the node
      const newTx = CW()/2 - scale * d.x;
      const newTy = CH()/2 - scale * d.y;
      d3.select(canvas).call(zoom.transform,
        d3.zoomIdentity.translate(newTx, newTy).scale(scale));
    }});
  }});
}}

// ═══════════════════════════════════════════════════════════════
// FILTER + SEARCH
// ═══════════════════════════════════════════════════════════════
function applyVisibility() {{
  if (selectedId !== null && !isVisible(graphData.nodes[selectedId])) clearSelection();
  renderList();
  draw();
}}

document.getElementById("search").addEventListener("input", ev => {{
  searchTerm = ev.target.value.toLowerCase().trim(); applyVisibility();
}});
document.querySelectorAll(".pill").forEach(pill => {{
  pill.addEventListener("click", () => {{
    document.querySelectorAll(".pill").forEach(p => p.classList.remove("active"));
    pill.classList.add("active"); activeFilter = pill.dataset.type; applyVisibility();
  }});
}});

// ═══════════════════════════════════════════════════════════════
// INIT
// ═══════════════════════════════════════════════════════════════
resizeCanvas();
renderList();
console.log(`Canvas renderer: ${{graphData.nodes.length}} nodes, ${{graphData.links.length}} edges`);
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