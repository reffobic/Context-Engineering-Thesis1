import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness.smallfiles import do_import, export  # noqa: E402


def _mk(root: Path, text: str):
    d = root / "datasets" / "x"
    d.mkdir(parents=True)
    (d / "tasks.jsonl").write_text(text, encoding="utf-8")
    (d / "labels.jsonl").write_text("L", encoding="utf-8")
    (d / "manifest.json").write_text("{}", encoding="utf-8")
    (root / "samples").mkdir()
    (root / "samples" / "x.sample.json").write_text(json.dumps({"ids": [1]}), encoding="utf-8")


def test_export_import_roundtrip_and_no_overwrite(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _mk(src, "T")
    out = tmp_path / "exp"
    assert export(src, out) == 0
    assert export(src, out) == 2                      # refuses existing export dir
    assert do_import(dst, out) == 0                   # fresh machine
    assert do_import(dst, out) == 0                   # identical files are skipped
    (dst / "samples" / "x.sample.json").write_text("{}", encoding="utf-8")
    before = (dst / "datasets" / "x" / "tasks.jsonl").read_text()
    assert do_import(dst, out) == 2                   # differing existing file: refuse
    assert (dst / "samples" / "x.sample.json").read_text() == "{}"
    assert (dst / "datasets" / "x" / "tasks.jsonl").read_text() == before


def test_import_rejects_corrupt_export_before_writing(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _mk(src, "T")
    out = tmp_path / "exp"
    export(src, out)
    (out / "datasets" / "x" / "tasks.jsonl").write_text("tampered", encoding="utf-8")
    assert do_import(dst, out) == 2
    assert not dst.exists()
