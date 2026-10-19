"""Embedding fingerprint: embed one fixed sentence on CPU and hash the rounded vector.

  python -m harness.fingerprint write   --profile P --index-config C --experiment E [--out F]
  python -m harness.fingerprint compare A.json B.json

The output file records model, model revision (HF snapshot hash), device, library versions
and the machine ID, so the files from the two PCs can be compared directly. Identical
hashes mean identical query embeddings at the rounding used; `compare` also prints the
largest absolute difference between the stored vectors.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path

from .common import load_toml


def _revision(model_name: str) -> str | None:
    try:
        from huggingface_hub import snapshot_download
        p = snapshot_download(f"sentence-transformers/{model_name}", local_files_only=True)
        return Path(p).name
    except Exception:
        return None


def fingerprint(model_name: str, text: str, decimals: int) -> dict:
    import numpy as np
    import torch
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_name, device="cpu")
    vec = model.encode([text])[0]                       # same call shape as the pipeline
    rounded = [round(float(x), decimals) for x in vec]
    blob = json.dumps(rounded).encode("utf-8")
    return {
        "model": model_name, "model_revision": _revision(model_name), "device": "cpu",
        "dim": len(rounded), "decimals": decimals, "text": text,
        "sha256_rounded": hashlib.sha256(blob).hexdigest(),
        "l2_norm": round(float(np.linalg.norm(vec)), 6),
        "torch": torch.__version__, "torch_threads": torch.get_num_threads(),
        "sentence_transformers": __import__("sentence_transformers").__version__,
        "numpy": np.__version__, "python": platform.python_version(),
        "vector_rounded": rounded,
    }


def compare(a: dict, b: dict) -> int:
    ok = True
    for k in ("model", "model_revision", "dim", "decimals", "text", "sha256_rounded"):
        same = a.get(k) == b.get(k)
        ok &= same
        print(f"[{'PASS' if same else 'FAIL'}] {k}: {a.get(k) if k != 'text' else '...'} | "
              f"{b.get(k) if k != 'text' else '...'}")
    if a["dim"] == b["dim"]:
        d = max(abs(x - y) for x, y in zip(a["vector_rounded"], b["vector_rounded"]))
        print(f"max abs difference between stored vectors: {d:.2e}")
    for k in ("machine_id", "torch", "torch_threads", "sentence_transformers", "numpy"):
        print(f"info {k}: {a.get(k)} | {b.get(k)}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write")
    w.add_argument("--profile", type=Path, required=True)
    w.add_argument("--index-config", type=Path, required=True)
    w.add_argument("--experiment", type=Path, required=True)
    w.add_argument("--out", type=Path, default=None)
    c = sub.add_parser("compare")
    c.add_argument("a", type=Path)
    c.add_argument("b", type=Path)
    a = ap.parse_args()
    if a.cmd == "compare":
        return compare(*(json.loads(p.read_text(encoding="utf-8")) for p in (a.a, a.b)))
    prof, icfg, exp = load_toml(a.profile), load_toml(a.index_config), load_toml(a.experiment)
    assert exp["embedding"]["query_device"] == "cpu"
    e = icfg["embedding"]
    fp = fingerprint(e["model"], e["fingerprint_text"], e["fingerprint_decimals"])
    fp["machine_id"] = prof["machine_id"]
    out = a.out or Path(prof["data_dir"]) / "fingerprints" / f"embedding-{prof['machine_id']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fp, indent=1), encoding="utf-8", newline="\n")
    print(f"model={fp['model']} revision={fp['model_revision']} device=cpu dim={fp['dim']}")
    print(f"sha256(rounded {fp['decimals']} dp) = {fp['sha256_rounded']}")
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
