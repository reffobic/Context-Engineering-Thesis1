"""Per-file chunk selection for late-chunking parent context (pure, no I/O)."""

from __future__ import annotations


def _chunk_line_span(meta: dict) -> tuple[int, int]:
    """(start, end) line span of a chunk, tolerant of missing/garbled metadata."""
    try:
        start = int(meta.get("start_line", 0) or 0)
    except (TypeError, ValueError):
        start = 0
    try:
        end = int(meta.get("end_line", start) or start)
    except (TypeError, ValueError):
        end = start
    return start, max(start, end)


def _select_chunks_for_file(
    paired: list[tuple[str, dict]],
    retrieved_spans: list[tuple[int, int]],
    cap: int = 10,
) -> list[tuple[str, dict]]:
    """Pick at most ``cap`` chunks of one file, keeping those nearest the hits.

    ``paired``          all (document, metadata) chunks of the file.
    ``retrieved_spans`` (start, end) line spans of the chunks retrieval actually
                        returned for this file, best-ranked first.

    Rules:
      1. Files with <= ``cap`` chunks are returned whole (line order).
      2. The retrieved chunks themselves are always kept first (best-ranked
         first if they alone exceed ``cap``).
      3. Remaining slots go to the siblings closest, in line distance, to any
         retrieved chunk (ties: earlier in the file).
      4. The result is returned in line order so the stitched text still reads
         top-to-bottom.
    With no usable ``retrieved_spans`` this degrades to the old behaviour
    (first ``cap`` chunks in line order).
    """
    ordered = sorted(paired, key=lambda x: _chunk_line_span(x[1]))
    if cap <= 0:
        return []
    if len(ordered) <= cap:
        return ordered
    if not retrieved_spans:
        return ordered[:cap]

    span_to_idx: dict[tuple[int, int], int] = {}
    for i, (_doc, meta) in enumerate(ordered):
        span_to_idx.setdefault(_chunk_line_span(meta), i)

    chosen: list[int] = []
    for span in retrieved_spans:
        idx = span_to_idx.get(span)
        if idx is not None and idx not in chosen:
            chosen.append(idx)
        if len(chosen) >= cap:
            break
    if not chosen:
        return ordered[:cap]

    anchors = [_chunk_line_span(ordered[i][1]) for i in chosen]

    def distance(i: int) -> tuple[int, int]:
        s, e = _chunk_line_span(ordered[i][1])
        d = min(
            0 if (s <= ae and as_ <= e) else (as_ - e if as_ > e else s - ae)
            for as_, ae in anchors
        )
        return d, i

    taken = set(chosen)
    rest = sorted((i for i in range(len(ordered)) if i not in taken), key=distance)
    for i in rest:
        if len(chosen) >= cap:
            break
        chosen.append(i)

    return [ordered[i] for i in sorted(chosen)]
