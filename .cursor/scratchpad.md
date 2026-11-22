# Scratchpad

## Background and Motivation

### Active (Planner 2026-11-15 evening): SWE-QA rest → full 720 (gen `qwen3.5:9b` · judge `qwen-7B`)

**Human ask:** Run the **rest of SWE-QA** (repos beyond Wave 1) with the same generator/judge as Wave 1 so we can report **full-bench (720)** numbers comparable in *scope* to the paper suite.

**What “rest” means:**
| | Repos | Questions |
|--|-------|-----------|
| **Wave 1 (done)** | flask, requests, pytest | 144 |
| **Rest (this work)** | astropy, conan, django, matplotlib, pylint, reflex, scikit-learn, sphinx, sqlfluff, streamlink, sympy, xarray | **12 × 48 = 576** |
| **Full SWE-QA** | 15 repos | **720** = W1 scores ∪ Rest scores (do **not** re-gen Wave 1) |

**Locked knobs (match Wave 1 / FREEZE):** generator `qwen3.5:9b` · judge `qwen-7B:latest` · `pipeline_phase2.toml` HEAD `a2cfcec…` · Always-LC = **current full-dump** (budgeted LC stays parked so merged 720 stays apples-to-apples with W1).

**Why not only “Wave 2 = SymPy or Django”:** Older plan (§3.2) allowed a 1–2 repo Wave 2; human now wants the **remaining 12** so Table B can be labeled `n=720`.

### Done (prior): Cursor-agent flask pilot

Flask Cursor CLI agent + Claude-subagent judge (n=48, mean Overall ~69.5) complete; optional A4/A5 still open. Not blocking SWE-QA rest.

### Parked: Budgeted Always-LC

Do **not** change Always-LC mid-suite. Revisit only after full-720 table exists (or human overrides).

## Key Challenges and Analysis

### SWE-QA rest → full 720 (Planner 2026-11-15)

1. **Scale:** 576 Q × 7 arms = **4032** generations (+ 4032 judge calls). Wave 1 queue estimates were ~180–260 min/arm for 144 tasks → rest is ~**4×** that per arm if serial (~12–17 h/arm ballpark; gated/LC arms slower). Plan for **multi-day** wall time; resume-safe queue required.
2. **No indexes yet** for the 12 repos (only flask/requests/pytest + RepoProbe indexes exist). Must clone @ `repo_commit.txt` tips → resolve full SHAs → `harness.index_cache build`. Large repos (django, sympy, scikit-learn, matplotlib, astropy) → long Graphify + heavy disk; `index.toml` already sets `max_files=1000000` (good).
3. **Config gap:** `datasets_phase2.toml` only has `swe-qa-w1`. Need new dataset e.g. **`swe-qa-rest`** (expected_count=576) + `repo_map`/`commits` for 12 shorts; extend `phase2_datasets.py` docstring/`--only` to accept it (logic already generic over `[[dataset]]`).
4. **Queue:** Mirror `phase2-sweqa-w1.toml` → `phase2-sweqa-rest.toml` with job ids `p2-sweqa-<ARM>-rest`, `dataset=swe-qa-rest`, `tasks=576`. Keep `frozen_sha=a2cfcec…`, `allow_dirty=true`.
5. **Merge, don’t redo W1:** Judge Rest separately under e.g. `phase2_sweqa_scores_rest/`; then build **Table B full** by pooling W1 + Rest instance scores (same judge already). Label tables `Wave1 n=144` vs `Full n=720`.
6. **Fairness vs paper still limited:** Full *scope* ≠ fair absolute vs paper Table 3 (still 9B pipeline + local 7B single-pass, not Sonnet 5-vote / agents). Claim: **suite-complete routing table**; not “we beat paper /100”.
7. **Risk order:** Prefer smoke (1 Q × 1 large repo, e.g. django-000 ALWAYS_RAG) after first index before burning all 7 arms. Optional: generate Always-RAG first across all 12, human spot-check, then remaining arms.
8. **Human decisions needed before Executor:** (a) all **7 arms** now vs RAG-first; (b) confirm keep **full-dump Always-LC**; (c) disk OK for ~12 large indexes.

