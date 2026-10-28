"""Answer-level metric: LOCATIONS parsing, normalisation, Acc@k on the answer's list, failure class, conversion rate."""
from harness.answer_scoring import (FAIL_CLASS, answer_lists, conversion, normalise, parse_item, parse_locations,
                                    path_validity, score_answer)

GOLD = {"files": ["pkg/a.py", "pkg/b.py"], "modules": ["pkg/a.py:Foo"], "functions": ["pkg/a.py:Foo.run"]}


def test_plain_with_marker():
    t = "LOCATIONS\npkg/a.py::Foo.run\npkg/b.py\nEND_LOCATIONS\nThe fix is to change run()."
    info, lists = answer_lists(t)
    assert info["marker"]
    assert lists["files"] == ["pkg/a.py", "pkg/b.py"]
    assert lists["functions"] == ["pkg/a.py:Foo.run"] and lists["modules"] == ["pkg/a.py:Foo"]


def test_bullets_backticks_numbering_bold_and_reasons():
    t = ("## LOCATIONS:\n- `pkg/a.py::Foo.run`\n* **pkg/b.py**\n1. `./pkg/c.py`,\n2) \"pkg/d.py\" - because it parses\n"
         "• pkg/e.py (the caller)\nEND_LOCATIONS")
    _, lists = answer_lists(t)
    assert lists["files"] == ["pkg/a.py", "pkg/b.py", "pkg/c.py", "pkg/d.py", "pkg/e.py"]


def test_missing_marker_stops_at_first_non_path_line_and_is_not_truncated():
    t = "LOCATIONS\npkg/a.py\npkg/b.py\n\nThe issue is caused by a missing check.\nsrc/not_listed.py"
    info, lists = answer_lists(t)
    assert not info["marker"]
    assert lists["files"] == ["pkg/a.py", "pkg/b.py"]


def test_missing_marker_at_end_of_text_keeps_items():
    info, lists = answer_lists("LOCATIONS\npkg/a.py::Foo.run\npkg/b.py")
    assert not info["marker"] and lists["files"] == ["pkg/a.py", "pkg/b.py"]
    assert score_answer("LOCATIONS\npkg/a.py::Foo.run\npkg/b.py", GOLD)["answer.file_acc@3"] == 1.0


def test_truncated_means_done_reason_length_only():
    t = "LOCATIONS\npkg/a.py\npkg/b.py"                                  # no marker, as after a stop-sequence hit
    assert score_answer(t, GOLD, "stop")["truncated"] is False               # a stop-sequence hit is not truncation
    assert score_answer(t, GOLD, "length")["truncated"] is True
    assert score_answer(t, GOLD)["truncated"] is False and score_answer(t, GOLD, None)["truncated"] is False


def test_duplicates_keep_first_rank():
    t = "LOCATIONS\nother.py\npkg/a.py::Foo.run\nother.py\npkg/a.py\npkg/b.py\nEND_LOCATIONS"
    _, lists = answer_lists(t)
    assert lists["files"] == ["other.py", "pkg/a.py", "pkg/b.py"]
    assert lists["functions"] == ["pkg/a.py:Foo.run"]
    s = score_answer(t, GOLD)
    assert s["answer.file_acc@1"] == 0.0 and s["answer.file_acc@3"] == 1.0         # order is the rank; the duplicate does not push b.py down


def test_normalisation_follows_locmap_rules():
    assert normalise(".\\pkg\\a.py::Foo.__init__") | {} == {"file": "pkg/a.py", "module": "pkg/a.py:Foo", "function": "pkg/a.py:Foo"}
    assert normalise("/pkg/a.py::run") == {"file": "pkg/a.py", "module": "pkg/a.py:run", "function": "pkg/a.py:run"}
    assert normalise("pkg/a.py") == {"file": "pkg/a.py", "module": None, "function": None}


def test_no_section_and_no_valid_items_are_a_separate_failure_class_and_score_zero():
    for t, st in (("The fix is in pkg/a.py.", "no_section"), ("LOCATIONS\nEND_LOCATIONS\nnothing", "no_valid_items"),
                  ("LOCATIONS\nI could not determine this.", "no_valid_items"), (None, "no_section"), ("", "no_section")):
        s = score_answer(t, GOLD)
        assert s["status"] == st and s["failure_class"] == FAIL_CLASS
        assert all(v == 0.0 for k, v in s.items() if k.startswith("answer."))


def test_prose_words_and_end_marker_are_not_paths():
    assert parse_item("Explanation") is None and parse_item("END_LOCATIONS") is None
    assert parse_item("The fix is here") is None
    assert parse_item("- `setup.py`") == "setup.py" and parse_item("src/x/__init__.py") == "src/x/__init__.py"
    assert parse_locations("locations\na/b.py\nEND_LOCATIONS")["items"] == ["a/b.py"]


def test_function_level_acc():
    s = score_answer("LOCATIONS\npkg/a.py::Foo.run\npkg/b.py\nEND_LOCATIONS", GOLD)
    assert s["answer.function_acc@5"] == 1.0 and s["answer.module_acc@5"] == 1.0 and s["answer.file_acc@1"] == 1.0   # cap rule: 1 hit = min(#gold, 1)


def test_conversion_and_path_validity():
    rows = [{"context_has_all_gold_files": True, "answer_has_all_gold_files": True, "n_paths": 2, "n_paths_not_in_checkout": 0},
            {"context_has_all_gold_files": True, "answer_has_all_gold_files": False, "n_paths": 3, "n_paths_not_in_checkout": 1},
            {"context_has_all_gold_files": False, "answer_has_all_gold_files": True, "n_paths": 1, "n_paths_not_in_checkout": 1}]
    assert conversion(rows) == {"n_context_has_all_gold": 2, "n_also_in_answer": 1, "conversion_rate": 0.5}
    assert path_validity(rows) == {"n_paths": 6, "n_not_in_base_commit": 2, "share_not_in_base_commit": 2 / 6}
    assert conversion([])["conversion_rate"] is None
