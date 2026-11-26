"""The reference interpreter for the Processing subset the compiler and the VM accept.

    sk = Sketch(source)                  # parse + compile; raises PdeError for what cannot be read
    grey = sk.next_frame()               # setup() the first time, then one draw(); 240*128 grey bytes
    rows = to_rows(grey)                 # 1-bit rows (bit x = pixel x, 1 = ink): the ordered 1-bit dither

`next_frame()` returns None once the sketch has stopped (noLoop() took effect, or a still sketch
has shown its one frame). A frame that breaks a limit raises PdeError and the sketch stays broken.
Safe by construction: see
compiler.py; no eval/exec, no I/O, bounded steps, time, memory and recursion.
"""
from __future__ import annotations

import time
from array import array

import functools

from .compiler import JList, Obj, PVec, compile_program
from .errors import PdeError
from .parser import parse
from .runtime import W, H, Api, Budget, PGr, PImg, to_rows
from .builtins import JMap

__all__ = ["PdeError", "Sketch", "render", "to_rows", "W", "H", "LIMITS", "library_ast", "library_parts",
           "link_ast", "callback_hints"]

LIMITS = {
    "steps": 4_000_000,         # per frame (setup has its own)
    "seconds": 2.0,             # wall clock per frame
    "alloc_bytes": 32 << 20,    # allocated within one frame
    "live_bytes": 8 << 20,      # still reachable from the globals after a frame
}
_SWEEP_EVERY = 4 << 20


@functools.lru_cache(maxsize=None)
def _chunk_ast(index: int) -> tuple:
    from .. import mathlib
    ch = mathlib.chunks()[index]
    _, classes = mathlib._index()
    return tuple(parse(ch.text, classes, mathlib.LINE_BASE + ch.line, bounded=False))


def library_parts(src: str) -> tuple[list, list]:
    """(the library's global declarations, its functions and classes) that this sketch calls. The
    program is `globals + sketch + rest`: the library's globals are initialised before the sketch's
    own (a sketch global may use PHI, CX ...), exactly as mathlib.link lays out the source for real
    Processing. ([], []) for a sketch that calls none of it."""
    from .. import mathlib
    try:
        pre, post = mathlib.split_plan(src)
    except mathlib.LinkError as e:
        raise PdeError("compile", str(e)) from None
    a: list = []
    b: list = []
    for k in pre:
        a.extend(_chunk_ast(k))
    for k in post:
        b.extend(_chunk_ast(k))
    return a, b


def link_ast(src: str, ast: list) -> list:
    """The sketch's AST with the library parts it calls around it (see library_parts)."""
    pre, post = library_parts(src)
    return pre + ast + post if (pre or post) else ast


def callback_hints() -> dict[str, str]:
    """The names the art library asks the SKETCH to write (mathlib.CALLBACKS): shadeField() calls
    the sketch's own float shade(float x, float y), plot() its float fy(float x), polarPlot() and
    polarShape() its float fr(float a). The library itself has no such function, so a compile error
    at a call of one of them says who wants it and what to write, instead of "is not defined" --
    the reference line "shadeField(cell) fills the frame from YOUR float shade(float x, float y)"
    reads as a library call, and a piece that called shade(x, y) was dropped for it."""
    from .. import mathlib
    out: dict[str, str] = {}
    for who, (name, sig) in sorted(mathlib.CALLBACKS.items()):
        out.setdefault(name, f"{name} is not a library function: it is the one your sketch writes "
                             f"for {who}() -- {sig}")
    return out


def library_ast(src: str) -> list:
    """The art library's parts this sketch calls (art/mathlib.py), parsed once and shared: [] for a
    sketch that calls none of it, which then compiles exactly as without the library. The sketch
    itself was parsed (and bounded) first; the library's lines are numbered from 10000."""
    from .. import mathlib
    try:
        need = mathlib.plan(src)
    except mathlib.LinkError as e:
        raise PdeError("compile", str(e)) from None
    out: list = []
    for k in need:
        out.extend(_chunk_ast(k))
    return out


