# Cursor-agent smoke (flask-000)

**Stamp (UTC):** 2026-11-15T06:02:00Z  
**CLI:** `agent` via `CURSOR_AGENT_PATH` · version **2026.11.13-eacade1** (paper used cursor-agent-2025.09.04)  
**Model:** `auto`  
**Repo:** `repos/flask` @ `85c5d93cbd049c4bd0679c36fd1ddcae8c37b642`  
**Question:** flask-000 (1/48)  
**Flags:** `--print --output-format json --force --max-tool-calls 30`  
**Latency:** 17.49s  

## Extracted answer (head)

The DefaultJSONProvider's ensure_ascii attribute (default True) controls Unicode in JSON output: when True, non-ASCII characters are replaced with \uXXXX escape sequences; when False, they are emitted as literal Unicode characters. It is passed through to json.dumps during serialization.

## Notes

- Upstream `batch_script.py` kills the process on `<end_of_answer>` (exit -9) — expected; answer still parsed.
- Warning: Cursor index not found for workspace (open repo in Cursor IDE optional).
- Label: `cursor-cli-agent · flask-smoke · n=1 · not paper-absolute`.
- **Security:** rotate `CURSOR_API_KEY` if logs ever printed `--api-key=crsr_…` unmasked; keep key only in `~/.cursor_agent_env.key` (mode 600).

## Claude-subagent judge (J1)

- Label: `claude-subagent · single-judge · cursor-cli-agent-smoke`
- Judge model: `claude-sonnet-5-5-high`
- Overall: **79/100**
- Dims: correctness=17, completeness=12, relevance=19, clarity=18, reasoning=13
- File: `scores/smoke_claude.json`
- Note: paper uses Claude Sonnet 4.5 with 5-vote majority; this is single-pass Cursor subagent.
