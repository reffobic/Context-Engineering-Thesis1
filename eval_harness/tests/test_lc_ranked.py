
from harness.arms import _lc_file_block, _select_lc_chunks_by_rank


def make_chunk(path, text, symbol="example", language="python"):
    return {
        "path": path,
        "text": text,
        "symbol": symbol,
        "language": language,
    }


def estimate(block):
    return max(1, len(block) // 4)


def test_ranked_selection_preserves_retrieval_priority_and_file_chunks():
    chunks = [
        make_chunk("z_first.py", "first chunk"),
        make_chunk("z_first.py", "second chunk"),
        make_chunk("a_second.py", "second file"),
        make_chunk("m_third.py", "third file"),
    ]

    first = _lc_file_block(
        "z_first.py", chunks[:2]
    )
    second = _lc_file_block(
        "a_second.py", chunks[2:3]
    )

    budget = estimate(first) + estimate(second)

    selected = _select_lc_chunks_by_rank(chunks, max_tokens=budget)

    # Selection follows retrieval order, not alphabetical path order.
    assert [c["path"] for c in selected] == [
        "z_first.py",
        "z_first.py",
        "a_second.py",
    ]

    # Both chunks belonging to the first file are retained.
    assert [c["text"] for c in selected[:2]] == [
        "first chunk",
        "second chunk",
    ]


def test_ranked_selection_skips_nonfitting_file_and_continues():
    chunks = [
        make_chunk("first.py", "small first file"),
        make_chunk("second.py", "x" * 1000),
        make_chunk("third.py", "small third file"),
    ]

    first = _lc_file_block("first.py", chunks[:1])
    third = _lc_file_block("third.py", chunks[2:])

    budget = estimate(first) + estimate(third)

    selected = _select_lc_chunks_by_rank(chunks, max_tokens=budget)

    assert [c["path"] for c in selected] == [
        "first.py",
        "third.py",
    ]


def test_oversized_first_file_is_passed_alone():
    chunks = [
        make_chunk("large.py", "x" * 1000),
        make_chunk("small.py", "small file"),
    ]

    selected = _select_lc_chunks_by_rank(chunks, max_tokens=10)

    # The backend is responsible for its existing oversized-file truncation.
    assert [c["path"] for c in selected] == ["large.py"]