### Cursor-agent flask pilot (historical)

1. **`CURSOR_API_KEY` still required by upstream script:** `Experiment/Script/Cursor-Agent_QA/batch_script.py` raises if unset and always passes `--api-key=`. Browser `agent login` ≠ enough. Human must set a **rotated** key in the shell (never paste into chat). Optional Executor patch: allow empty key and omit `--api-key` when logged in via `agent status` — only if key path fails.
2. **Binary name mismatch:** Paper = `cursor-agent-2025.09.04-…`. We have `agent` `2026.11.13-eacade1`. Set `CURSOR_AGENT_PATH=/home/g7/.local/bin/agent` and **label version in FREEZE** — scaffold-comparable, not version-identical.
3. **Repo layout:** Script expects `{repo_base_dir}/{repo_name}/`. Use Wave 1 checkout:  
   `eval_harness/data/cache/checkouts/pallets__flask/85c5d93cbd049c4bd0679c36fd1ddcae8c37b642` → symlink as `…/agent_repos/flask`.
4. **Question format:** Benchmark `flask.jsonl` has `question`+`answer`. Script reads `question` and optional `ground_truth`. Prepare `docs/phase2_runs/agent_qa_questions/flask.jsonl` with `ground_truth` = reference answer (or leave empty if unused for generation).
5. **Cost / time:** 48 agent trajectories with up to ~30–50 tool calls each. Start with **1-question smoke**, then full flask at `--max-concurrency 1`. Optional 2nd repo = **requests** (also 48) only after flask smoke OK.
6. **Comparability honesty:** Same questions + pinned SHA + same 5-dim judge protocol → **scaffold reference**. Absolute /100 will not match paper Cursor ~69–71 unless model/stack match.
7. **Do not mix into Table B arm rows:** Add a separate **Table B-agent** / appendix row; keep 7-arm routing table for our pipeline.
8. **Judge choice (human 2026-11-15):** Paper judge = **Claude Sonnet 4.5** (5-dim, paper uses 5-vote majority). We will use **Cursor Claude subagents** (`claude-sonnet-5-5-high`) with the **same SWE-QA rubric** as `phase2_sweqa_ollama_judge.py`. Label: `claude-subagent · single-judge` (not paper 5-vote; not Anthropic API). Scope **option 3:** judge **smoke now (n=1)**, then judge **full flask 48 after A3**. Optional later: qwen-7B cross-check on same agent answers.

### Budgeted Always-LC (parked — still valid)

1. `ALWAYS_LC_RANKED` / `_select_lc_chunks_by_rank` exist but Always-LC unused; ranked default still 24k.
2. Use `LC_TOKEN_BUDGET=8000`, not global `MAX_CONTEXT_TOKENS`.
3. Apply same packing to PREASSESS/LC escalations; archive full-dump LC; re-run Always-LC (+ PREASSESS).

## High-level Task Breakdown

### C — SWE-QA rest → full 720 (ACTIVE — awaiting human approve then Executor)

#### C0 — Human confirm scope
- Confirm: **12-repo rest**, all **7 arms**, gen `qwen3.5:9b`, judge `qwen-7B`, keep Always-LC full-dump, merge with W1 for n=720.
- Optional: RAG-first staging vs all arms queued at once.
- **Success:** Human reply recorded; Executor starts C1 only after that.

#### C1 — Dataset config + materialize
- Add `[[dataset]] name = "swe-qa-rest"` to `datasets_phase2.toml`: 12 repos, `expected_count = 576`, `repo_map` + short `commits` from SWE-QA `repo_commit.txt` (resolve to full SHAs via `--resolve-commits`).
- Update `phase2_datasets` docs/`--only` help to include `swe-qa-rest`.
- Run: `python -m harness.phase2_datasets … --only swe-qa-rest --resolve-commits`.
- **Success:** `eval_harness/data/datasets/swe-qa-rest/{tasks,labels}.jsonl` = 576 lines each; checkouts exist for all 12.

