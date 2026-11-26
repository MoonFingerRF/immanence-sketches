#!/usr/bin/env python3
"""Check the native VM against the reference interpreter, frame by frame, on any sketches.

    make -C host && python3 host/parity.py piece.pde [more.pde ...] [--frames 60]

For every sketch: compile it, run it on the reference interpreter (python/immanence) and on the
C++ VM (host/build/immanence-vm), and compare the crc32 of every frame and how each run ended.
Exit 0 when every sketch matches. Use it after changing src/ or building with a new compiler.
"""
import argparse
import os
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "python"))

from immanence import PdeError, Unsupported, compile_pde, for_compiler, render, run_native  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="+")
    ap.add_argument("--frames", type=int, default=60)
    ap.add_argument("--vm", default=os.path.join(HERE, "build", "immanence-vm"))
    a = ap.parse_args()
    bad = 0
    for f in a.files:
        src = open(f, encoding="utf-8").read()
        try:
            prog = compile_pde(src)
        except (PdeError, Unsupported) as e:
            print(f"SKIP {f}: does not compile for the VM ({e})")
            continue
        ref = render(for_compiler(src), a.frames, now=lambda: 0)
        nat = run_native(a.vm, prog, a.frames, now=0)
        rc = ["%08x" % zlib.crc32(g) for g in ref["frames"]]
        nc = ["%08x" % zlib.crc32(g) for g in nat["frames"]]
        rk = (ref["error"] or {}).get("kind")
        nk = (nat["error"] or {}).get("kind")
        if rc == nc and rk == nk:
            print(f"OK   {f}: {len(rc)} frames" + (f", both stop with {rk}" if rk else ""))
            continue
        bad += 1
        first = next((i for i, (x, y) in enumerate(zip(rc, nc)) if x != y), min(len(rc), len(nc)))
        print(f"DIFF {f}: frame {first + 1} (reference {len(rc)} frames {rk or 'ok'}, VM {len(nc)} frames {nk or 'ok'})")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
