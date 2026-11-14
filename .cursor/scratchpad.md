# Scratchpad

## Current Status / Progress Tracking

Phase 2 **agent-executable runbook** is the Executor source of truth:

**[`docs/PHASE2_AGENT_EVAL_RUNBOOK.md`](docs/PHASE2_AGENT_EVAL_RUNBOOK.md)**

Updated to follow official READMEs: [SWE-QA-Bench](https://github.com/peng-weihan/SWE-QA-Bench), [RepoProbe](https://github.com/Tencent-Hunyuan/RepoProbe) (+ AGENT_INTEGRATION / `scorer.py` / `llm-as-a-judge.py`).

Scientific theory remains [`docs/PHASE2_REPO_QA_EVAL_PLAN.md`](docs/PHASE2_REPO_QA_EVAL_PLAN.md).  
Branch: `phase2-eval`. Executor does **one CP at a time**, then asks human to verify.

## Project Status Board

### Prior (branch merge) — done

- [x] Create `phase2-eval` from `origin/pipeline-fixes`
- [x] Merge `baseline-nov3-handover` (handover docs)
- [x] Merge `origin/main` (README → main; Pareto/tools2)
- [x] Port graph retrieval (`f1a30c2`); UI includes `8a0e6cc` intent
- [x] Harness budget 1800→1300; PHASE2 scientific plan committed
- [x] Push `origin/phase2-eval`; restore `origin/pipeline-fixes` to `708e8f3`
- [x] Write Phase 2 agent eval runbook (CP0–CP12)

### Phase 2 eval execution (from runbook) — start at CP0

- [ ] **CP0** — Env + freeze SHAs (`docs/phase2_runs/FREEZE.md`)
- [ ] **CP1** — Dataset adapters (SWE-QA Wave 1 + RepoProbe subset)
- [ ] **CP2** — Index + Graphify Wave 1
- [ ] **CP3** — Smoke 1q × 7 arms
- [ ] **CP4** — SWE-QA Wave 1 queue
- [ ] **CP5** — SWE-QA LLM judge
- [ ] **CP6** — Fill Table B
- [ ] **CP7** — Compare to SWE-QA paper
- [ ] **CP8** — RepoProbe subset run
- [ ] **CP9** — RepoProbe checklist scorer
- [ ] **CP10** — Tables C–F
- [ ] **CP11** — Hypotheses H1–H5
- [ ] **CP12** — Methods blurb + handoff

## Executor's Feedback or Assistance Requests

Runbook written. Next Executor action: start **CP0** only after human says to execute the runbook checkpoints.

Do not mix Nov 3 Phase 1 Loc Acc@k into Phase 2 Tables B–F.

## Lessons

- PowerShell: quote `stash@{0}` as `"stash@{0}"` or pop fails with `unknown switch e`
- Cherry-pick of graph mode conflicts heavily with current server/UI; prefer ours + manual port of graph knobs
- `8a0e6cc` UI fix was already folded into the resolved `f1a30c2` port — skip rather than re-conflict
- Creating `phase2-eval` from `origin/pipeline-fixes` sets upstream to pipeline-fixes; push with `git push -u origin HEAD:phase2-eval` then `git branch -u origin/phase2-eval`
