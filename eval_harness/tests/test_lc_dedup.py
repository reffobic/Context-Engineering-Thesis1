from harness.arms import _build_lc_chunks


class DummyServer:
    def reconstruct_file_context(self, chunks):
        raise AssertionError("Reconstruction should not be needed when parent_context exists")


def make_chunk(path, text):
    return {
        "parent_context": text,
        "metadata": {
            "file": path,
            "function": "example",
            "language": "python",
        },
    }


def test_lc_dedup_keeps_first_occurrence_and_preserves_original_behavior():
    chunks = [
        make_chunk("src/alpha.py", "First version of alpha"),
        make_chunk("src/alpha.py", "Second version of alpha"),
        make_chunk("src/beta.py", "Content of beta"),
    ]

    server = DummyServer()

    # Original behavior: repeated parent files are retained.
    original = _build_lc_chunks(server, chunks)
    assert len(original) == 3
    assert original[0]["text"] == "First version of alpha"
    assert original[1]["text"] == "Second version of alpha"

    # New behavior: only the first occurrence of each file is retained.
    deduplicated = _build_lc_chunks(server, chunks, deduplicate=True)
    assert len(deduplicated) == 2
    assert [item["path"] for item in deduplicated] == [
        "src/alpha.py",
        "src/beta.py",
    ]
    assert deduplicated[0]["text"] == "First version of alpha"
    assert deduplicated[1]["text"] == "Content of beta"