class Sketch:
    def __init__(self, src: str, steps: int | None = None, seconds: float | None = None,
                 alloc_bytes: int | None = None, live_bytes: int | None = None,
                 clock=time.monotonic, now=time.time) -> None:
        self.seconds = LIMITS["seconds"] if seconds is None else seconds
        self.live_bytes = LIMITS["live_bytes"] if live_bytes is None else live_bytes
        self.budget = Budget(LIMITS["steps"] if steps is None else steps, self.seconds,
                             LIMITS["alloc_bytes"] if alloc_bytes is None else alloc_bytes, clock)
        self.api = Api(self.budget, now)
        try:
            ast = parse(src)
            self.prog = compile_program(link_ast(src, ast), self.api, self.budget, callback_hints())
        except RecursionError:
            raise PdeError("depth", "the code is nested too deeply") from None
        self.started = False
        self.done = False
        self.error: PdeError | None = None
        self.frame = 0                 # frames produced (== frameCount of the last draw)
        self.since_sweep = 0
        self.last_steps = 0
        self.last_ms = 0.0

    # ------------------------------------------------------------------ running
    def _guard(self, fn) -> None:
        try:
            fn()
        except PdeError:
            raise
        except RecursionError:
            raise PdeError("depth", "the sketch recursed too deeply") from None
        except MemoryError:
            raise PdeError("memory", "the sketch ran out of memory") from None
        except (IndexError, KeyError) as e:
            raise PdeError("runtime", f"index out of range ({e})") from None
        except (TypeError, AttributeError):
            raise PdeError("runtime", "a null value was used (NullPointerException)") from None
        except (ValueError, ZeroDivisionError, OverflowError, ArithmeticError) as e:
            raise PdeError("runtime", f"arithmetic error ({type(e).__name__})") from None

    def _call(self, sig) -> None:
        F = [None] * sig.nslots
        sig.body(F)

    def next_frame(self) -> bytes | None:
        if self.error is not None:
            raise self.error
        if self.done:
            return None
        api, prog, B = self.api, self.prog, self.budget
        t0 = time.monotonic()
        try:
            if not self.started:
                self.started = True
                B.reset(self.seconds)
                self._guard(prog.globals_init)
                # the oracle: randomSeed(0) and noiseSeed(0) after construction, before setup()
                api.rand.seed(0)
                api.rand.noise_seed(0)
                B.reset(self.seconds)
                if prog.static is not None:
                    self._guard(prog.static)
                if prog.setup is not None:
                    self._guard(lambda: self._call(prog.setup))
                self._sweep(force=False)
                if api.shape is not None:
                    api.shape = None
            B.reset(self.seconds)
            api.frame_count += 1
            # PGraphics.beginDraw(), as Processing runs before every draw(): the matrix starts again
            # from identity (translate/rotate/scale do NOT carry over), while the style (fill, stroke,
            # textures...) and the push stack do -- an unbalanced pushMatrix() fails on the 33rd
            # frame, as in Processing 4.5.2 (measured, tests/test_pde_oracle.py).
            api.reset_matrix()
            if api.t3 is not None:
                api.t3.begin_frame()
            if prog.draw is not None:
                self._guard(lambda: self._call(prog.draw))
            api.shape = None
            self.frame += 1
            self._sweep(force=False)
            self.last_steps = B.steps
            self.last_ms = (time.monotonic() - t0) * 1000
            if prog.draw is None or not api.looping or api.exited:
                self.done = True
            return api.grey()
        except PdeError as e:
            self.error = e
            raise

    # ------------------------------------------------------------------ memory
    def _sweep(self, force: bool) -> None:
        """Measure what the globals still reach after a frame; over the cap is a memory error."""
        self.since_sweep += self.budget.alloc
        if not force and self.since_sweep < _SWEEP_EVERY:
            return
        self.since_sweep = 0
        total = 0
        seen: set[int] = set()
        stack = list(self.prog.G)
        visited = 0
        while stack:
            v = stack.pop()
            if v is None or isinstance(v, (bool, int, float)):
                continue
            if isinstance(v, str):
                total += len(v)
                continue
            if id(v) in seen:
                continue
            seen.add(id(v))
            visited += 1
            if visited > 2_000_000:
                raise PdeError("memory", "the sketch keeps too many things")
            if isinstance(v, array):
                total += len(v) * v.itemsize
            elif isinstance(v, list):
                total += 8 * len(v) + 56
                stack.extend(v)
            elif isinstance(v, JList):
                total += 8 * len(v.a) + 64
                stack.extend(v.a)
            elif isinstance(v, Obj):
                total += 64 + 8 * len(v.f)
                stack.extend(v.f)
            elif isinstance(v, PVec):
                total += 64
            elif isinstance(v, JMap):
                total += 64 + 16 * len(v.d)
                stack.extend(v.d.keys())
                stack.extend(v.d.values())
            elif isinstance(v, PImg):
                total += 64 + 4 * len(v.pixels)
            elif isinstance(v, PGr):
                total += 2 * W * H + 256
            if total > self.live_bytes:
                raise PdeError("memory", f"the sketch keeps more than {self.live_bytes >> 20} MB")


def render(src: str, frames: int, **limits) -> dict:
    """Run a sketch for up to `frames` frames: {'frames': [grey], 'error': dict | None, 'still': bool,
    'ms': [per frame], 'steps': [per frame]}. Never raises for the sketch's own faults."""
    out: dict = {"frames": [], "error": None, "still": False, "ms": [], "steps": []}
    try:
        sk = Sketch(src, **limits)
    except PdeError as e:
        out["error"] = e.as_dict()
        return out
    for _ in range(max(1, frames)):
        try:
            g = sk.next_frame()
        except PdeError as e:
            out["error"] = e.as_dict()
            break
        if g is None:
            break
        out["frames"].append(g)
        out["ms"].append(round(sk.last_ms, 2))
        out["steps"].append(sk.last_steps)
    out["still"] = sk.done
    return out
