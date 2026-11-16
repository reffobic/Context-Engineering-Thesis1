# Cursor-agent flask pilot (SWE-QA paper scaffold)

Layout for upstream `SWE-QA-Bench/Experiment/Script/Cursor-Agent_QA/batch_script.py`.

- `repos/flask` — pallets/flask @ `85c5d93cbd049c4bd0679c36fd1ddcae8c37b642` (Wave 1)
- `questions/flask.jsonl` — 48 SWE-QA flask questions (`ground_truth` = reference answer)
- `smoke/flask.jsonl` — flask-000 only
- `output/` — agent answers

CLI: `agent` 2026.11.13 (set `CURSOR_AGENT_PATH`); requires `CURSOR_API_KEY` for batch_script.

Do not commit secrets. Label results: `cursor-cli-agent · flask-only · n=48` (not paper Cursor absolute score).
