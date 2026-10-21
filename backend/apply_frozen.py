import re
from pathlib import Path

vals = {}
for line in open("frozen.env"):
    line = line.strip()
    if line.startswith("export ") and "=" in line:
        k, v = line[7:].split("=", 1)
        vals[k] = v

SKIP = {".venv", "context_metrics", "__pycache__"}
SKIP_FILES = {"finalists.py", "finalists_repo.py", "screen_front.py", "check_gold.py", "apply_frozen.py"}
hits = {k: 0 for k in vals}

for p in Path(".").rglob("*.py"):
    if any(x in SKIP or x.startswith("chroma_db") for x in p.parts) or p.name in SKIP_FILES:
        continue
    text = p.read_text()
    new = text
    for k, v in vals.items():
        pat = re.compile(r'(os\.(?:getenv|environ\.get)\(\s*["\']' + re.escape(k) + r'["\']\s*,\s*)(["\']?)([-+0-9.eE]+)(["\']?)(\s*\))')
        new, n = pat.subn(lambda m: m.group(1) + m.group(2) + v + m.group(4) + m.group(5), new)
        hits[k] += n
    if new != text:
        p.write_text(new)
        print("updated", p)

for k, n in hits.items():
    print("OK   " if n == 1 else "CHECK", k, "->", vals[k], f"({n} matches)")
