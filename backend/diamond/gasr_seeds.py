"""Seed selection for GASR graph expand (precision: pruned, non-test)."""

from __future__ import annotations

from typing import Any


def chunk_file_path(item: dict[str, Any]) -> str:
    meta = item.get("metadata") or {}
    return str(
        item.get("path")
        or meta.get("file")
        or meta.get("file_path")
        or ""
    ).replace("\\", "/")


def is_test_path(path: str) -> bool:
    """True for tests/ trees and common test module names."""
    p = (path or "").replace("\\", "/").lower()
    if not p:
        return False
    if "/tests/" in f"/{p}/" or p.startswith("tests/"):
        return True
    if "/test_" in f"/{p}" or p.startswith("test_"):
        return True
    if p.endswith("_test.py") or p.endswith("_tests.py"):
        return True
    return False


def filter_non_test_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for c in chunks if not is_test_path(chunk_file_path(c))]


def build_gasr_seed_pool(
    pruned: list[dict[str, Any]],
    raw_retrieve: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Expand seeds for GASR: prefer pruned, non-test chunks.

    Falls back to non-test raw retrieve, then pruned (even if tests), then raw.
    """
    for pool in (
        filter_non_test_chunks(pruned),
        filter_non_test_chunks(raw_retrieve),
        list(pruned),
        list(raw_retrieve),
    ):
        if pool:
            return pool
    return []
