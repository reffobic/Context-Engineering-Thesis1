"""
Graphify visualization helper for the demo UI.

Locates Graphify HTML/SVG under GRAPHIFY_OUT / graphify-out and optionally
copies or links it next to the MCP static folder. Falls back to a short
status page when artifacts are missing.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("graphify_viz")

_BACKEND = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND.parent
_STATIC = _BACKEND / "mcp_server" / "static"


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    env = os.environ.get("GRAPHIFY_OUT", "").strip()
    if env:
        p = Path(env)
        dirs.append(p if p.is_dir() else p.parent)
    dirs.extend(
        [
            _REPO_ROOT / "graphify-out",
            _REPO_ROOT / "graphify_out",
            _BACKEND / "chroma_db" / "graphify-out",
        ]
    )
    return dirs


def find_graphify_artifacts() -> dict[str, Optional[Path]]:
    """Return paths to graph.json and any html/svg viz files."""
    found: dict[str, Optional[Path]] = {
        "graph_json": None,
        "html": None,
        "svg": None,
        "dir": None,
    }
    for d in _candidate_dirs():
        if not d.exists():
            continue
        gj = d / "graph.json"
        if gj.exists() and found["graph_json"] is None:
            found["graph_json"] = gj
            found["dir"] = d
        for pattern in ("*.html", "**/*.html"):
            for html in d.glob(pattern):
                if html.is_file():
                    found["html"] = html
                    found["dir"] = d
                    break
            if found["html"]:
                break
        for pattern in ("*.svg", "**/*.svg"):
            for svg in d.glob(pattern):
                if svg.is_file():
                    found["svg"] = svg
                    break
            if found["svg"]:
                break
        if found["graph_json"] or found["html"]:
            break
    return found


def visualize_graphify(output_format: str = "html") -> dict[str, Any]:
    """
    Expose Graphify viz for the UI.

    Copies the first HTML (or SVG) into ``mcp_server/static/graphify_view.html``
    when present; otherwise writes a small status HTML explaining how to run Graphify.
    """
    arts = find_graphify_artifacts()
    _STATIC.mkdir(parents=True, exist_ok=True)
    out_html = _STATIC / "graphify_view.html"

    src = arts.get("html")
    if output_format == "svg" and arts.get("svg"):
        src = arts["svg"]

    if src and src.exists():
        try:
            if src.suffix.lower() == ".html":
                shutil.copy2(src, out_html)
            else:
                # wrap SVG
                svg_text = src.read_text(encoding="utf-8", errors="replace")
                out_html.write_text(
                    "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                    "<title>Graphify</title></head><body>"
                    f"{svg_text}</body></html>",
                    encoding="utf-8",
                )
            rel = "static/graphify_view.html"
            return {
                "ok": True,
                "backend": "graphify",
                "source": str(src),
                "graph_json": str(arts["graph_json"]) if arts.get("graph_json") else None,
                "output_path": str(out_html),
                "url_path": f"/graphify-view",
                "relative": rel,
                "message": "Graphify visualization ready.",
            }
        except OSError as exc:
            logger.warning("visualize_graphify copy failed: %s", exc)

    # Status page when missing
    graph_json = arts.get("graph_json")
    status = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Graphify</title>
<style>
 body {{ font-family: system-ui, sans-serif; max-width: 40rem; margin: 2rem auto; padding: 0 1rem; }}
 code {{ background: #f4f4f4; padding: 0.1rem 0.3rem; }}
</style></head><body>
<h1>Graphify visualization</h1>
<p>No Graphify HTML/SVG was found under <code>graphify-out/</code> or <code>GRAPHIFY_OUT</code>.</p>
<p>{"Found graph.json at: <code>" + str(graph_json) + "</code>" if graph_json else "graph.json not found yet."}</p>
<ol>
<li>Install: <code>pip install graphifyy</code> (or <code>uv tool install graphifyy</code>)</li>
<li>From the indexed package/repo: <code>graphify update . --force</code></li>
<li>Copy or set <code>GRAPHIFY_OUT</code> to that <code>graphify-out/</code> folder</li>
<li>Reload this page (serves Graphify&apos;s native <code>graph.html</code>)</li>
</ol>
<p>Open <code>graphify-out/graph.html</code> directly in a browser if preferred.</p>
</body></html>
"""
    out_html.write_text(status, encoding="utf-8")
    return {
        "ok": bool(graph_json),
        "backend": "graphify",
        "source": None,
        "graph_json": str(graph_json) if graph_json else None,
        "output_path": str(out_html),
        "url_path": "/graphify-view",
        "message": "Graphify HTML missing; wrote setup instructions page.",
    }
