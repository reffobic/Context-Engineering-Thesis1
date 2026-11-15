#!/usr/bin/env python3
"""SWE-QA 5-dim LLM judge via local Ollama (OpenAI-compatible).

Protocol matches upstream SWE-QA-Bench ``llm-as-a-judge.py`` (Correctness,
Completeness, Relevance, Clarity, Reasoning each 1–20 → /100), but uses
``openai.OpenAI`` against Ollama instead of AzureOpenAI.

Env (see docs/phase2_runs/judge_ollama.env):
  EVAL_LLM_BASE_URL      default http://127.0.0.1:11434/v1
  EVAL_LLM_API_KEY       default ollama
  EVAL_LLM_MODEL_NAME    required (locked: qwen3.8:27b)
  EVAL_REFERENCE_PATH    gold JSONL (question, answer)
  EVAL_CANDIDATE_PATH    candidate JSONL (question, answer)  OR
  EVAL_CANDIDATE_DIR     directory of candidate *.jsonl
  EVAL_OUTPUT_PATH       single output  OR
  EVAL_OUTPUT_DIR        directory for *_score.jsonl
  EVAL_REPO_FILTER       optional comma filter (unused unless candidates carry repo)
  EVAL_MAX_WORKERS       default 1 (local GPU — keep low)
  PHASE2_JUDGE_SMOKE=1   score one hardcoded triple and exit

Label tables: local-judge · single-judge · generator≠judge
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

from openai import OpenAI

JUDGE_PROMPT = """You are a STRICT and RIGOROUS evaluator. You must rate the candidate answer STRICTLY against the reference answer. Be CONSERVATIVE with high scores - only award high scores (16-20) when the candidate answer is truly excellent and closely matches the reference answer in quality and content.

CRITICAL EVALUATION PRINCIPLES:
1. Compare the candidate answer DIRECTLY with the reference answer point by point
2. Any deviation, omission, or inaccuracy should result in score reduction
3. High scores (16-20) should be RARE - reserve them only for answers that are nearly perfect
4. Be strict about factual accuracy - even minor errors should lower the correctness score
5. Missing key points from the reference answer should significantly reduce completeness score
6. Vague or imprecise language should lower clarity scores
7. When in doubt between two score ranges, choose the LOWER one

Evaluation Criteria and Scoring Guidelines (each scored 1 to 20, total score 100):
        1. Correctness (STRICT - penalize any inaccuracies):
            20 — ONLY if completely correct with ALL core points and details accurate, matching reference answer precisely
            16-19 — Mostly correct but must have only TRIVIAL inaccuracies; any noticeable error reduces to 15 or below
            12-15 — Partially correct; has some errors or omissions that affect understanding; main points may be accurate but details are wrong
            8-11 — Several errors or ambiguities that significantly affect understanding of core information
            4-7 — Many errors; misleading or fails to convey key information correctly
            1-3 — Serious errors; completely wrong or misleading
        2. Completeness (STRICT - penalize missing information):
            20 — ONLY if covers ALL key points from reference answer without ANY omission; must match reference in depth
            16-19 — Covers most key points but missing some non-trivial information; minor omissions are acceptable
            12-15 — Missing several important key points; content is noticeably incomplete compared to reference
            8-11 — Important information largely missing; content is one-sided or superficial
            4-7 — Covers very little relevant information; seriously incomplete
            1-3 — Covers almost no relevant information; completely incomplete
        3. Relevance (STRICT - penalize off-topic content):
            20 — ONLY if content is fully focused on question topic with NO irrelevant information whatsoever
            16-19 — Mostly focused but may have minor peripheral information; any significant off-topic content reduces score
            12-15 — Generally on topic but contains some off-topic content that detracts from answer
            8-11 — Topic not sufficiently focused; contains considerable off-topic or tangential content
            4-7 — Content deviates from topic; includes excessive irrelevant information
            1-3 — Majority of content irrelevant to the question
        4. Clarity (STRICT - penalize unclear expression):
            20 — ONLY if language is exceptionally fluent, clear, and precise; very easy to understand without any ambiguity
            16-19 — Mostly fluent and clear but may have minor unclear points; any significant ambiguity reduces score
            12-15 — Generally clear but some expressions are unclear or not concise; may require effort to understand
            8-11 — Expression somewhat awkward; has ambiguity or lacks fluency that hinders understanding
            4-7 — Language obscure; sentences are not smooth; significantly hinders understanding
            1-3 — Expression confusing; very difficult to understand
        5. Reasoning (STRICT - penalize weak logic):
            20 — ONLY if reasoning is exceptionally clear, logical, and well-structured; argumentation is excellent and matches reference quality
            16-19 — Reasoning is clear and logical with solid argumentation; minor logical gaps may exist
            12-15 — Reasoning generally reasonable but has noticeable logical jumps or organization issues
            8-11 — Reasoning is average; has logical jumps or organization problems that affect understanding
            4-7 — Reasoning unclear; lacks logical order; difficult to follow
            1-3 — No clear reasoning; logic is chaotic

