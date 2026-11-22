"""After backend fix C the PromptBuilder packs file blocks in rank order and skips files that do not fit,
so the harness pre-selection in ALWAYS_LC_RANKED should add nothing: both routes must keep the same file blocks.

Synthetic chunks only (no index, no model). The two outputs differ only by the truncation notice, which the plain route
carries when it drops a file.
"""

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from harness.lc_budget import select_lc_chunks_by_rank as _select_lc_chunks_by_rank  # noqa: E402
from injection.prompt_builder import CONTEXT_TRUNCATED_TAG, PromptBuilder  # noqa: E402


def _chunks(seed: int):
    rnd = random.Random(seed)
    files = [f"pkg/m{i}.py" for i in range(12)]
    out = []
    for _ in range(rnd.randint(8, 30)):
        f = rnd.choice(files)
        out.append({"path": f, "text": "x = 1\n" * rnd.randint(1, 120), "symbol": f"s{rnd.randint(0, 4)}", "language": "python"})
    return out


def _context(user_message: str) -> str:
    # drop the truncation notice (only the plain route has it) so the file blocks can be compared
    if user_message.count(CONTEXT_TRUNCATED_TAG):
        start = user_message.index(CONTEXT_TRUNCATED_TAG)
        end = user_message.index("</CONTEXT_TRUNCATED>", start) + len("</CONTEXT_TRUNCATED>")
        user_message = user_message[:start] + user_message[end:]
    return "\n".join(line for line in user_message.splitlines() if line.strip())


def test_ranked_preselection_is_redundant_with_the_backend_packing():
    from backend.injection.prompt_builder import ContextChunk  # noqa: E402

    mismatches = []
    for seed in range(40):
        raw = _chunks(seed)
        budget = random.Random(seed).choice([300, 600, 1200, 2500])
        b = PromptBuilder()
        plain = b.build("task", [ContextChunk(**c) for c in raw], max_context_tokens=budget)[1]
        ranked = b.build("task", [ContextChunk(**c) for c in _select_lc_chunks_by_rank(raw, max_tokens=budget)], max_context_tokens=budget)[1]
        if _context(plain) != _context(ranked):
            mismatches.append((seed, budget))
    assert not mismatches, mismatches
