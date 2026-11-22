# Phase 2 freeze — Wave 1 / budgeted LC

**Stamp (UTC):** 2026-11-22T18:40:00Z (Fix-4 neighbour packing + budgeted-LC)  
**Branch:** `phase2-eval-haya`  
**Pipeline frozen_sha:** `3ac10665c16f07ae58ee3fbb9ce3e67874f07c19` (budgeted LC; previous `0c618fda…`)  
**Phase 2 pipeline config:** `eval_harness/configs/pipeline_phase2.toml`  
**Always-LC:** ranked parent-file packing under **`LC_TOKEN_BUDGET=8000`** (inject safety `MAX_CONTEXT_TOKENS=24000`). Prior full-dump LC runs remain ablation / archive only.

## Models

| Role | Tag | Notes |
|------|-----|--------|
| **Generator** | `qwen3.5:9b` | Locked for all Wave 1 arms; quality×speed balance; Phase 1 continuity |
| **Assessor / gate** | `qwen3.5:9b` (`ASSESSOR_MODEL=@model`) | Same as generator for Phase 1 continuity; `qwen3.5:4b` reserved for later gate-speed ablations only |
| **SWE-QA judge** | `qwen-7B:latest` via Ollama | **≠ generator.** OpenAI-compatible `http://127.0.0.1:11434/v1`. Port: [`scripts/phase2_sweqa_ollama_judge.py`](../../scripts/phase2_sweqa_ollama_judge.py). Tables: **`local-judge` · `single-judge`**. (Prior partial run with `qwen3.8:27b` archived under `phase2_sweqa_scores_qwen38_27b/`.) |
| **RepoProbe scorer** | `qwen-7B:latest` via Ollama | `SCORING_MODEL=qwen-7B:latest` + `OPENAI_SCORING_BASE_URL=http://127.0.0.1:11434/v1` (see `judge_ollama.env`) |

**Runtime knobs:** `OLLAMA_THINK=0`, `OLLAMA_NUM_PREDICT=8192` (default), `token_budget=1300`, `OLLAMA_NUM_CTX=32768`, **`LC_TOKEN_BUDGET=8000`**, `MAX_CONTEXT_TOKENS=24000`, **`GASR_NEIGHBOR_BUDGET_FRACTION=0.55`**.

**Judge env file:** [`docs/phase2_runs/judge_ollama.env`](judge_ollama.env) — `set -a && source docs/phase2_runs/judge_ollama.env && set +a` before CP5/CP9.

**GPU note:** Wave 1 generation finished before CP5. Judge switched from `qwen3.8:27b` → **`qwen-7B:latest`** for speed (2026-11-15).

## Scaffolding

- RepoProbe: **scorer-on-pipeline** (host 7-arm answers → `scorer.py` with local Ollama scoring). Not head-to-head with paper GPT-5.2 Docker scaffolding.
- SWE-QA: our 7-arm pipeline answers → **local Ollama port** of upstream `llm-as-a-judge.py` protocol (same dims /100).

## Hardware

- GPU: NVIDIA GeForce RTX 4080, 16376 MiB, driver 595.99.02
- Ollama: reachable at `http://127.0.0.1:11434` with `qwen3.5:9b`, `qwen3.5:4b`, `qwen3.8:27b` installed

## Upstream clones

| Suite | Path |
|-------|------|
| SWE-QA-Bench | `/home/g7/Documents/HODA/external/SWE-QA-Bench` (= `../external/SWE-QA-Bench` from thesis root) |
| RepoProbe | `/home/g7/Documents/HODA/external/RepoProbe` |

### SWE-QA Wave 1 SHAs (`repo_commit.txt`)

| Repo | GitHub | Commit |
|------|--------|--------|
| flask | https://github.com/pallets/flask | `85c5d93` |
| requests | https://github.com/psf/requests | `46e939b` |
| pytest | https://github.com/pytest-dev/pytest | `5989efe` |

**JSONL line counts:** flask=48, requests=48, pytest=48 → **144** tasks (`Benchmark/{flask,requests,pytest}.jsonl`).

### SWE-QA rest (12 repos beyond Wave 1) → full n=720

**Dataset:** `swe-qa-rest` · **576** Q · gen/judge same as Wave 1 (`qwen3.5:9b` / `qwen-7B:latest`).  
**Full SHAs:** [`sweqa_rest_commits.json`](sweqa_rest_commits.json) · indexes: [`INDEXES_REST.md`](INDEXES_REST.md).  
**Tables:** [`TABLE_B_SWEQA_REST.md`](TABLE_B_SWEQA_REST.md) · [`TABLE_B_SWEQA_FULL.md`](TABLE_B_SWEQA_FULL.md) (W1∪rest).

| Repo | GitHub | Commit (short) |
|------|--------|----------------|
| astropy | astropy/astropy | `0a041d3` |
| conan | conan-io/conan | `52f43d9` |
| django | django/django | `14fc2e9` |
| matplotlib | matplotlib/matplotlib | `a5e1f60` |
| pylint | pylint-dev/pylint | `44740e5` |
| reflex | reflex-dev/reflex | `fe0f946` |
| scikit-learn | scikit-learn/scikit-learn | `adb1ae7` |
| sphinx | sphinx-doc/sphinx | `6c9e320` |
| sqlfluff | sqlfluff/sqlfluff | `db9801b` |
| streamlink | streamlink/streamlink | `ab1f365` |
| sympy | sympy/sympy | `3c817ed` |
| xarray | pydata/xarray | `40119bf` |

**Note:** django Graphify hung; index used call_graph→graph.json fallback (`GRAPHIFY_TIMEOUT_S` in `index_worker.py`).

### RepoProbe Python-8 (`repos_info.json` snapshot.git.head)

| Short name | Full name | Head |
|------------|-----------|------|
| FieldStation42 | shane-mason/FieldStation42 | `b2032732317f0fe4e9318d6764c7afcd17bd9c01` |
| adk-python | google/adk-python | `56775afc48ee54e9cbea441a6e0fa6c8a12891b9` |
| agent-framework | microsoft/agent-framework | `8b4f7d5e298868ee945900eb7a3bd887c89afff9` |
| browser-use | browser-use/browser-use | `310bc12c3eb412c967ef24f9e94a48adff84aa45` |
| crawl4ai | unclecode/crawl4ai | `aa7b05072d5b6deb9d1b85f9bb8b8561b936ac5e` |
| docling | docling-project/docling | `be085c0e39dd5c51572b883d0f795c5a7abefd5d` |
| sglang | sgl-project/sglang | `7cf0551014953c89738504803efb0b30f491f4ed` |
| yasb | amnweb/yasb | `f2f402d6d51cdc3c52c0017ec9c014e270f09174` |

CSV **question** counts (DictReader; checklists are multiline so raw line counts are higher): FieldStation42=14, adk-python=30, agent-framework=9, browser-use=5, crawl4ai=18, docling=10, sglang=11, yasb=11 → **Σ 108**.

## Smoke task (CP3)

- Suite: SWE-QA Wave 1
- `task_id` / `instance_id`: `flask-000` (first flask JSONL line)
- Out: `eval_harness/data/runs/phase2-smoke/`

## Do not use for Wave 1 generator

- `qwen3.8:27b` — too slow for ~1008 generations
- `qwen3.5:4b` — gate-only, not answer model
- `qwen2.5-coder:7b` — breaks freeze vs Phase 1
