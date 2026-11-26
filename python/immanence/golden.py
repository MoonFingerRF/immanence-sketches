"""A sketch as a program plus its golden frames: what a device needs to run it and check itself.

    got = build(src)    # {"ok": True, "id", "program", "hashes", "k", "still", "clock", "t0", "ms"}
                        # or {"ok": False, "why": ...}

`program` is the bytecode the C++ VM runs; `hashes` are crc32 of the 240x128 grey canvas of the
first `k` frames, drawn by the reference interpreter. A device can run the program, compare its
own first frames with these hashes and so prove that its build of the VM is exact (the compiler
flags matter: see src/pde_fp.h). A sketch that reads the clock (`clock`) draws differently at
every time, so its hashes hold only for the time `t0` it was drawn at.
"""
from __future__ import annotations

import hashlib
import time
import zlib
from typing import Callable

from . import for_compiler
from .pde import PdeError, Sketch
from .pde.bytecode import Unsupported, compile_bytecode

K = 30                    # golden frames per program


def build(src: str, k: int = K, now: Callable[[], float] = time.time) -> dict:
    """Compile `src` and draw its first `k` frames for the golden hashes."""
    src = for_compiler(src)
    try:
        program = compile_bytecode(src)
    except Unsupported as e:
        return {"ok": False, "why": f"unsupported: {e.what}"}
    except PdeError as e:
        return {"ok": False, "why": f"{e.kind}: {e}"}
    t0 = int(now())
    try:
        sk = Sketch(src, now=lambda: t0)
    except PdeError as e:
        return {"ok": False, "why": f"{e.kind}: {e}"}
    hashes: list[int] = []
    ms: list[float] = []
    for _ in range(k):
        try:
            g = sk.next_frame()
        except PdeError as e:
            return {"ok": False, "why": f"frame {len(hashes) + 1}: {e.kind}"}
        if g is None:
            break
        hashes.append(zlib.crc32(g))
        ms.append(sk.last_ms)
        if sk.done:
            break
    if not hashes:
        return {"ok": False, "why": "no frame"}
    return {"ok": True, "id": hashlib.sha1(program).hexdigest()[:16], "program": program, "hashes": hashes,
            "k": len(hashes), "still": sk.done, "clock": bool(program[5] & 1), "t0": t0,
            "ms": round(max(ms), 1)}
