"""Export/import the small shared files (tasks, labels, manifests, sample files) with checksums.

  python -m harness.smallfiles export --profile P --out <new-dir>
  python -m harness.smallfiles import --profile P --src <export-dir>

export  refuses to write into an existing directory.
import  verifies every checksum first, then refuses to overwrite any existing file whose
        checksum differs (it aborts before writing anything). Identical files are skipped.
        There is no force option. Sample files are only ever copied, never regenerated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .common import load_toml

SHARED = ["tasks.jsonl", "labels.jsonl", "manifest.json"]
SUMS = "SHA256SUMS.json"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def shared_files(data_dir: Path) -> list[str]:
    rels = []
    for d in sorted((data_dir / "datasets").iterdir()):
        rels += [f"datasets/{d.name}/{n}" for n in SHARED if (d / n).is_file()]
    rels += [f"samples/{p.name}" for p in sorted((data_dir / "samples").glob("*.sample.json"))]
    return rels


def export(data_dir: Path, out: Path) -> int:
    if out.exists():
        print(f"[FAIL] {out} already exists; refusing to write into it.")
        return 2
    rels = shared_files(data_dir)
    files = {}
    for rel in rels:
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(data_dir / rel, dst)
        files[rel] = {"sha256": sha256_file(dst), "bytes": dst.stat().st_size}
    (out / SUMS).write_text(json.dumps(
        {"created_utc": datetime.now(timezone.utc).isoformat(), "files": files}, indent=1),
        encoding="utf-8", newline="\n")
    print(f"[PASS] exported {len(files)} files to {out}")
    for rel, m in files.items():
        print(f"  {m['sha256'][:16]}  {rel}")
    return 0


def do_import(data_dir: Path, src: Path) -> int:
    sums = json.loads((src / SUMS).read_text(encoding="utf-8"))["files"]
    bad = [r for r, m in sums.items() if not (src / r).is_file() or sha256_file(src / r) != m["sha256"]]
    if bad:
        print(f"[FAIL] checksum mismatch in the export itself: {bad}. Nothing written.")
        return 2
    conflicts, to_copy, same = [], [], []
    for rel, m in sums.items():
        dst = data_dir / rel
        if not dst.exists():
            to_copy.append(rel)
        elif sha256_file(dst) == m["sha256"]:
            same.append(rel)
        else:
            conflicts.append(rel)
    if conflicts:
        print("[FAIL] refusing to overwrite existing files with different content:")
        for r in conflicts:
            print(f"  {data_dir / r}")
        print("Nothing written.")
        return 2
    for rel in to_copy:
        dst = data_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / rel, dst)
        if sha256_file(dst) != sums[rel]["sha256"]:
            print(f"[FAIL] copy of {rel} does not verify")
            return 2
    print(f"[PASS] imported {len(to_copy)} files, {len(same)} already identical, 0 overwritten")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--profile", type=Path, required=True)
    e.add_argument("--out", type=Path, required=True)
    i = sub.add_parser("import")
    i.add_argument("--profile", type=Path, required=True)
    i.add_argument("--src", type=Path, required=True)
    a = ap.parse_args()
    data_dir = Path(load_toml(a.profile)["data_dir"])
    return export(data_dir, a.out) if a.cmd == "export" else do_import(data_dir, a.src)


if __name__ == "__main__":
    raise SystemExit(main())
