# Table B-agent — Cursor CLI agent on SWE-QA flask (n=48)

**Stamp (UTC):** 2026-11-15T06:48:44Z  
**Label:** `cursor-cli-agent · claude-subagent-judge · single-judge · flask-only · n=48`  
**Generator:** Cursor `agent` 2026.11.13-eacade1 · model `auto` · flask @ `85c5d93…`  
**Judge:** Cursor Claude subagent (`claude-sonnet-5-5-high`) · **single-pass** (paper uses Claude Sonnet 4.5 · **5-vote**)

## Result

| Metric | Score |
|--------|------:|
| **Overall /100 (mean)** | **69.5** |
| Correctness | 15.06 |
| Completeness | 10.19 |
| Relevance | 16.62 |
| Clarity | 15.58 |
| Reasoning | 12.04 |
| Min / Max Overall | 51 / 83 |

Files: `scores/flask_claude_scores.jsonl`, `scores/flask_claude_summary.json`.

## Vs paper (anchors only — not identical setup)

| System | Overall /100 | Note |
|--------|-------------:|------|
| **Our Cursor CLI agent (flask only)** | **69.5** | This run |
| Paper Cursor-agent (full SWE-QA) | ~70.7 | Different CLI version + 5-vote Claude Sonnet 4.5 |
| Paper flask repo average (across models) | ~75.4 | Easier repo; multi-model avg |
| Paper GPT-5.1 + OpenHands | ~70.8 | Full benchmark |

**Do not claim we beat paper Cursor absolute score.** Same question set + pinned SHA + same 5-dim rubric family; judge protocol and agent version differ.

## Vs our 7-arm pipeline on flask (same Claude-subagent judge earlier)

| Arm | Flask mean Overall (Claude subagent) |
|-----|-------------------------------------:|
| Cursor CLI agent (this) | **69.5** |
| GASR | 35.8 |
| ALWAYS_RAG | 35.7 |
| ALWAYS_GRAPH | 33.3 |
| GAPA | 33.3 |
| SELF_ROUTE | 32.6 |
| ALWAYS_LC | 27.1 |
| PREASSESS | 27.1 |

qwen-7B judge flask means (pipeline): {'ALWAYS_LC': 16.5, 'ALWAYS_RAG': 31.1, 'ALWAYS_GRAPH': 25.9, 'SELF_ROUTE': 25.9, 'PREASSESS': 16.5, 'GASR': 30.1, 'GAPA': 26.3}

**Takeaway:** Cursor-agent scaffolding + strong `auto` model scores far above our `qwen3.5:9b` 7-arm means on flask under a Claude-like judge — expected; primary thesis claim remains routing cost–quality among the 7 arms, with this row as a paper-scaffold reference.
