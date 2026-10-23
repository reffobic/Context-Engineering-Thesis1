"""
Graphify adapter for GASR hop expand.

Loads Graphify ``graph.json`` (NetworkX node-link: ``links`` or ``edges``).
CallGraph dumps are not used for expand — hybrid indexing may still build
``call_graph.json`` for vector fuse / PageRank separately.

Node ids are normalized toward ``{rel_path}:{symbol}`` when possible.
EXTRACTED edges are preferred; INFERRED/AMBIGUOUS are off by default.
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

# Relation labels we treat as structural expand edges
DEFAULT_EDGE_TYPES = frozenset(
    {
        "calls",
        "call",
        "imports",
        "import",
        "inherits_from",
        "inherits",
        "inherit",
        "implements",
        "contain",
        "contains",
        "contained_in",
        "defines",
        "defined_in",
    }
)


def _env_path(name: str, default: Optional[Path] = None) -> Optional[Path]:
    raw = os.environ.get(name, "").strip()
    if raw:
        return Path(raw)
    return default


def default_graphify_paths(repo_root: Optional[Path] = None) -> list[Path]:
    """Candidate locations for Graphify graph.json."""
    roots: list[Path] = []
    env = _env_path("GRAPHIFY_OUT")
    if env:
        roots.append(env if env.name == "graph.json" else env / "graph.json")
        if env.is_dir():
            roots.append(env / "graph.json")
    if repo_root is None:
        # backend/indexing/this → backend → project root
        repo_root = Path(__file__).resolve().parent.parent.parent
    roots.extend(
        [
            repo_root / "graphify-out" / "graph.json",
            repo_root / "graphify_out" / "graph.json",
            Path(__file__).resolve().parent.parent / "chroma_db" / "graphify_graph.json",
        ]
    )
    return roots


def _norm_path(p: str) -> str:
    return (p or "").replace("\\", "/").lstrip("./")


def _clean_symbol(label: str) -> str:
    """Normalize Graphify labels like ``create_app()`` / ``.__call__()`` → bare names."""
    s = (label or "").strip()
    if s.startswith("."):
        s = s[1:]
    if s.endswith("()"):
        s = s[:-2]
    # drop trailing () already handled; also ``foo(a, b)`` → foo
    if "(" in s:
        s = s.split("(", 1)[0].strip()
    return s


def _node_key_from_graphify(node: dict[str, Any]) -> str:
    """Build a join key ``path:symbol`` when possible; else use id."""
    nid = str(node.get("id") or "")
    label = _clean_symbol(str(node.get("label") or node.get("name") or ""))
    src = _norm_path(str(node.get("source_file") or node.get("file") or ""))
    if src and label:
        return f"{src}:{label}"
    if ":" in nid and "/" in nid.split(":", 1)[0]:
        return nid.replace("\\", "/")
    return nid or f"{src}:{label}"


def _edge_list(data: dict[str, Any]) -> list[dict[str, Any]]:
    if "links" in data and isinstance(data["links"], list):
        return data["links"]
    if "edges" in data and isinstance(data["edges"], list):
        return data["edges"]
    return []


def _relation_ok(rel: str, allowed: frozenset[str]) -> bool:
    r = (rel or "").strip().lower().replace(" ", "_")
    if not allowed:
        return True
    if r in allowed:
        return True
    # fuzzy: "calls function" → calls
    return any(r == a or r.startswith(a + "_") or a in r.split("_") for a in allowed)


class CodeGraphAdapter:
    """In-memory adjacency for hop expand (Graphify only)."""

    def __init__(self) -> None:
        self.backend: str = "empty"
        self.nodes: dict[str, dict[str, Any]] = {}  # key → attrs
        self.adj: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        # (neighbor_key, relation, confidence)
        self._id_alias: dict[str, str] = {}  # raw id → key

    @classmethod
    def load(
        cls,
        graphify_path: Optional[Path] = None,
        call_graph_path: Optional[Path] = None,  # retained for API compat; ignored
        repo_root: Optional[Path] = None,
        extracted_only: bool = True,
        edge_types: Optional[Iterable[str]] = None,
    ) -> "CodeGraphAdapter":
        del call_graph_path  # GASR expand is Graphify-only (scope A)
        adapter = cls()
        allowed = frozenset(e.lower() for e in edge_types) if edge_types else DEFAULT_EDGE_TYPES

        paths = [graphify_path] if graphify_path else default_graphify_paths(repo_root)
        for p in paths:
            if not p or not p.exists():
                continue
            try:
                peek = json.loads(p.read_text(encoding="utf-8"))
                nodes_peek = peek.get("nodes")
                # Misplaced CallGraph dump (nodes: dict) — skip; do not use for expand.
                if isinstance(nodes_peek, dict):
                    logger.warning(
                        "CodeGraphAdapter: skipping CallGraph-shaped dump at %s "
                        "(GASR expand requires Graphify node-link JSON)",
                        p,
                    )
                    continue
                if not isinstance(nodes_peek, list):
                    logger.warning(
                        "CodeGraphAdapter: skipping %s (nodes is not a list)",
                        p,
                    )
                    continue
                adapter._load_graphify_data(
                    peek, extracted_only=extracted_only, allowed=allowed
                )
                adapter.backend = "graphify"
                logger.info(
                    "CodeGraphAdapter: loaded Graphify from %s (%d nodes)",
                    p,
                    len(adapter.nodes),
                )
                return adapter
            except Exception as exc:
                logger.warning("CodeGraphAdapter: failed Graphify load %s: %s", p, exc)

        logger.warning(
            "CodeGraphAdapter: no Graphify graph.json found "
            "(run `graphify update .` → graphify-out/graph.json)"
        )
        adapter.backend = "empty"
        return adapter

    def _register_node(self, key: str, attrs: dict[str, Any], raw_id: Optional[str] = None) -> str:
        # Always store forward-slash keys so seeds join Graphify ids.
        key = _norm_path(key) if ":" not in key else key.replace("\\", "/")
        if ":" in key:
            path_part, sym = key.split(":", 1)
            key = f"{_norm_path(path_part)}:{sym}"
        if attrs.get("file"):
            attrs = {**attrs, "file": _norm_path(str(attrs["file"]))}
        if key not in self.nodes:
            self.nodes[key] = attrs
        if raw_id:
            raw_s = str(raw_id)
            self._id_alias[raw_s] = key
            self._id_alias[raw_s.replace("\\", "/")] = key
            self._id_alias[raw_s.replace("/", "\\")] = key
        self._id_alias[key] = key
        self._id_alias[key.replace("/", "\\")] = key
        return key

    def _resolve(self, raw: str) -> Optional[str]:
        if not raw:
            return None
        if raw in self._id_alias:
            return self._id_alias[raw]
        if raw in self.nodes:
            return raw
        norm = raw.replace("\\", "/")
        if norm in self._id_alias:
            return self._id_alias[norm]
        if norm in self.nodes:
            return norm
        back = raw.replace("/", "\\")
        if back in self._id_alias:
            return self._id_alias[back]
        return None

    def _add_edge(self, src: str, tgt: str, relation: str, confidence: str) -> None:
        self.adj[src].append((tgt, relation, confidence))
        # undirected expand for callers: also reverse for calls/imports
        self.adj[tgt].append((src, f"rev_{relation}", confidence))

    def _load_graphify(
        self,
        path: Path,
        *,
        extracted_only: bool,
        allowed: frozenset[str],
    ) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        self._load_graphify_data(data, extracted_only=extracted_only, allowed=allowed)

    def _load_graphify_data(
        self,
        data: dict[str, Any],
        *,
        extracted_only: bool,
        allowed: frozenset[str],
    ) -> None:
        prefix = os.environ.get("GRAPHIFY_PATH_PREFIX", "superset/")
        for node in data.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            raw_id = str(node.get("id") or "")
            key = _node_key_from_graphify(node)
            label = _clean_symbol(str(node.get("label") or node.get("name") or ""))
            src_file = _norm_path(str(node.get("source_file") or node.get("file") or ""))
            # Prefixed path used by our indexer (package under ``superset/``).
            if prefix and src_file and not src_file.startswith(prefix):
                prefixed = f"{prefix}{src_file}"
            else:
                prefixed = src_file
            self._register_node(
                key,
                {
                    "id": key,
                    "raw_id": raw_id,
                    "label": label,
                    "file": prefixed or src_file,
                    "file_type": node.get("file_type"),
                },
                raw_id=raw_id,
            )
            # Aliases so hybrid seeds ``superset/app.py:create_app`` resolve.
            if prefixed and label:
                self._id_alias[f"{prefixed}:{label}"] = key
            if src_file and label and src_file != prefixed:
                self._id_alias[f"{src_file}:{label}"] = key
            base = (src_file or prefixed or "").split("/")[-1]
            if base and label:
                self._id_alias[f"{base}:{label}"] = key

        for link in _edge_list(data):
            if not isinstance(link, dict):
                continue
            conf = str(link.get("confidence") or "EXTRACTED").upper()
            if extracted_only and conf not in ("EXTRACTED", ""):
                continue
            rel = str(link.get("relation") or link.get("type") or "calls")
            if not _relation_ok(rel, allowed):
                continue
            s_raw = str(link.get("source") or "")
            t_raw = str(link.get("target") or "")
            s = self._resolve(s_raw) or self._register_node(
                s_raw, {"id": s_raw, "label": s_raw, "file": ""}, raw_id=s_raw
            )
            t = self._resolve(t_raw) or self._register_node(
                t_raw, {"id": t_raw, "label": t_raw, "file": ""}, raw_id=t_raw
            )
            self._add_edge(s, t, rel.lower(), conf or "EXTRACTED")

    def seed_key_from_chunk(self, chunk: dict[str, Any]) -> Optional[str]:
        meta = chunk.get("metadata") or {}
        path = _norm_path(
            str(
                meta.get("file")
                or meta.get("file_path")
                or chunk.get("path")
                or ""
            )
        )
        name = str(
            meta.get("function")
            or meta.get("name")
            or chunk.get("symbol")
            or ""
        ).strip()
        if path and name:
            key = f"{path}:{name}"
            if self._resolve(key):
                return self._resolve(key)
            # basename match
            base = path.split("/")[-1]
            alt = f"{base}:{name}"
            if self._resolve(alt):
                return self._resolve(alt)
            for nid, attrs in self.nodes.items():
                if attrs.get("label") == name and (
                    attrs.get("file") == path
                    or str(attrs.get("file", "")).endswith(path)
                    or path.endswith(str(attrs.get("file", "")))
                ):
                    return nid
            # soft key so expand can still try
            return key
        return None

    def neighbors(
        self,
        seed_ids: list[str],
        hops: int = 1,
        edge_types: Optional[Iterable[str]] = None,
        extracted_only: bool = True,
    ) -> list[str]:
        """
        BFS up to ``hops`` from seeds. Returns neighbor node keys (excluding seeds).
        """
        if hops < 1 or not seed_ids:
            return []
        allowed = frozenset(e.lower() for e in edge_types) if edge_types else None
        start = [self._resolve(s) or s for s in seed_ids]
        start = [s for s in start if s]
        seen = set(start)
        frontier = deque((s, 0) for s in start)
        found: list[str] = []

        while frontier:
            node, dist = frontier.popleft()
            if dist >= hops:
                continue
            for nbr, rel, conf in self.adj.get(node, []):
                if extracted_only and conf not in ("EXTRACTED", ""):
                    if not str(conf).upper().startswith("EXTRACT"):
                        continue
                if allowed is not None:
                    base_rel = rel[4:] if rel.startswith("rev_") else rel
                    if not _relation_ok(base_rel, allowed):
                        continue
                if nbr in seen:
                    continue
                seen.add(nbr)
                found.append(nbr)
                frontier.append((nbr, dist + 1))

        return found

    def node_attrs(self, node_id: str) -> dict[str, Any]:
        key = self._resolve(node_id) or node_id
        return dict(self.nodes.get(key) or {"id": key, "label": key, "file": ""})


# Module-level cache
_ADAPTER: Optional[CodeGraphAdapter] = None


def get_adapter(reload: bool = False, **kwargs: Any) -> CodeGraphAdapter:
    global _ADAPTER
    if _ADAPTER is None or reload:
        _ADAPTER = CodeGraphAdapter.load(**kwargs)
    return _ADAPTER