#### C2 — Build indexes (Chroma + call_graph + graphify)
- `python -m harness.index_cache build … --dataset swe-qa-rest` (same profile/pipeline/index.toml as INDEXES.md).
- Document paths in `docs/phase2_runs/INDEXES_REST.md` (or append INDEXES.md).
- **Success:** 12 index dirs with `DONE`; chunk counts logged; no silent 600-file cap.

#### C3 — Smoke (1 large repo × 1 arm)
- Queue or one-shot: e.g. `django-000` ALWAYS_RAG under rest freeze.
- **Success:** Non-empty answer + route/token telemetry; Ollama healthy; human/Executor OK to full queue.

#### C4 — Full rest generation queue (7 × 576)
- Add `eval_harness/queues/phase2-sweqa-rest.toml` (mirror W1; `tasks=576`; ids `*-rest`).
- Launch `harness.queue` with `pipeline_phase2.toml`; monitor resume/`STOPPED`.
- **Success:** 7 run dirs under `…/a2cfcec/p2-sweqa-*-rest` with **576** `results.jsonl` lines each (or documented failures).

#### C5 — Judge rest with qwen-7B
- Export candidates (reuse `phase2_export_sweqa_candidates.py` pattern); score via `phase2_sweqa_ollama_judge.py` + `judge_ollama.env`.
- Out: `eval_harness/data/reports/phase2_sweqa_scores_rest/`.
- **Success:** 7 arm summaries; same 5-dim /100 protocol as W1.

#### C6 — Merge Table B full (720) + docs
- Pool W1 + Rest scores → `TABLE_B_SWEQA_FULL.md` (+ csv); keep `TABLE_B_SWEQA_W1.md` unchanged.
- Update FREEZE (rest SHAs), PHASE2_RUNS_SUMMARY, cross-judge note: paper Table 3 scope now matched on **n**, still not on judge/scaffold.
- **Success:** Full means per arm for n=720; W1 vs Rest vs Full called out; Planner cross-check with human.

### A — Cursor-agent flask pilot (mostly done)

#### A0 — Auth + key hygiene (human)
- Revoke exposed key; create new API key; `export CURSOR_API_KEY=…` in terminal only.
- `export CURSOR_AGENT_PATH="$(which agent)"`; `agent status` shows logged in.
- **Success:** `agent status` OK; `echo ${#CURSOR_API_KEY}` > 0; key never committed / never pasted in chat.

#### A1 — Layout: repo symlink + question JSONL
- Create `docs/phase2_runs/agent_cursor/` (or `eval_harness/data/reports/phase2_cursor_agent/`) with:
  - `repos/flask` → symlink to Wave 1 flask checkout SHA above
  - `questions/flask.jsonl` from SWE-QA Benchmark (map `answer`→`ground_truth`)
  - `output/` empty
- **Success:** `ls repos/flask/src/flask` works; questions = 48 lines.

#### A2 — One-question smoke
- Run upstream `batch_script.py` with `--repo-filter flask`, concurrency 1, questions file truncated to **flask-000 only** (or temp 1-line JSONL).
- **Success:** One `output/flask.jsonl` line with non-empty `answer`; trajectory captured; no auth/proxy hard fail. Record CLI version + model in a short `SMOKE_CURSOR_AGENT.md`.

#### J1 — Claude-subagent judge on smoke (n=1) — NEXT after human says Executor
- Input: `docs/phase2_runs/agent_cursor/output/smoke/flask.jsonl` + reference from `questions/flask.jsonl` / Benchmark gold.
- Launch one Cursor Task subagent (`claude-sonnet-5-5-high`) with SWE-QA 5-dim rubric (correctness, completeness, relevance, clarity, reasoning; ints 1–20).
- Write `docs/phase2_runs/agent_cursor/scores/smoke_claude.json` + append score block to `SMOKE_CURSOR_AGENT.md`.
- **Success:** Valid 5 ints + overall/100; human can read smoke score before approving A3.

