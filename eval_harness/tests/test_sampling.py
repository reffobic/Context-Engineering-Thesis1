import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness.sampling import allocate, sample_tasks  # noqa: E402


def _tasks():
    out = []
    for repo, n in {"a/a": 50, "b/b": 30, "c/c": 15, "d/d": 4, "e/e": 1}.items():
        out += [{"instance_id": f"{repo}-{i}", "repo": repo} for i in range(n)]
    return out


def test_total_and_determinism():
    ids1, _ = sample_tasks(_tasks(), 0.10, 42)
    ids2, _ = sample_tasks(list(reversed(_tasks())), 0.10, 42)
    assert ids1 == ids2 and len(ids1) == 10


def test_different_seed_differs_and_subset_of_input():
    ids1, _ = sample_tasks(_tasks(), 0.10, 42)
    ids2, _ = sample_tasks(_tasks(), 0.10, 43)
    assert ids1 != ids2
    assert set(ids1) <= {t["instance_id"] for t in _tasks()}


def test_allocation_proportional_and_bounded():
    counts = {"a": 50, "b": 30, "c": 15, "d": 4, "e": 1}
    alloc = allocate(counts, 0.10, 42)
    assert sum(alloc.values()) == 10
    assert alloc["a"] == 5 and alloc["b"] == 3
    assert all(0 <= alloc[r] <= counts[r] for r in counts)
