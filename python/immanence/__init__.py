"""immanence-sketches: compile Processing sketches to compact bytecode and run them on small displays.

    from immanence import compile_pde, check, Sketch

    program = compile_pde(source)          # bytes: the bytecode the C++ VM runs (magic b"PDEB")
    report = check(source, frames=60)      # compiles, runs, and says whether it draws a picture
    sk = Sketch(source)                    # the reference interpreter (bit-identical to the VM)
    grey = sk.next_frame()                 # 240*128 grey bytes, 0 = black

The compiler and the reference interpreter are pure Python (no dependencies). The C++ VM in
`src/` runs the same bytecode on an ESP32 (or any C++17 target) and draws the same pixels.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import zlib

from .pde import H, LIMITS, W, PdeError, Sketch, render, to_rows
from .pde.bcspec import MAGIC, VERSION as BYTECODE_VERSION
from .pde.bytecode import Unsupported, compile_bytecode

__version__ = "1.0.0"
__all__ = ["compile_pde", "check", "Sketch", "PdeError", "Unsupported", "render", "to_rows", "W", "H",
           "BYTECODE_VERSION", "MAGIC", "for_compiler", "ink_share", "picture", "find_vm", "run_native"]

# A sketch written for real Processing with BitMapMathLib starts with `import bmml.*;`. The prelude is
# linked automatically here, so every such line reaches the compiler blank (line numbers unchanged),
# however it is spaced or commented -- the same rule as the browser compiler on immanencepure.xyz.
_BMML_IMPORT = re.compile(r"^[ \t]*import[ \t]+bmml(?:[ \t]*\.[ \t]*\*)?[ \t]*;?[ \t]*(?://.*|/\*.*\*/[ \t]*)?$",
                          re.M)

# A frame is a picture when the share of ink after the 1-bit dither is strictly between these.
SOLID, EMPTY = 0.97, 0.003


def for_compiler(src: str) -> str:
    """The source as the compiler should see it: BitMapMathLib import lines blanked."""
    return _BMML_IMPORT.sub("", src)


def compile_pde(src: str) -> bytes:
    """Compile Processing source to VM bytecode. Raises PdeError (bad sketch) or Unsupported (valid
    Processing the VM does not run: see docs/LANGUAGE.md)."""
    return compile_bytecode(for_compiler(src))


def ink_share(grey: bytes) -> float:
    """Share of pixels that are ink after the 1-bit ordered dither (what a one-bit display shows)."""
    return sum(r.bit_count() for r in to_rows(grey)) / (W * H)


def picture(grey: bytes) -> str | None:
    """None when a frame is a picture; otherwise why not ("blank", "all ink")."""
    s = ink_share(grey)
    if s >= SOLID:
        return f"all ink ({s:.0%})"
    if s <= EMPTY:
        return f"blank ({s:.1%} ink)"
    return None


def check(src: str, frames: int = 60, now: float = 0.0, vm: str | None = None) -> dict:
    """The gate a generated sketch must pass: it compiles to bytecode, runs `frames` frames without a
    fault, and every frame it draws is a picture (not blank, not all ink).

    Returns {"ok", "stage", "error", "frames", "still", "animated", "bytecode_bytes", "max_steps",
    "ink": [first, last], "frame_crc32": [...]} -- `error` is a dict {kind, message, line} or a
    string, `stage` is where it stopped: "compile" | "run" | "picture" | "done".

    Frames run on the native host runner (`immanence-vm`, built from host/: the C++ VM itself, fast)
    when it is found (argument, $IMMANENCE_VM or PATH), else on the Python reference interpreter;
    both draw the same pixels."""
    out: dict = {"ok": False, "stage": "compile", "error": None, "frames": 0, "still": False,
                 "animated": False, "bytecode_bytes": 0, "max_steps": 0, "ink": None, "frame_crc32": []}
    try:
        prog = compile_pde(src)
    except PdeError as e:
        out["error"] = e.as_dict()
        return out
    except Unsupported as e:
        out["error"] = {"kind": "unsupported", "message": e.what, "line": e.line}
        return out
    except RecursionError:
        out["error"] = {"kind": "depth", "message": "the code is nested too deeply", "line": 0}
        return out
    out["bytecode_bytes"] = len(prog)
    out["stage"] = "run"
    vm = vm or find_vm()
    r = run_native(vm, prog, frames, now) if vm else render(for_compiler(src), frames, now=lambda: now)
    out["runner"] = "native" if vm else "python"
    fs = r["frames"]
    out["frames"] = len(fs)
    out["still"] = r["still"] and len(fs) <= 1
    out["max_steps"] = max(r["steps"], default=0)
    out["frame_crc32"] = ["%08x" % zlib.crc32(g) for g in fs]
    out["animated"] = len(set(out["frame_crc32"])) > 1
    if r["error"] is not None:
        out["error"] = r["error"]
        return out
    if not fs:
        out["error"] = {"kind": "runtime", "message": "no frame was drawn", "line": 0}
        return out
    out["stage"] = "picture"
    out["ink"] = [round(ink_share(fs[0]), 4), round(ink_share(fs[-1]), 4)]
    for i, g in enumerate(fs):
        why = picture(g)
        if why is not None:
            where = "" if len(fs) == 1 else f" on frame {i + 1}"
            out["error"] = {"kind": "picture", "message": f"{why}{where}", "line": 0}
            return out
    out["stage"] = "done"
    out["ok"] = True
    return out


_FAULTS = {"runtime", "memory", "size", "budget", "deadline", "depth", "bad_program", "unsupported"}


def find_vm() -> str | None:
    """The native host runner: $IMMANENCE_VM, or `immanence-vm` on PATH; None if neither."""
    env = os.environ.get("IMMANENCE_VM")
    if env:
        return env if os.access(env, os.X_OK) else None
    return shutil.which("immanence-vm")


def run_native(vm: str, prog: bytes, frames: int, now: float = 0.0, arena: int | None = None) -> dict:
    """Run bytecode on the native host runner; same result shape as pde.render()."""
    out: dict = {"frames": [], "error": None, "still": False, "ms": [], "steps": []}
    with tempfile.TemporaryDirectory() as d:
        pb, dump = os.path.join(d, "p.pdeb"), os.path.join(d, "frames.grey")
        with open(pb, "wb") as f:
            f.write(prog)
        cmd = [vm, pb, str(max(1, frames)), "--clock", str(int(now)), "0", "--dump", dump]
        if arena:
            cmd += ["--arena", str(arena)]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        data = open(dump, "rb").read() if os.path.exists(dump) else b""
    for line in res.stdout.splitlines():
        t = line.split()
        if t[0] == "F":
            out["steps"].append(int(t[3]))
            out["ms"].append(int(t[4]) / 1000)
        elif t[0] == "END":
            if t[1] == "done":
                out["still"] = True
            elif t[1] == "fault" or t[1] == "bad_program":
                kind = t[2] if t[1] == "fault" else "bad_program"
                msg = " ".join(t[3:] if t[1] == "fault" else t[2:])
                out["error"] = {"kind": kind if kind in _FAULTS else "runtime", "message": msg, "line": 0}
    n = W * H
    out["frames"] = [data[i:i + n] for i in range(0, len(data) - n + 1, n)]
    if res.returncode != 0 and out["error"] is None:
        out["error"] = {"kind": "runtime", "message": f"runner exited {res.returncode}", "line": 0}
    return out