INPUT:
    Question:{question}
    Reference Answer:{reference}
    Candidate Answer:{candidate}

OUTPUT:
    Please output ONLY a JSON object with 5 integer fields in the range [1,20], corresponding
    to the evaluation scores:
        {{
        "correctness": <1-20>,
        "completeness": <1-20>,
        "relevance": <1-20>,
        "clarity": <1-20>,
        "reasoning": <1-20>
        }}

SCORING INSTRUCTIONS:
- Read the reference answer carefully and identify ALL key points, details, and structure
- Compare the candidate answer systematically against the reference answer
- For each criterion, start with a conservative score and only increase if the candidate truly deserves it
- If the candidate answer is significantly shorter, less detailed, or less precise than the reference, reduce scores accordingly
- If the candidate answer contains information not in the reference (unless it's clearly relevant and accurate), consider reducing relevance score
- When scoring, ask yourself: "Does this candidate answer match the quality and completeness of the reference answer?" If not, reduce scores
- Average or mediocre answers should receive scores in the 8-15 range, not higher
- Only truly excellent answers that closely match the reference should receive 16-20 scores

REQUIREMENT:
    No explanation, no extra text, no formatting other than valid JSON. Be strict and conservative with your scores."""

REQUIRED = ("correctness", "completeness", "clarity", "relevance", "reasoning")


def get_client() -> OpenAI:
    return OpenAI(
        base_url=os.environ.get("EVAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1"),
        api_key=os.environ.get("EVAL_LLM_API_KEY", "ollama"),
        timeout=float(os.environ.get("EVAL_LLM_TIMEOUT", "600")),
    )


def _parse_scores(raw: str) -> Optional[dict[str, int]]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # Prefer first JSON object if model added prose
    m = re.search(r"\{[^{}]*\}", text, re.S)
    if m:
        text = m.group(0)
    try:
        scores = json.loads(text)
    except json.JSONDecodeError:
        return None
    for k in REQUIRED:
        if k not in scores or not isinstance(scores[k], (int, float)) or not (1 <= int(scores[k]) <= 20):
            return None
    return {k: int(scores[k]) for k in REQUIRED}


def score_answer(question: str, reference: str, candidate: str, client: OpenAI, model: str) -> Optional[dict[str, int]]:
    prompt = JUDGE_PROMPT.format(question=question, reference=reference, candidate=candidate)
    # Ollama: disable thinking when supported via options / extra body is model-specific — keep plain chat.
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    content = (resp.choices[0].message.content or "").strip()
    scores = _parse_scores(content)
    if scores is None:
        print(f"[WARN] parse failed; raw head={content[:200]!r}", flush=True)
    return scores


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def reference_map(rows: list[dict[str, Any]]) -> dict[str, str]:
    return {r["question"]: r["answer"] for r in rows if r.get("question") and r.get("answer")}


def score_file(cand_path: Path, ref_map: dict[str, str], out_path: Path, client: OpenAI, model: str, workers: int) -> dict:
    cands = load_jsonl(cand_path)
    results: list[dict] = []
    failed = 0

    def one(rec: dict) -> Optional[dict]:
        q, a = rec.get("question", ""), rec.get("answer", "")
        ref = ref_map.get(q, "")
        if not ref or not a or a.strip() == "No answer found":
            return None
        scores = score_answer(q, ref, a, client, model)
        if not scores:
            return None
        return {"question": q, "score": scores, "overall": sum(scores.values()),
                "task_id": rec.get("task_id") or rec.get("instance_id"),
                "arm": rec.get("arm"), "repo": rec.get("repo")}

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = [pool.submit(one, r) for r in cands]
        for fut in as_completed(futs):
            r = fut.result()
            if r is None:
                failed += 1
            else:
                results.append(r)
                print(f"  scored overall={r['overall']} q={r['question'][:60]!r}", flush=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="\n") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary = {
        "candidate": str(cand_path),
        "output": str(out_path),
        "n_candidates": len(cands),
        "n_scored": len(results),
        "n_failed_or_skipped": failed,
        "mean_overall": round(sum(r["overall"] for r in results) / len(results), 2) if results else None,
        "judge_model": model,
        "label": "local-judge single-judge",
    }
    (out_path.with_suffix(".summary.json")).write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1), flush=True)
    return summary


def smoke(client: OpenAI, model: str) -> int:
    q = "What does ensure_ascii control in Flask JSON?"
    ref = "ensure_ascii on DefaultJSONProvider controls whether non-ASCII characters are escaped as \\uXXXX in JSON output."
    cand = "The ensure_ascii flag escapes non-ASCII characters when True."
    print(f"smoke judge model={model}", flush=True)
    scores = score_answer(q, ref, cand, client, model)
    print(json.dumps({"scores": scores, "overall": sum(scores.values()) if scores else None}, indent=1))
    return 0 if scores else 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--smoke", action="store_true", help="one local smoke score (loads judge model onto GPU)")
    args = ap.parse_args()
    model = os.environ.get("EVAL_LLM_MODEL_NAME") or os.environ.get("PHASE2_JUDGE_MODEL") or "qwen3.8:27b"
    client = get_client()
    if args.smoke or os.environ.get("PHASE2_JUDGE_SMOKE") == "1":
        return smoke(client, model)

    ref_path = os.environ.get("EVAL_REFERENCE_PATH")
    if not ref_path:
        print("EVAL_REFERENCE_PATH required", file=sys.stderr)
        return 2
    ref_map = reference_map(load_jsonl(Path(ref_path)))
    workers = int(os.environ.get("EVAL_MAX_WORKERS") or "1")

    cand_path = os.environ.get("EVAL_CANDIDATE_PATH")
    cand_dir = os.environ.get("EVAL_CANDIDATE_DIR")
    out_path = os.environ.get("EVAL_OUTPUT_PATH")
    out_dir = os.environ.get("EVAL_OUTPUT_DIR")

    if cand_path:
        out = Path(out_path) if out_path else Path(cand_path).with_name(Path(cand_path).stem + "_score.jsonl")
        score_file(Path(cand_path), ref_map, out, client, model, workers)
        return 0
    if cand_dir:
        odir = Path(out_dir or cand_dir)
        odir.mkdir(parents=True, exist_ok=True)
        for p in sorted(Path(cand_dir).rglob("*_answers.jsonl")):
            score_file(p, ref_map, odir / (p.stem + "_score.jsonl"), client, model, workers)
        return 0
    print("Set EVAL_CANDIDATE_PATH or EVAL_CANDIDATE_DIR", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