#### A3 — Full flask (48) Cursor-agent run
- Same script, full 48, `--max-concurrency 1` (raise only if stable).
- Resume-safe (script skips done questions).
- **Pre-req:** rotated key in `~/.cursor_agent_env.key`; A2 verified.
- **Success:** 48/48 answers (or document failures); outputs under `output/full/`; FREEZE note for agent version.

#### J2 — Claude-subagent judge on full flask (n=48) — after A3
- Batch like prior 7-arm flask Claude judge: e.g. 6 batches × 8 Qs (here **one candidate per Q**, so smaller prompts — can do 8–12 Qs/batch).
- Rubric identical to J1; write `scores/flask_claude_scores.jsonl` + summary mean Overall/100 + per-dim means.
- Short compare note: agent Overall vs our Always-RAG/GASR **flask-only** (from prior Claude or qwen-7B table — state which) + paper disclaimer (paper Claude Sonnet 4.5 · 5-vote · different generator).
- **Success:** 48 scored; label `cursor-cli-agent · claude-subagent-judge · single-judge · flask-only · n=48`; no claim of matching paper Cursor ~70 absolute.

#### A4 — (superseded by J1/J2) Optional qwen-7B cross-check
- Only if human wants dual-judge correlation on agent answers after J2.

#### A5 — Optional second small repo (`requests`)
- Only after A3+J2 OK. Same pattern, 48 Q, pinned `46e939b…` checkout.
- **Success:** Same artifacts for requests; or human skips.

### B — Budgeted Always-LC (PARKED)

### Task 1 — Wire Always-LC to budgeted ranked packing (code)
- Change `run_always_lc` to: retrieve → `_build_lc_chunks` → `_select_lc_chunks_by_rank(..., max_tokens=LC_TOKEN_BUDGET)`.
- Add/read env **`LC_TOKEN_BUDGET` default `8000`** (do not default ranked helper to 24000 when used for Always-LC).
- Optionally make `ALWAYS_LC_RANKED` call the same helper/budget (alias) so it cannot silently diverge; keep name for ablation scripts if needed.
- Apply the **same** budgeted packing wherever harness builds LC chunks for injection (`PREASSESS` LC branch at minimum; server `_build_lc_chunks` if Executor touches LIVE mode=LC in the same change — prefer one shared helper).
- **Success criteria:** Unit tests pass; `run_always_lc` path never relies on inject’s 24k truncate to drop files under normal flask-sized parents; selected est tokens ≤ 8000 except oversized-first-file case documented in tests.

### Task 2 — TDD for budget default and Always-LC wiring
- Extend `test_lc_ranked.py` (or add focused tests): default budget for Always-LC path is 8000 when env unset; packing stops under budget; retrieval priority preserved (existing tests remain green).
- Smoke: one flask task via harness arm `ALWAYS_LC` → assert `hit_max_context_tokens` is false (or inject stage shows no truncation notice) and `n_chunks` / prompt tokens ≪ old ~22k.
- **Success criteria:** New/updated tests green; smoke on `flask-000` recorded under `docs/phase2_runs/` note (short).

### Task 3 — Relabel freeze / tables / methods (docs only, clear wording)
- Update `docs/phase2_runs/FREEZE.md`: Always-LC = **our budgeted parent-file LC** (`LC_TOKEN_BUDGET=8000`, ranked file blocks); **not** SWE-QA paper LC (paper has none). Point to archived full-dump run dir.
- Table B / fill script stamp line: e.g. `Always-LC (ours: ranked parent files ≤8k est)` · note prior full-dump Overall for appendix if kept.
- Methods blurb draft sentence for CP12 later.
- **Success criteria:** A reader cannot confuse our LC with SWE-QA Direct/RAG; FREEZE lists `LC_TOKEN_BUDGET=8000`.

