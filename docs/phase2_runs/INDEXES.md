# Phase 2 Wave 1 indexes

**Pipeline freeze:** `a2cfcec312626b4b50f743c4af91a6d118c79d0e` (`pipeline_phase2.toml`)  
**Graphify:** `graphify 0.9.65`  
**Index config:** `eval_harness/configs/index.toml` (embedding `all-MiniLM-L6-v2`, `index_device=cuda`)  
**Built:** 2026-11-14 (~134s wall for 3 repos)

## Exact CLI used

```bash
cd /home/g7/Documents/HODA/Context-Engineering-Thesis1
export PATH="$HOME/.local/bin:$PATH"
export PYTHONPATH=eval_harness/src
unset GAPA_COMPACT_PROMPT GAPA_NEED GAPA_GRAPHMETA GAPA_META INDEX_CONTEXT_OLLAMA \
      GASR_OFFLINE RERANKER_ENABLED PREROUTE_DEBUG OLLAMA_SKIP

# 1) Materialize tasks + checkouts (full SHAs)
python3 -m harness.phase2_datasets \
  --profile eval_harness/profiles/ubuntu-4080.toml \
  --config eval_harness/configs/datasets_phase2.toml \
  --only swe-qa-w1 --resolve-commits

# 2) Build indexes (Chroma + call_graph.json + graphify-out/graph.json)
python3 -m harness.index_cache build \
  --profile eval_harness/profiles/ubuntu-4080.toml \
  --experiment eval_harness/configs/experiment.toml \
  --pipeline-config eval_harness/configs/pipeline_phase2.toml \
  --index-config eval_harness/configs/index.toml \
  --dataset swe-qa-w1
```

## Index paths (under `eval_harness/data/cache/indexes/`)

| Repo | Commit (12) | Index dir suffix (key) | chunks | call_graph | graphify nodes |
|------|-------------|------------------------|--------|------------|----------------|
| pallets/flask | `85c5d93cbd04` | `bd88183c77c06c83` | 1605 | yes | 2106 |
| psf/requests | `46e939b5525d` | `a4c65b54170e168c` | 784 | yes | 1195 |
| pytest-dev/pytest | `5989efe3efec` | `f1134c929218c0d9` | 6483 | yes | 8726 |

Full dir names:

- `pallets__flask__85c5d93cbd04__bd88183c77c06c83`
- `psf__requests__46e939b5525d__a4c65b54170e168c`
- `pytest-dev__pytest__5989efe3efec__f1134c929218c0d9`

Each contains `DONE`, `key.json`, `build_report.json`, `chroma/call_graph.json`, `graphify-out/graph.json`.

## Checkout paths

`eval_harness/data/cache/checkouts/{owner__repo}/{full_sha}/` — SHAs match FREEZE.md / GitHub API expansion of `repo_commit.txt` tips.
