#!/usr/bin/env python3
"""Write prompt/system-full.md: system.md with its short reference (from WHAT WORKS up to RULES)
replaced by the full references of the skill (reference/language.md and reference/library.md).
Run it after changing any of the three files."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REF = os.path.join(HERE, "..", "sketch-writer", "reference")


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


s = read(os.path.join(HERE, "system.md"))
a, b = s.index("WHAT WORKS"), s.index("RULES THAT KEEP")
full = (s[:a] + read(os.path.join(REF, "language.md")).strip() + "\n\n"
        + read(os.path.join(REF, "library.md")).strip() + "\n\n" + s[b:])
with open(os.path.join(HERE, "system-full.md"), "w", encoding="utf-8") as f:
    f.write(full)
print(f"system.md {len(s)} chars (~{len(s) // 4} tokens), system-full.md {len(full)} chars (~{len(full) // 4} tokens)")