### Task 4 — Re-run Always-LC Wave 1 + re-judge + refresh Table B
- Archive current `p2-sweqa-ALWAYS_LC-w1` results (rename/move run dir + scores) as **full-dump** ablation.
- Re-queue **only** `ALWAYS_LC` (144) with new code; generator still `qwen3.5:9b`.
- Re-export candidates for that arm; re-score with `qwen-7B:latest`; regenerate Table B (token% vs **new** LC mean).
- **Success criteria:** 144/144 ok; `hit_max_context_tokens` rate ≈ 0%; Overall/100 and tokens mean updated; LC no longer near floor solely from cap thrashing (quality may still lag RAG — that is OK).

### Task 5 — Re-run arms that escalate to LC (recommended same change-set)
- After Task 1 applies budgeted LC to escalations: re-run **PREASSESS** (was 100% LC dump) and spot-check **SELF_ROUTE** / **GASR** LC subset or full re-run if cheap enough.
- Re-judge affected arms; update Table B route/%LC and Overall.
- **Success criteria:** PREASSESS ≠ bit-identical to old full-dump LC; flask mean tokens for PREASSESS LC path ~budgeted; documented in Executor feedback.

### Task 6 — Planner cross-check (human)
- Human verifies smoke + Table B delta before marking Phase 2 LC fix complete.
- **Success criteria:** Human confirms; Planner marks done (Executor does not self-close).

## Project Status Board

### Phase 2 eval (context)

- [x] CP0–CP3 readiness artifacts
- [x] CP4 SWE-QA Wave 1 generation (7 arms, old Always-LC = full dump)
- [x] CP5–CP7 local qwen-7B judge + Table B + paper note (based on **old** LC)
- [x] Claude flask-only cross-judge (n=48)
- [x] CP8 RepoProbe generation (7×108) complete
- [x] **CP9 smoke** — 7 arms × browser-use n=35 done (~21.5 min); see `docs/phase2_runs/SMOKE_CP9.md`
- [x] **CP9 full** — 744/756 scored · [`TABLE_C_REPOPROBE_PY8.md`](docs/phase2_runs/TABLE_C_REPOPROBE_PY8.md)
- [ ] CP10–CP12

### C — SWE-QA rest → full 720 (ACTIVE — Executor)

- [x] C0 — Human confirm: 12 repos × 7 arms × full-dump LC · merge W1
- [x] C1 — `swe-qa-rest` dataset + checkouts
- [x] C2 — Indexes for 12 repos (django call_graph→graph.json fallback)
- [x] C3 — Smoke: ALWAYS_LC `astropy-000` ok
- [x] C4 — Queue 7 × 576 generation (4032/4032)
- [x] C5 — qwen-7B judge on rest (576/arm)
- [x] C6 — Table B full n=720 + FREEZE/summary docs
- [x] Planner cross-check — human confirmed 2026-11-16 · **§C SWE-QA rest→full 720 COMPLETE**

### A — Cursor-agent flask pilot

- [x] A0–A3, J1–J2 (flask agent Overall **69.5** — human verify still welcome)
- [ ] A4 — Optional qwen-7B cross-check on agent answers
- [ ] A5 — Optional `requests`

### B — Budgeted Always-LC (ACTIVE on phase2-eval-haya)

- [x] **Task 1 — Wire budgeted ranked LC** (code)
- [x] **Task 2 — Smoke flask-000 ALWAYS_LC** — awaiting human verify
- [x] **Task 3 — Freeze bump** `619a4ea` + FREEZE/queues/rekey/auth — awaiting human verify
- [ ] Task 4–5 — Local retest (n=120) Always-LC + LC-escalation arms + judge
- [ ] Task 6 — Planner/human cross-check after retest

## Current Status / Progress Tracking

- **Mode:** Executor (2026-11-22). Human asked to implement LC budgeting on **haya `phase2-eval-haya`**.
- **Task 1+2:** budgeted LC wired; smoke `flask-000` ALWAYS_LC **ok**.
- Smoke artifact: `/home/g7/haya/smoke_budgeted_lc/ALWAYS_LC_flask000/` (`--smoke --allow-dirty`).
- Harness helper is `eval_harness/src/harness/lc_budget.py` (frozen backend snapshot lacks new `injection.lc_budget` until freeze bump). Backend copy still present for GASR/SELF after freeze.
- **Still required before recorded jobs:** pipeline **freeze bump** + `record_authorisation` refresh.

## Executor's Feedback or Assistance Requests

- **Freeze bump done** — please verify. Next: local n=120 retest (say when).
- Pipeline `619a4eaa…` · harness `f2b3f0b…` (+ queue pin commit if any) · auth written · 23 indexes rekeyed · snapshot extracted.
- Lesson: harness must not import new backend modules until they are in the frozen snapshot; rekey needs a venv with numpy (`-I` isolates user site).

## Lessons

- Graphify can hang on huge repos (django); use `GRAPHIFY_TIMEOUT_S` + call_graph→graph.json fallback in `index_worker.py`. Manual finalize must set `chunks_meta` in `build_report.json` to match chroma fingerprint or preflight KeyErrors.
- `record_authorisation.json` must match **current** `harness_commit` (even if pipeline freeze SHA stays `a2cfcec`); mismatch → STOPPED preflight.
- PowerShell: quote `stash@{0}` as `"stash@{0}"` or pop fails with `unknown switch e`
- Cherry-pick of graph mode conflicts heavily with current server/UI; prefer ours + manual port of graph knobs
- `8a0e6cc` UI fix was already folded into the resolved `f1a30c2` port — skip rather than re-conflict
- Creating `phase2-eval` from `origin/pipeline-fixes` sets upstream to pipeline-fixes; push with `git push -u origin HEAD:phase2-eval` then `git branch -u origin/phase2-eval`
- Python 3.10 needs `tomli as tomllib` fallback in `harness.common`
- GitHub does not fetch abbreviated SHAs as refs — resolve via Commits API (or hardcode full SHAs in `datasets_phase2.toml`)
- RepoProbe CSV question counts ≠ raw line counts (multiline `checklist` fields); use csv.DictReader
- Keep Phase 2 `pipeline_phase2.toml` separate from Phase 1 `pipeline.toml` so Nov3 freeze stays intact
- `harness.queue` needed `allow_dirty` passthrough for dirty worktrees during Phase 2 bring-up
- SWE-QA paper has **no** full parent-context LC baseline (Direct / Func RAG / Sliding RAG / agents only). Our Always-LC is thesis-owned.
- Inject truncation at 24k sorts file blocks **alphabetically**; pre-selecting ranked files under 8k avoids depending on that path for LC quality.
- `ALWAYS_LC_RANKED` already existed but defaulted budget to `MAX_CONTEXT_TOKENS=24000` — budgeted LC needs a **lower separate** budget.
- SWE-QA `Cursor-Agent_QA/batch_script.py` **requires** `CURSOR_API_KEY` even if `agent login` succeeded; it always passes `--api-key=`.
- Never paste API keys into chat/terminal logs that sync to the agent; rotate if exposed.
- Modern Cursor CLI binary is `agent`, not `cursor-agent`; set `CURSOR_AGENT_PATH` and label version vs paper `2025.09.04`.
- System Ollama models are under `/usr/share/ollama/.ollama/models` (incl. `qwen-7B:latest`); `~/.ollama/models/blobs` can be empty and still have a working judge if `OLLAMA_MODELS` points at the system store.
- RepoProbe scorer-on-pipeline entry: `scripts/phase2_repoprobe_score_pipeline.py` (not Docker evaluator).
