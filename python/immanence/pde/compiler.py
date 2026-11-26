"""AST -> a tree of Python closures, with Java's static types deciding every operation.

No `eval`, `exec` or `compile` anywhere: each node becomes a small Python function that calls its
children. What a sketch can reach is exactly what this file builds: its own variables, functions
and classes, the Processing API in builtins.py, and a fixed set of methods on strings, arrays,
PVector and ArrayList. Nothing here looks up a Python attribute by a name the sketch chose.

Types: "int" "long" "short" "byte" "char" "float" "double" "boolean" "String" "null" "void",
("[]", T) arrays, ("C", name, args) classes. Values: Python int (wrapped to the Java width),
float (float variables hold values rounded to 32 bits), bool, str, None, array.array for
primitive arrays (float[] is array('f'): Java float storage for free), list for the rest,
Obj for sketch classes, PVec / JList for PVector / ArrayList.

Control flow: a statement returns None, or BRK / CNT / RET. Frames: L[0] return value, L[1] this,
then parameters and locals by slot. Loops and calls tick the frame budget.
"""
from __future__ import annotations

import math
from array import array

from .errors import PdeError
from .jrandom import _F, f32

_P, _U = _F.pack, _F.unpack
_INF = math.inf
from .runtime import CONST, Api, Budget, PFont

BRK, CNT, RET = 1, 2, 3
MAX_DEPTH = 64
MAX_STRING = 65536
MAX_ARRAY = 4_000_000

INTS = ("int", "long", "short", "byte", "char")
FLOATS = ("float", "double")
NUMS = INTS + FLOATS
_RANK = {"byte": 1, "short": 2, "char": 2, "int": 3, "long": 4, "float": 5, "double": 6}
_TYPECODE = {"int": "i", "long": "q", "short": "h", "byte": "b", "char": "H", "float": "f", "double": "d"}
_ITEMSIZE = {"i": 4, "q": 8, "h": 2, "b": 1, "H": 2, "f": 4, "d": 8}
_BOX = {"Integer": "int", "Float": "float", "Double": "double", "Long": "long", "Boolean": "boolean",
        "Character": "char", "Short": "short", "Byte": "byte"}
NOCONST = object()


def tname(t) -> str:
    if isinstance(t, tuple):
        if t[0] == "[]":
            return tname(t[1]) + "[]"
        return t[1] + ("<" + ", ".join(tname(a) for a in t[2]) + ">" if t[2] else "")
    return t


def norm(t):
    """Boxed primitives in generics read as the primitives."""
    if isinstance(t, tuple) and t[0] == "C" and t[1] in _BOX:
        return _BOX[t[1]]
    if isinstance(t, tuple) and t[0] == "C" and t[1] == "String":
        return "String"
    if isinstance(t, tuple) and t[0] == "[]":
        return ("[]", norm(t[1]))
    if isinstance(t, tuple) and t[0] == "C" and t[2]:
        return ("C", t[1], tuple(norm(a) for a in t[2]))
    return t


def is_ref(t) -> bool:
    return t == "String" or t == "null" or isinstance(t, tuple)


def sig_text(sig) -> str:
    """`float shade(float x, float y)`: a callable as an error message names it (Sig or the VM
    compiler's Func -- both carry name/params/ret)."""
    return f"{tname(sig.ret)} {sig.name}(" + ", ".join(tname(p) for p in sig.params) + ")"


def sigs_text(sigs) -> str:
    return " or ".join(sig_text(s) for s in sigs)


def w32(v: int) -> int:
    return ((v + 0x80000000) & 0xFFFFFFFF) - 0x80000000


def w64(v: int) -> int:
    return ((v + 0x8000000000000000) & 0xFFFFFFFFFFFFFFFF) - 0x8000000000000000


def w16(v: int) -> int:
    return ((v + 0x8000) & 0xFFFF) - 0x8000


def w8(v: int) -> int:
    return ((v + 0x80) & 0xFF) - 0x80


def d2i(v: float) -> int:
    if v != v:
        return 0
    if v >= 2147483647.0:
        return 2147483647
    if v <= -2147483648.0:
        return -2147483648
    return int(v)


def d2l(v: float) -> int:
    if v != v:
        return 0
    if v >= 9.223372036854776e18:
        return 9223372036854775807
    if v <= -9.223372036854776e18:
        return -9223372036854775808
    return int(v)


def fdiv(a, b):
    try:
        return a / b
    except ZeroDivisionError:
        if a != a or a == 0:
            return math.nan
        return math.copysign(math.inf, a) * math.copysign(1.0, b)


def fmod(a, b):
    try:
        return math.fmod(a, b)
    except (ZeroDivisionError, ValueError):
        return math.nan


def idiv(a, b):
    if b == 0:
        raise PdeError("runtime", "division by zero")
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def imod(a, b):
    if b == 0:
        raise PdeError("runtime", "division by zero")
    r = abs(a) % abs(b)
    return -r if a < 0 else r


def jfloat_str(v: float, single: bool = True) -> str:
    """Java's Float.toString / Double.toString (shortest digits that read back the same)."""
    if v != v:
        return "NaN"
    if v in (math.inf, -math.inf):
        return "Infinity" if v > 0 else "-Infinity"
    if v == 0:
        return "-0.0" if math.copysign(1.0, v) < 0 else "0.0"
    v = float(v)
    p = 0
    top = 9 if single else 17
    while p < top:
        s = f"{v:.{p}e}"
        back = float(s)
        if (f32(back) if single else back) == v:
            break
        p += 1
    s = f"{v:.{p}e}"
    mant, exp = s.split("e")
    neg = mant.startswith("-")
    digits = mant.lstrip("-").replace(".", "").rstrip("0") or "0"
    e = int(exp)
    sign = "-" if neg else ""
    if 1e-3 <= abs(v) < 1e7:
        if e >= 0:
            ip = digits[:e + 1].ljust(e + 1, "0")
            fp = digits[e + 1:] or "0"
        else:
            ip = "0"
            fp = "0" * (-e - 1) + digits
        return f"{sign}{ip}.{fp}"
    return f"{sign}{digits[0]}.{digits[1:] or '0'}E{e}"


# ---------------------------------------------------------------------- runtime objects
class Obj:
    """An instance of a sketch's own class: its class record and its field values."""
    __slots__ = ("cls", "f")

    def __init__(self, cls, f) -> None:
        self.cls = cls
        self.f = f


class PVec:
    __slots__ = ("x", "y", "z")

    def __init__(self, x=0.0, y=0.0, z=0.0) -> None:
        self.x, self.y, self.z = f32(x), f32(y), f32(z)


class JList:
    __slots__ = ("a",)

    def __init__(self) -> None:
        self.a: list = []


def jstr(v, t) -> str:
    if t == "String":
        return "null" if v is None else v
    if t == "boolean":
        return "true" if v else "false"
    if t == "char":
        return chr(v & 0xFFFF)
    if t in ("int", "long", "short", "byte"):
        return str(v)
    if t == "float":
        return jfloat_str(v, True)
    if t == "double":
        return jfloat_str(v, False)
    if v is None:
        return "null"
    if isinstance(v, PVec):
        return f"[ {jfloat_str(v.x)}, {jfloat_str(v.y)}, {jfloat_str(v.z)} ]"
    if isinstance(v, JList):
        et = t[2][0] if isinstance(t, tuple) and len(t) > 2 and t[2] else "Object"
        return "[" + ", ".join(jstr(x, norm(et)) for x in v.a[:200]) + "]"
    if isinstance(v, Obj):
        return f"{v.cls.name}@{id(v) & 0xFFFFFF:x}"
    return f"{tname(t)}@{id(v) & 0xFFFFFF:x}"


def default_value(t):
    if t in ("int", "long", "short", "byte", "char"):
        return 0
    if t in FLOATS:
        return 0.0
    if t == "boolean":
        return False
    return None


# ---------------------------------------------------------------------- compiled expressions
class E:
    """A compiled expression: its closure, static type, constant value (NOCONST if not constant)
    and whether a float value is already rounded to 32 bits."""
    __slots__ = ("fn", "t", "const", "exact")

    def __init__(self, fn, t, const=NOCONST, exact=False) -> None:
        self.fn, self.t, self.const, self.exact = fn, t, const, exact


def const_e(v, t) -> E:
    if t == "float":
        v = f32(v)
    return E(lambda L, v=v: v, t, v, True)


class Sig:
    """A user function, method or constructor: parameter types, return type, frame size, body."""
    __slots__ = ("name", "params", "ret", "nslots", "body", "cls", "node")

    def __init__(self, name, params, ret, node, cls=None) -> None:
        self.name, self.params, self.ret, self.node, self.cls = name, params, ret, node, cls
        self.nslots = 2 + len(params)
        self.body = None


class ClassInfo:
    def __init__(self, name) -> None:
        self.name = name
        self.fields: dict[str, tuple[int, object]] = {}
        self.inits: list = []
        self.methods: dict[str, list[Sig]] = {}
        self.ctors: list[Sig] = []
        self.init_slots = 2


class Program:
    """A compiled sketch bound to its runtime: call run_globals() once, then setup(), then draw()."""

    def __init__(self) -> None:
        self.G: list = []
        self.globals_init = None
        self.setup: Sig | None = None
        self.draw: Sig | None = None
        self.static = None          # top-level statements (static mode, or run before setup)
        self.gtypes: dict[str, tuple[int, object]] = {}


# ---------------------------------------------------------------------- the compiler
class Compiler:
    def __init__(self, api: Api, budget: Budget, hints: dict | None = None) -> None:
        self.api = api
        self.B = budget
        self.prog = Program()
        self.funcs: dict[str, list[Sig]] = {}
        self.classes: dict[str, ClassInfo] = {}
        self.scopes: list[dict] = []
        self.nslots = 2
        self.maxslots = 2
        self.cls: ClassInfo | None = None
        self.ret_t = "void"
        self.loop_depth = 0
        self.in_global_init = False
        self._class_nodes: dict[str, list] = {}
        # names the art library asks the SKETCH to write (mathlib.CALLBACKS through
        # pde.callback_hints): calling one without writing it says so instead of "not defined"
        self.hints: dict[str, str] = hints or {}

    # ------------------------------------------------------------------ errors
    @staticmethod
    def err(msg: str, line: int, kind: str = "compile"):
        raise PdeError(kind, msg, line)

    def callback_hint(self, name: str, line: int) -> None:
        """`name` is one the art library asks the sketch to write (shadeField() calls the sketch's
        float shade(float x, float y), plot() its fy ...): the library has no such function, so a
        bare "is not defined" hides who wants it (a piece read the reference's `shadeField(cell)
        fills the frame from YOUR float shade(float x, float y)` as a library call)."""
        msg = self.hints.get(name)
        if msg:
            self.err(msg, line)

    # ------------------------------------------------------------------ program
    def compile(self, items: list) -> Program:
        prog = self.prog
        globals_: list = []
        statics: list = []
        # pass 1: classes, function signatures, global names
        for it in items:
            if it[0] == "class":
                if it[2] in self.classes:
                    self.err(f"class {it[2]} is declared twice", it[1])
                self.classes[it[2]] = ClassInfo(it[2])
                self._class_nodes[it[2]] = it[3]
        for it in items:
            k = it[0]
            if k == "class":
                self.declare_class(it)
            elif k == "func":
                _, line, ret, name, params, body = it
                sig = Sig(name, [self.check_type(norm(p[0]), line) for p in params], self.check_type(norm(ret), line), it)
                self.funcs.setdefault(name, []).append(sig)
            elif k == "global":
                globals_.append(it)
                for t, name, init in it[2][2]:
                    t = self.check_type(norm(t), it[1])
                    if name in prog.gtypes:
                        self.err(f"{name} is declared twice", it[1])
                    prog.gtypes[name] = (len(prog.G), t)
                    prog.G.append(default_value(t))
            elif k == "stmt":
                statics.append(it[2])
        # pass 2: global initialisers, in order (Processing runs them before settings/setup)
        inits = []
        self.in_global_init = True
        self.begin_function()
        for it in globals_:
            for t, name, init in it[2][2]:
                if init is None:
                    continue
                slot, gt = prog.gtypes[name]
                e = self.store_conv(self.value_for(init, gt, it[1]), gt, it[1])
                inits.append((slot, e.fn))
        g_slots = self.maxslots
        self.in_global_init = False
        G = prog.G

        def run_globals():
            L = [None] * g_slots
            for slot, fn in inits:
                G[slot] = fn(L)
        prog.globals_init = run_globals
        # pass 3: bodies
        for ci in self.classes.values():
            self.compile_class(ci)
        for sigs in self.funcs.values():
            for sig in sigs:
                self.compile_func(sig)
        for name in ("setup", "draw"):
            for sig in self.funcs.get(name, []):
                if not sig.params:
                    setattr(prog, name, sig)
        if statics:
            self.begin_function()
            body = self.block_of(statics, 0)
            n = self.maxslots

            def run_static(body=body, n=n):
                body([None] * n)
            prog.static = run_static
        return prog

    def check_type(self, t, line):
        if isinstance(t, tuple):
            if t[0] == "[]":
                self.check_type(t[1], line)
            elif t[1] not in self.classes and t[1] not in ("PVector", "ArrayList", "PFont", "Object", "List",
                                                            "PImage", "PGraphics", "IntList", "FloatList",
                                                            "StringList", "HashMap", "Map"):
                self.err(f"the type {t[1]} is not available here", line)
        return t

    # ------------------------------------------------------------------ scopes
    def begin_function(self, params=()):
        self.scopes = [{}]
        self.nslots = 2
        self.maxslots = 2
        for t, name in params:
            self.declare_local(name, t, 0)

    def declare_local(self, name, t, line) -> int:
        if name in self.scopes[-1]:
            self.err(f"{name} is already defined here", line)
        slot = self.nslots
        self.nslots += 1
        self.maxslots = max(self.maxslots, self.nslots)
        self.scopes[-1][name] = (slot, t)
        return slot

    def push_scope(self):
        self.scopes.append({})

    def pop_scope(self):
        sc = self.scopes.pop()
        self.nslots -= len(sc)

    def find_local(self, name):
        for sc in reversed(self.scopes):
            if name in sc:
                return sc[name]
        return None

    # ------------------------------------------------------------------ classes
    def declare_class(self, it):
        _, line, name, members = it
        ci = self.classes[name]
        for m in members:
            k = m[0]
            if k == "global":
                for t, fname, init in m[2][2]:
                    t = self.check_type(norm(t), m[1])
                    ci.fields[fname] = (len(ci.fields), t)
            elif k == "func":
                _, mline, ret, mname, params, body = m
                sig = Sig(mname, [self.check_type(norm(p[0]), mline) for p in params],
                          self.check_type(norm(ret), mline), m, ci)
                ci.methods.setdefault(mname, []).append(sig)
            elif k == "ctor":
                _, cline, _, params, body = m
                sig = Sig(name, [self.check_type(norm(p[0]), cline) for p in params], "void",
                          ("func", cline, "void", name, params, body), ci)
                ci.ctors.append(sig)
            elif k == "class":
                self.err("a class inside a class is not available here", m[1])

    def compile_class(self, ci: ClassInfo):
        self.cls = ci
        self.begin_function()
        for it in [m for m in self._class_nodes.get(ci.name, []) if m[0] == "global"]:
            for t, fname, init in it[2][2]:
                if init is None:
                    continue
                idx, ft = ci.fields[fname]
                e = self.store_conv(self.value_for(init, ft, it[1]), ft, it[1])
                ci.inits.append((idx, e.fn))
        ci.init_slots = self.maxslots
        for sigs in ci.methods.values():
            for sig in sigs:
                self.compile_func(sig)
        for sig in ci.ctors:
            self.compile_func(sig)
        self.cls = None

    def compile_func(self, sig: Sig):
        _, line, ret, name, params, body = sig.node
        self.cls = sig.cls
        self.ret_t = sig.ret
        self.begin_function([(t, p[1]) for t, p in zip(sig.params, params)])
        fn = self.stmt(body)
        sig.nslots = self.maxslots
        sig.body = fn
        self.cls = None

    # ------------------------------------------------------------------ statements
    def block_of(self, stmts, line):
        self.push_scope()
        fns = [self.stmt(s) for s in stmts]
        self.pop_scope()
        fns = [f for f in fns if f is not None]
        if not fns:
            return lambda L: None
        if len(fns) == 1:
            return fns[0]
        if len(fns) == 2:
            a, b = fns

            def two(L):
                r = a(L)
                if r:
                    return r
                return b(L)
            return two

        def run(L):
            for f in fns:
                r = f(L)
                if r:
                    return r
        return run

    def stmt(self, s):
        k = s[0]
        m = getattr(self, "s_" + k, None)
        if m is None:
            self.err(f"{k} is not available here", s[1])
        return m(s)

    def s_block(self, s):
        return self.block_of(s[2], s[1])

    def s_empty(self, s):
        return None

    def s_expr(self, s):
        e = self.expr(s[2])
        f = e.fn

        def run(L):
            f(L)
        return run

    def s_vardecl(self, s):
        fns = []
        for t, name, init in s[2]:
            t = self.check_type(norm(t), s[1])
            if init is not None:
                e = self.store_conv(self.value_for(init, t, s[1]), t, s[1])
            else:
                e = None
            slot = self.declare_local(name, t, s[1])
            if e is None:
                dv = default_value(t)

                def st(L, slot=slot, dv=dv):
                    L[slot] = dv
            else:
                def st(L, slot=slot, f=e.fn):
                    L[slot] = f(L)
            fns.append(st)
        if len(fns) == 1:
            return fns[0]

        def run(L):
            for f in fns:
                f(L)
        return run

    def s_if(self, s):
        c = self.cond(s[2])
        then = self.stmt(s[3]) or (lambda L: None)
        if s[4] is None:
            def run(L):
                if c(L):
                    return then(L)
            return run
        other = self.stmt(s[4]) or (lambda L: None)

        def run2(L):
            if c(L):
                return then(L)
            return other(L)
        return run2

    def _loop_body(self, s):
        self.loop_depth += 1
        body = self.stmt(s) or (lambda L: None)
        self.loop_depth -= 1
        return body

    def s_while(self, s):
        c = self.cond(s[2])
        body = self._loop_body(s[3])
        B = self.B

        def run(L):
            while c(L):
                r = body(L)
                if r:
                    if r == BRK:
                        break
                    if r == RET:
                        return r
                B.steps += 1
                if B.steps >= B.next:
                    B.check()
        return run

    def s_do(self, s):
        body = self._loop_body(s[2])
        c = self.cond(s[3])
        B = self.B

        def run(L):
            while True:
                r = body(L)
                if r:
                    if r == BRK:
                        break
                    if r == RET:
                        return r
                B.steps += 1
                if B.steps >= B.next:
                    B.check()
                if not c(L):
                    break
        return run

    def s_for(self, s):
        _, line, init, cond, update, body = s
        self.push_scope()
        inits = [f for f in (self.stmt(i) for i in init) if f is not None]
        c = self.cond(cond) if cond is not None else (lambda L: True)
        ups = [self.expr(u).fn for u in update]
        b = self._loop_body(body)
        self.pop_scope()
        B = self.B
        if len(ups) == 1:
            u = ups[0]

            def run1(L):
                for f in inits:
                    f(L)
                while c(L):
                    r = b(L)
                    if r:
                        if r == BRK:
                            break
                        if r == RET:
                            return r
                    u(L)
                    B.steps += 1
                    if B.steps >= B.next:
                        B.check()
            return run1

        def run(L):
            for f in inits:
                f(L)
            while c(L):
                r = b(L)
                if r:
                    if r == BRK:
                        break
                    if r == RET:
                        return r
                for f in ups:
                    f(L)
                B.steps += 1
                if B.steps >= B.next:
                    B.check()
        return run

    def s_foreach(self, s):
        _, line, t, name, it, body = s
        src = self.expr(it)
        t = self.check_type(norm(t), line)
        st = src.t
        if isinstance(st, tuple) and st[0] == "[]":
            et = st[1]
        elif isinstance(st, tuple) and st[0] == "C" and st[1] in ("ArrayList", "List"):
            et = norm(st[2][0]) if st[2] else ("C", "Object", ())
        elif isinstance(st, tuple) and st[0] == "C" and st[1] in ("IntList", "FloatList", "StringList"):
            et = {"IntList": "int", "FloatList": "float", "StringList": "String"}[st[1]]
        else:
            self.err(f"cannot loop over a {tname(st)}", line)
        conv = self.store_conv(E(lambda L: L[0], et), t, line).fn   # converts the element (read from L[0] slot)
        self.push_scope()
        slot = self.declare_local(name, t, line)
        b = self._loop_body(body)
        self.pop_scope()
        f = src.fn
        B = self.B
        is_list = not (isinstance(st, tuple) and st[0] == "[]")

        def run(L):
            coll = f(L)
            if coll is None:
                raise PdeError("runtime", "the loop's collection is null", line)
            items = list(coll.a) if is_list else coll
            keep = L[0]
            for v in items:
                L[0] = v
                L[slot] = conv(L)
                L[0] = keep
                r = b(L)
                if r:
                    if r == BRK:
                        break
                    if r == RET:
                        return r
                B.steps += 1
                if B.steps >= B.next:
                    B.check()
        return run

    def s_switch(self, s):
        _, line, e, cases = s
        se = self.expr(e)
        if se.t not in INTS and se.t != "String":
            self.err("switch needs an int, a char or a String", line)
        table: dict = {}
        default = None
        bodies = []
        self.push_scope()
        self.loop_depth += 1
        for labels, body in cases:
            idx = len(bodies)
            if labels is None:
                default = idx
            else:
                for lab in labels:
                    le = self.expr(lab)
                    if le.const is NOCONST:
                        self.err("a case label must be a constant", line)
                    table[le.const] = idx
            fns = [f for f in (self.stmt(b) for b in body) if f is not None]
            bodies.append(fns)
        self.loop_depth -= 1
        self.pop_scope()
        flat = []
        starts = []
        for fns in bodies:
            starts.append(len(flat))
            flat.extend(fns)
        f = se.fn

        def run(L):
            v = f(L)
            i = table.get(v, default)
            if i is None:
                return None
            for g in flat[starts[i]:]:
                r = g(L)
                if r:
                    if r == BRK:
                        return None
                    return r
        return run

    def s_break(self, s):
        if s[2] is not None:
            self.err("labelled break is not available here", s[1])
        if not self.loop_depth:
            self.err("break outside a loop or switch", s[1])
        return lambda L: BRK

    def s_continue(self, s):
        if s[2] is not None:
            self.err("labelled continue is not available here", s[1])
        if not self.loop_depth:
            self.err("continue outside a loop", s[1])
        return lambda L: CNT

    def s_labeled(self, s):
        return self.stmt(s[3])

    def s_return(self, s):
        if s[2] is None:
            return lambda L: RET
        if self.ret_t == "void":
            self.err("a void function cannot return a value", s[1])
        e = self.store_conv(self.value_for(s[2], self.ret_t, s[1]), self.ret_t, s[1])
        f = e.fn

        def run(L):
            L[0] = f(L)
            return RET
        return run

    def s_vardecl_init(self, s):   # pragma: no cover - not a node kind
        return None

    # ------------------------------------------------------------------ conversions
    def value_for(self, node, t, line) -> E:
        """An initialiser or assigned value: array initialisers need the target type."""
        if node[0] == "arrinit":
            if not (isinstance(t, tuple) and t[0] == "[]"):
                self.err("{...} needs an array type", line)
            return self.array_init(node, t)
        return self.expr(node)

    def conv(self, e: E, to, line, explicit=False) -> E:
        src = e.t
        if src == to:
            return e
        if e.const is not NOCONST and src in NUMS + ("boolean",) and to in NUMS:
            if src == "boolean":
                self.err(f"cannot convert boolean to {to}", line)
            v = self._const_conv(e.const, src, to)
            return const_e(v, to)
        f = e.fn
        if to in NUMS and src in NUMS:
            if to == "double":
                if src in INTS:
                    return E(lambda L: float(f(L)), to)
                return E(f, to, exact=e.exact)
            if to == "float":
                if src in INTS:
                    # an int below 2^24 is exact in 32 bits: only bigger ones pay for the rounding
                    return E(lambda L: float(v) if -16777216 <= (v := f(L)) <= 16777216 else f32(float(v)),
                             to, exact=True)
                return E(lambda L: f32(f(L)), to, exact=True)
            if src in FLOATS:
                if to == "long":
                    return E(lambda L: d2l(f(L)), to)
                if to == "int":
                    return E(lambda L: d2i(f(L)), to)
                if to == "short":
                    return E(lambda L: w16(d2i(f(L))), to)
                if to == "byte":
                    return E(lambda L: w8(d2i(f(L))), to)
                return E(lambda L: d2i(f(L)) & 0xFFFF, to)
            if to == "long":
                return E(f, to)
            if to == "int":
                return E(f, to) if src != "long" else E(lambda L: w32(f(L)), to)
            if to == "short":
                return E(f, to) if src == "byte" else E(lambda L: w16(f(L)), to)
            if to == "byte":
                return E(lambda L: w8(f(L)), to)
            if to == "char":
                return E(lambda L: f(L) & 0xFFFF, to)
        if to == "String":
            if src == "null":
                return E(f, to)
            return E(lambda L: jstr(f(L), src), to)   # lenient: Java would need "" + x
        if src == "null" and is_ref(to):
            return E(f, to)
        if is_ref(to) and is_ref(src):
            if isinstance(to, tuple) and isinstance(src, tuple):
                if to[0] != src[0] or (to[0] == "C" and to[1] not in ("Object", "List") and src[1] != to[1]
                                       and not (to[1] in ("ArrayList", "List") and src[1] in ("ArrayList", "List"))
                                       and not (to[1] in ("HashMap", "Map") and src[1] in ("HashMap", "Map"))
                                       and not (to[1] == "PImage" and src[1] == "PGraphics")):
                    if to[0] == "[]" and src[0] == "[]":
                        return E(f, to)
                    self.err(f"cannot convert {tname(src)} to {tname(to)}", line)
            elif to != "Object" and src != to and not (isinstance(to, tuple) and to[1] == "Object"):
                self.err(f"cannot convert {tname(src)} to {tname(to)}", line)
            return E(f, to)
        if isinstance(src, tuple) and src[0] == "C" and src[1] == "Object" and to in NUMS + ("boolean",):
            return E(f, to)
        if src == "boolean" or to == "boolean":
            self.err(f"cannot convert {tname(src)} to {tname(to)}", line)
        self.err(f"cannot convert {tname(src)} to {tname(to)}", line)

    @staticmethod
    def _const_conv(v, src, to):
        if to == "double":
            return float(v)
        if to == "float":
            return f32(float(v))
        if src in FLOATS:
            v = d2l(v) if to == "long" else d2i(v)
        if to == "int":
            return w32(v)
        if to == "long":
            return w64(v)
        if to == "short":
            return w16(v)
        if to == "byte":
            return w8(v)
        if to == "char":
            return v & 0xFFFF
        return v

    def store_conv(self, e: E, t, line) -> E:
        """A value on its way into a variable of type t: converted (leniently narrowing, like a
        cast) and, for float, rounded to 32 bits."""
        if e.t != t and e.t in NUMS and t in NUMS:
            e = self.conv(e, t, line, explicit=True)
        elif e.t != t:
            e = self.conv(e, t, line)
        if t == "float" and not e.exact:
            f = e.fn
            return E(lambda L: f32(f(L)), t, exact=True)
        return e

    def cond(self, node):
        e = self.expr(node)
        if e.t != "boolean":
            self.err(f"a condition must be true/false, not {tname(e.t)}", node[1])
        return e.fn

    # ------------------------------------------------------------------ expressions
    def expr(self, n) -> E:
        m = getattr(self, "e_" + n[0], None)
        if m is None:
            self.err(f"{n[0]} is not available here", n[1])
        return m(n)

    def e_lit(self, n):
        _, line, t, v = n
        if t == "String" and len(v) > MAX_STRING:
            self.err("the string is too long", line)
        return const_e(v, t)

    def e_this(self, n):
        if self.cls is None:
            self.err("this is only available inside a class", n[1])
        return E(lambda L: L[1], ("C", self.cls.name, ()))

    def e_name(self, n):
        _, line, name = n
        loc = self.find_local(name)
        if loc is not None:
            slot, t = loc
            return E(lambda L: L[slot], t, exact=(t == "float"))
        if self.cls is not None and name in self.cls.fields:
            idx, t = self.cls.fields[name]
            return E(lambda L: L[1].f[idx], t, exact=(t == "float"))
        g = self.prog.gtypes.get(name)
        if g is not None:
            slot, t = g
            G = self.prog.G
            return E(lambda L: G[slot], t, exact=(t == "float"))
        return self.builtin_var(name, line)

    def builtin_var(self, name, line) -> E:
        api = self.api
        if name in CONST:
            t, v = CONST[name]
            return const_e(v, t)
        if name == "width":
            if self.in_global_init:
                return const_e(100, "int")        # PApplet's width before size() takes effect
            return const_e(240, "int")
        if name == "height":
            if self.in_global_init:
                return const_e(100, "int")
            return const_e(128, "int")
        if name == "frameCount":
            return E(lambda L: api.frame_count, "int")
        if name == "frameRate":
            return const_e(60.0, "float")
        if name in ("mouseX", "mouseY", "pmouseX", "pmouseY", "keyCode"):
            return const_e(0, "int")
        if name in ("mousePressed", "keyPressed", "focused"):
            return const_e(False, "boolean")
        if name == "key":
            return const_e(0, "char")
        if name in ("displayWidth", "displayHeight"):
            return const_e(240 if name == "displayWidth" else 128, "int")
        if name == "pixels":
            def px(L):
                if api.pixels is None:
                    raise PdeError("runtime", "pixels[] is empty until loadPixels()", line)
                return api.pixels
            return E(px, ("[]", "int"))
        if name in ("Math", "PVector", "String", "Integer", "Float", "Double", "Long", "Character", "Boolean",
                    "PApplet", "System"):
            return E(lambda L: None, ("static", name))
        self.callback_hint(name, line)
        self.err(f"{name} is not defined", line)

    # -------------------------------------------------------------- operators
    def e_un(self, n):
        _, line, op, a = n
        e = self.expr(a)
        f = e.fn
        if op == "!":
            if e.t != "boolean":
                self.err("! needs true/false", line)
            if e.const is not NOCONST:
                return const_e(not e.const, "boolean")
            return E(lambda L: not f(L), "boolean")
        if e.t not in NUMS:
            self.err(f"{op} needs a number", line)
        t = e.t if _RANK[e.t] >= 3 else "int"
        if op == "+":
            return self.conv(e, t, line) if t != e.t else e
        if op == "~":
            if t not in ("int", "long"):
                self.err("~ needs an integer", line)
            return E(lambda L: ~f(L), t)
        e = self.conv(e, t, line) if t != e.t else e
        f = e.fn
        if e.const is not NOCONST:
            v = -e.const
            return const_e(w32(v) if t == "int" else w64(v) if t == "long" else v, t)
        if t == "int":
            return E(lambda L: w32(-f(L)), t)
        if t == "long":
            return E(lambda L: w64(-f(L)), t)
        return E(lambda L: -f(L), t, exact=e.exact)

    def promote(self, a: E, b: E, line, op):
        if a.t not in NUMS or b.t not in NUMS:
            self.err(f"{op} needs numbers, not {tname(a.t)} and {tname(b.t)}", line)
        ra, rb = _RANK[a.t], _RANK[b.t]
        if "double" in (a.t, b.t):
            t = "double"
        elif "float" in (a.t, b.t):
            t = "float"
        elif "long" in (a.t, b.t):
            t = "long"
        else:
            t = "int"
        if ra < 3 and a.t != t:
            a = self.conv(a, "int", line)
        if rb < 3 and b.t != t:
            b = self.conv(b, "int", line)
        return (self.conv(a, t, line) if a.t != t else a), (self.conv(b, t, line) if b.t != t else b), t

    def e_bin(self, n):
        _, line, op, an, bn = n
        if op in ("&&", "||"):
            a, b = self.expr(an), self.expr(bn)
            if a.t != "boolean" or b.t != "boolean":
                self.err(f"{op} needs true/false on both sides", line)
            fa, fb = a.fn, b.fn
            if op == "&&":
                return E(lambda L: fa(L) and fb(L), "boolean")
            return E(lambda L: fa(L) or fb(L), "boolean")
        a, b = self.expr(an), self.expr(bn)
        return self.binop(op, a, b, line)

    def binop(self, op, a: E, b: E, line) -> E:
        if op == "+" and ("String" in (a.t, b.t)):
            return self.concat(a, b, line)
        if op in ("==", "!="):
            return self.equality(op, a, b, line)
        if op in ("&", "|", "^") and a.t == "boolean" and b.t == "boolean":
            fa, fb = a.fn, b.fn
            fn = {"&": lambda L: fa(L) & fb(L), "|": lambda L: fa(L) | fb(L), "^": lambda L: fa(L) ^ fb(L)}[op]
            return E(fn, "boolean")
        if op in ("<<", ">>", ">>>"):
            if a.t not in INTS or b.t not in INTS:
                self.err(f"{op} needs integers", line)
            t = "long" if a.t == "long" else "int"
            a = self.conv(a, t, line) if a.t != t else a
            fa, fb = a.fn, b.fn
            bits, mask, wrap = (32, 31, w32) if t == "int" else (64, 63, w64)
            if op == "<<":
                fn = lambda L: wrap(fa(L) << (fb(L) & mask))
            elif op == ">>":
                fn = lambda L: fa(L) >> (fb(L) & mask)
            else:
                full = (1 << bits) - 1
                fn = lambda L: wrap((fa(L) & full) >> (fb(L) & mask))
            return self.fold(E(fn, t), a, b)
        a, b, t = self.promote(a, b, line, op)
        fa, fb = a.fn, b.fn
        if op in ("<", ">", "<=", ">="):
            fn = {"<": lambda L: fa(L) < fb(L), ">": lambda L: fa(L) > fb(L),
                  "<=": lambda L: fa(L) <= fb(L), ">=": lambda L: fa(L) >= fb(L)}[op]
            return self.fold(E(fn, "boolean"), a, b)
        if op in ("&", "|", "^"):
            if t not in ("int", "long"):
                self.err(f"{op} needs integers", line)
            fn = {"&": lambda L: fa(L) & fb(L), "|": lambda L: fa(L) | fb(L), "^": lambda L: fa(L) ^ fb(L)}[op]
            return self.fold(E(fn, t), a, b)
        if t == "float":
            # Java float arithmetic: every operation rounds to 32 bits (a double product and sum
            # rounded only once at the store differ in the last bit, and a crisp edge can move a
            # pixel on it). The C++ VM does the same in C++ float.
            if op == "+":
                def fn(L):
                    v = fa(L) + fb(L)
                    try:
                        return _U(_P(v))[0]
                    except OverflowError:
                        return _INF if v > 0 else -_INF
            elif op == "-":
                def fn(L):
                    v = fa(L) - fb(L)
                    try:
                        return _U(_P(v))[0]
                    except OverflowError:
                        return _INF if v > 0 else -_INF
            elif op == "*":
                def fn(L):
                    v = fa(L) * fb(L)
                    try:
                        return _U(_P(v))[0]
                    except OverflowError:
                        return _INF if v > 0 else -_INF
            elif op == "/":
                fn = lambda L: f32(fdiv(fa(L), fb(L)))
            else:
                fn = lambda L: f32(fmod(fa(L), fb(L)))
            r = self.fold(E(fn, t, exact=True), a, b)
            if r.const is not NOCONST:
                return const_e(r.const, "float")
            return r
        if t in FLOATS:
            if op == "+":
                fn = lambda L: fa(L) + fb(L)
            elif op == "-":
                fn = lambda L: fa(L) - fb(L)
            elif op == "*":
                fn = lambda L: fa(L) * fb(L)
            elif op == "/":
                fn = lambda L: fdiv(fa(L), fb(L))
            else:
                fn = lambda L: fmod(fa(L), fb(L))
            return self.fold(E(fn, t), a, b)
        wrap = w32 if t == "int" else w64
        if op == "+":
            fn = lambda L: wrap(fa(L) + fb(L))
        elif op == "-":
            fn = lambda L: wrap(fa(L) - fb(L))
        elif op == "*":
            fn = lambda L: wrap(fa(L) * fb(L))
        elif op == "/":
            fn = lambda L: wrap(idiv(fa(L), fb(L)))
        else:
            fn = lambda L: imod(fa(L), fb(L))
        return self.fold(E(fn, t), a, b)

    @staticmethod
    def fold(e: E, a: E, b: E) -> E:
        if a.const is not NOCONST and b.const is not NOCONST:
            try:
                v = e.fn(None)
            except PdeError:
                return e
            return E(lambda L, v=v: v, e.t, v, e.t != "double")
        return e

    def concat(self, a: E, b: E, line) -> E:
        fa, fb, ta, tb = a.fn, b.fn, a.t, b.t
        B = self.B
        sa = (lambda L: fa(L) if (v := fa(L)) is not None else "null") if ta == "String" else None

        def fn(L):
            x = fa(L)
            y = fb(L)
            s = (x if ta == "String" and x is not None else jstr(x, ta)) + \
                (y if tb == "String" and y is not None else jstr(y, tb))
            if len(s) > MAX_STRING:
                raise PdeError("memory", "a string grew past 64 K characters", line)
            B.alloc += len(s)
            if B.alloc > B.alloc_limit:
                B.allocate(0)
            return s
        del sa
        r = E(fn, "String")
        if a.const is not NOCONST and b.const is not NOCONST:
            v = fn(None)
            return E(lambda L, v=v: v, "String", v)
        return r

    def equality(self, op, a: E, b: E, line) -> E:
        if a.t in NUMS and b.t in NUMS:
            a, b, _ = self.promote(a, b, line, op)
        elif (a.t == "boolean") != (b.t == "boolean"):
            self.err(f"cannot compare {tname(a.t)} with {tname(b.t)}", line)
        fa, fb = a.fn, b.fn
        both_str = a.t == "String" and b.t == "String"
        if op == "==":
            fn = (lambda L: fa(L) == fb(L)) if (a.t in NUMS or a.t == "boolean" or both_str) else \
                (lambda L: fa(L) is fb(L))
        else:
            fn = (lambda L: fa(L) != fb(L)) if (a.t in NUMS or a.t == "boolean" or both_str) else \
                (lambda L: fa(L) is not fb(L))
        return self.fold(E(fn, "boolean"), a, b)

    def e_cond(self, n):
        _, line, c, an, bn = n
        cf = self.cond(c)
        a, b = self.expr(an), self.expr(bn)
        if a.t in NUMS and b.t in NUMS and a.t != b.t:
            a, b, _ = self.promote(a, b, line, "?:")
        elif a.t != b.t:
            if a.t == "null":
                a = E(a.fn, b.t)
            elif b.t == "null":
                b = E(b.fn, a.t)
            elif a.t == "String" or b.t == "String":
                a, b = self.conv(a, "String", line), self.conv(b, "String", line)
        fa, fb = a.fn, b.fn
        return E(lambda L: fa(L) if cf(L) else fb(L), a.t, exact=a.exact and b.exact)

    def e_cast(self, n):
        _, line, t, a = n
        t = norm(t)
        e = self.expr(a)
        if t == e.t:
            return e
        if t in NUMS and e.t in NUMS:
            return self.conv(e, t, line, explicit=True)
        if t == "String" and e.t != "String":
            if e.t == "null" or (isinstance(e.t, tuple) and e.t[1] == "Object"):
                return E(e.fn, "String")
            self.err(f"cannot cast {tname(e.t)} to String", line)
        return self.conv(e, t, line, explicit=True)

    # -------------------------------------------------------------- assignment
    def lvalue(self, n):
        """(type, getter(L) -> (container, key) ... ) as a pair of closures: read(L) and write(L, v)
        plus a 'bind' closure that evaluates the container/index once for compound assignment."""
        k = n[0]
        line = n[1]
        if k == "name":
            name = n[2]
            loc = self.find_local(name)
            if loc is not None:
                slot, t = loc
                return t, (lambda L: (L, slot))
            if self.cls is not None and name in self.cls.fields:
                idx, t = self.cls.fields[name]
                return t, (lambda L: (L[1].f, idx))
            g = self.prog.gtypes.get(name)
            if g is not None:
                slot, t = g
                G = self.prog.G
                return t, (lambda L: (G, slot))
            if name in CONST or name in ("width", "height", "frameCount"):
                self.err(f"{name} cannot be changed", line)
            self.callback_hint(name, line)
            self.err(f"{name} is not defined", line)
        if k == "index":
            arr = self.expr(n[2])
            if not (isinstance(arr.t, tuple) and arr.t[0] == "[]"):
                self.err(f"{tname(arr.t)} is not an array", line)
            ix = self.expr(n[3])
            if ix.t not in INTS or ix.t == "long":
                ix = self.conv(ix, "int", line) if ix.t in INTS else self.err("an array index must be an int", line)
            fa, fi = arr.fn, ix.fn

            def bind(L):
                a = fa(L)
                i = fi(L)
                if a is None:
                    raise PdeError("runtime", "the array is null", line)
                if i < 0 or i >= len(a):
                    raise PdeError("runtime", f"index {i} is outside the array (length {len(a)})", line)
                return a, i
            return arr.t[1], bind
        if k == "field":
            obj = self.expr(n[2])
            name = n[3]
            ot = obj.t
            fo = obj.fn
            if isinstance(ot, tuple) and ot[0] == "C" and ot[1] in self.classes:
                ci = self.classes[ot[1]]
                if name not in ci.fields:
                    self.err(f"{ot[1]} has no field {name}", line)
                idx, t = ci.fields[name]

                def bindf(L):
                    o = fo(L)
                    if o is None:
                        raise PdeError("runtime", f"the {ot[1]} is null", line)
                    return o.f, idx
                return t, bindf
            if isinstance(ot, tuple) and ot[0] == "C" and ot[1] == "PVector" and name in ("x", "y", "z"):
                return "float", ("pvec", fo, name, line)
            self.err(f"cannot assign to {name} of a {tname(ot)}", line)
        self.err("the left of an assignment must be a variable", line)

    def e_assign(self, n):
        _, line, op, target, value = n
        t, bind = self.lvalue(target)
        if isinstance(bind, tuple):          # PVector field
            return self.assign_pvec(op, bind, value, line)
        if op == "=":
            e = self.store_conv(self.value_for(value, t, line), t, line)
            f = e.fn

            def st(L):
                c, k = bind(L)
                v = f(L)
                c[k] = v
                return v
            if target[0] == "name":
                loc = self.find_local(target[2])
                if loc is not None:
                    slot = loc[0]

                    def st_local(L):
                        v = f(L)
                        L[slot] = v
                        return v
                    return E(st_local, t, exact=(t == "float"))
            return E(st, t, exact=(t == "float"))
        # compound: x op= v  ==  x = (T)(x op v), the target evaluated once
        cur = E(lambda L: L[0][0][L[0][1]], t)          # reads the bound (container, key) in L[0]
        rhs = self.expr(value)
        r = self.binop(op[:-1], cur, rhs, line)
        r = self.store_conv(r, t, line)
        rf = r.fn

        def comp(L):
            ck = bind(L)
            keep = L[0]
            L[0] = ck
            try:
                v = rf(L)
            finally:
                L[0] = keep
            ck[0][ck[1]] = v
            return v
        return E(comp, t, exact=(t == "float"))

    def assign_pvec(self, op, bind, value, line):
        _, fo, name, _ = bind
        e = self.store_conv(self.expr(value), "float", line)
        f = e.fn
        if op == "=":
            def st(L):
                o = fo(L)
                if o is None:
                    raise PdeError("runtime", "the PVector is null", line)
                v = f(L)
                setattr(o, name, v) if name in ("x", "y", "z") else None
                return v
            return E(st, "float", exact=True)
        rhs = self.expr(value)
        cur = E(lambda L: getattr(L[0], name), "float", exact=True)
        r = self.store_conv(self.binop(op[:-1], cur, rhs, line), "float", line)
        rf = r.fn

        def comp(L):
            o = fo(L)
            if o is None:
                raise PdeError("runtime", "the PVector is null", line)
            keep = L[0]
            L[0] = o
            try:
                v = rf(L)
            finally:
                L[0] = keep
            setattr(o, name, v)
            return v
        return E(comp, "float", exact=True)

    def _incdec(self, n, prefix):
        _, line, op, target = n
        t, bind = self.lvalue(target)
        if t not in NUMS:
            self.err(f"{op} needs a number", line)
        d = 1 if op == "++" else -1
        if t == "float":
            step = lambda v: f32(v + d)
        elif t == "double":
            step = lambda v: v + d
        elif t == "int":
            step = lambda v: w32(v + d)
        elif t == "long":
            step = lambda v: w64(v + d)
        elif t == "char":
            step = lambda v: (v + d) & 0xFFFF
        elif t == "short":
            step = lambda v: w16(v + d)
        else:
            step = lambda v: w8(v + d)
        if isinstance(bind, tuple):
            _, fo, name, _ = bind

            def pv(L):
                o = fo(L)
                old = getattr(o, name)
                new = step(old)
                setattr(o, name, new)
                return new if prefix else old
            return E(pv, t)
        if target[0] == "name" and self.find_local(target[2]) is not None:
            slot = self.find_local(target[2])[0]
            if t == "int" and prefix:
                def pre_i(L):
                    v = L[slot] + d
                    if v > 2147483647 or v < -2147483648:
                        v = w32(v)
                    L[slot] = v
                    return v
                return E(pre_i, t)
            if t == "int":
                def post_i(L):
                    old = L[slot]
                    v = old + d
                    if v > 2147483647 or v < -2147483648:
                        v = w32(v)
                    L[slot] = v
                    return old
                return E(post_i, t)

        def run(L):
            c, k = bind(L)
            old = c[k]
            new = step(old)
            c[k] = new
            return new if prefix else old
        return E(run, t, exact=(t == "float"))

    def e_pre(self, n):
        return self._incdec(n, True)

    def e_post(self, n):
        return self._incdec(n, False)

    # -------------------------------------------------------------- arrays
    def e_index(self, n):
        _, line, an, ixn = n
        arr = self.expr(an)
        if not (isinstance(arr.t, tuple) and arr.t[0] == "[]"):
            self.err(f"{tname(arr.t)} is not an array", line)
        ix = self.expr(ixn)
        if ix.t not in INTS or ix.t == "long":
            if ix.t in INTS:
                ix = self.conv(ix, "int", line)
            else:
                self.err("an array index must be an int", line)
        fa, fi = arr.fn, ix.fn
        et = arr.t[1]

        def get(L):
            a = fa(L)
            i = fi(L)
            if a is None:
                raise PdeError("runtime", "the array is null", line)
            if i < 0:
                raise PdeError("runtime", f"index {i} is outside the array", line)
            try:
                return a[i]
            except IndexError:
                raise PdeError("runtime", f"index {i} is outside the array (length {len(a)})", line) from None
        return E(get, et, exact=(et == "float"))

    def new_array(self, et, sizes, line):
        """A zeroed array of element type et with the given sizes (outer first)."""
        B = self.B
        tc = _TYPECODE.get(et) if len(sizes) == 1 else None
        n = sizes[0]
        if n < 0:
            raise PdeError("runtime", f"negative array size {n}", line)
        if n > MAX_ARRAY:
            raise PdeError("memory", f"an array of {n} elements is too big", line)
        if len(sizes) == 1:
            if tc is not None:
                B.allocate(n * _ITEMSIZE[tc])
                return array(tc, bytes(n * _ITEMSIZE[tc]))
            B.allocate(n * 8)
            return [default_value(et)] * n
        B.allocate(n * 8)
        inner_t = et
        return [self.new_array(inner_t, sizes[1:], line) for _ in range(n)]

    def e_newarr(self, n):
        _, line, base, dims, extra, init = n
        base = norm(base)
        self.check_type(base, line)
        total = len(dims) + extra
        t = base
        for _ in range(total):
            t = ("[]", t)
        if init is not None:
            return self.array_init(init, t)
        des = []
        for d in dims:
            e = self.expr(d)
            if e.t not in INTS:
                self.err("an array size must be an int", line)
            des.append(e.fn)
        # element type of the innermost allocated level
        et = base
        for _ in range(extra):
            et = ("[]", et)
        mk = self.new_array
        item = _ITEMSIZE.get(_TYPECODE.get(et, ""), 8)
        B = self.B

        def run(L):
            sizes = [f(L) for f in des]
            total = item
            for n in sizes:                    # the whole block, checked before any of it exists
                total *= max(0, n)
            if total > B.alloc_limit - B.alloc:
                raise PdeError("memory", f"an array of {total >> 20} MB is too big", line)
            return mk(et, sizes, line)
        return E(run, t)

    def array_init(self, node, t) -> E:
        _, line, items = node
        et = t[1]
        es = []
        for it in items:
            if it[0] == "arrinit":
                if not (isinstance(et, tuple) and et[0] == "[]"):
                    self.err("too many { } for this array type", line)
                es.append(self.array_init(it, et))
            else:
                es.append(self.store_conv(self.expr(it), et, line))
        fns = [e.fn for e in es]
        tc = _TYPECODE.get(et)
        B = self.B
        if tc is not None:
            size = _ITEMSIZE[tc]

            def run(L):
                B.allocate(len(fns) * size)
                return array(tc, [f(L) for f in fns])
            return E(run, t)

        def run2(L):
            B.allocate(len(fns) * 8)
            return [f(L) for f in fns]
        return E(run2, t)

    # -------------------------------------------------------------- fields and methods
    def e_field(self, n):
        _, line, on, name = n
        obj = self.expr(on)
        ot = obj.t
        fo = obj.fn
        if isinstance(ot, tuple) and ot[0] == "[]" and name == "length":
            def ln(L):
                a = fo(L)
                if a is None:
                    raise PdeError("runtime", "the array is null", line)
                return len(a)
            return E(ln, "int")
        if isinstance(ot, tuple) and ot[0] == "static":
            key = f"{ot[1]}.{name}"
            consts = {"Math.PI": ("double", math.pi), "Math.E": ("double", math.e),
                      "Integer.MAX_VALUE": ("int", 2147483647), "Integer.MIN_VALUE": ("int", -2147483648),
                      "Float.MAX_VALUE": ("float", 3.4028234663852886e38), "Float.MIN_VALUE": ("float", 1.401298464324817e-45),
                      "Long.MAX_VALUE": ("long", 9223372036854775807), "Double.MAX_VALUE": ("double", 1.7976931348623157e308),
                      "Float.POSITIVE_INFINITY": ("float", math.inf), "Float.NEGATIVE_INFINITY": ("float", -math.inf),
                      "PApplet.PI": ("float", f32(math.pi)), "PApplet.TWO_PI": ("float", f32(2 * math.pi))}
            if key in consts:
                t, v = consts[key]
                return const_e(v, t)
            self.err(f"{key} is not available here", line)
        if isinstance(ot, tuple) and ot[0] == "C" and ot[1] in self.classes:
            ci = self.classes[ot[1]]
            if name not in ci.fields:
                self.err(f"{ot[1]} has no field {name}", line)
            idx, t = ci.fields[name]

            def get(L):
                o = fo(L)
                if o is None:
                    raise PdeError("runtime", f"the {ot[1]} is null", line)
                return o.f[idx]
            return E(get, t, exact=(t == "float"))
        if isinstance(ot, tuple) and ot[0] == "C" and ot[1] in ("PImage", "PGraphics") and \
                name in ("width", "height", "pixelWidth", "pixelHeight", "pixels"):
            is_pg = ot[1] == "PGraphics"

            def iget(L):
                o = fo(L)
                if o is None:
                    raise PdeError("runtime", f"the {ot[1]} is null", line)
                if name == "pixels":
                    if is_pg:
                        if o.api.pixels is None:
                            raise PdeError("runtime", "pixels[] is empty until loadPixels()", line)
                        return o.api.pixels
                    return o.pixels
                return o.w if name in ("width", "pixelWidth") else o.h
            return E(iget, ("[]", "int") if name == "pixels" else "int")
        if isinstance(ot, tuple) and ot[0] == "C" and ot[1] == "PVector" and name in ("x", "y", "z"):
            def pget(L):
                o = fo(L)
                if o is None:
                    raise PdeError("runtime", "the PVector is null", line)
                return getattr(o, name)
            return E(pget, "float", exact=True)
        self.err(f"{tname(ot)} has no field {name}", line)

    def e_mcall(self, n):
        from .builtins import method_call
        return method_call(self, n)

    def e_call(self, n):
        _, line, name, argn = n
        args = [self.expr(a) for a in argn]
        # the sketch's own functions (and, inside a class, its methods) come first
        if self.cls is not None and name in self.cls.methods:
            sig = self.pick(self.cls.methods[name], args, line, name)
            if sig is not None:
                return self.call_sig(sig, args, line, this=lambda L: L[1])
        if name in self.funcs:
            sig = self.pick(self.funcs[name], args, line, name)
            if sig is not None:
                return self.call_sig(sig, args, line)
            from .builtins import CALLS
            if name not in CALLS:
                # a sketch function that is called with arguments it cannot take is NOT "not
                # defined": name it as it is written (a piece that wrote float shade(float x,
                # float y) and called shade(t) was dropped with "shade() is not defined").
                self.err(f"{name}() does not take these arguments: {sigs_text(self.funcs[name])}", line)
        self.callback_hint(name, line)
        from .builtins import builtin_call
        return builtin_call(self, name, args, line)

    def pick(self, sigs, args, line, name):
        best, best_score = None, None
        for sig in sigs:
            if len(sig.params) != len(args):
                continue
            score = 0
            ok = True
            for p, a in zip(sig.params, args):
                if p == a.t:
                    continue
                if p in NUMS and a.t in NUMS:
                    score += 1 if _RANK[a.t] <= _RANK[p] else 10
                elif a.t == "null" and is_ref(p):
                    score += 1
                elif p == "String" or (is_ref(p) and is_ref(a.t)):
                    score += 2
                else:
                    ok = False
                    break
            if ok and (best_score is None or score < best_score):
                best, best_score = sig, score
        return best

    def call_sig(self, sig: Sig, args, line, this=None) -> E:
        argfs = [self.store_conv(a, p, line).fn for a, p in zip(args, sig.params)]
        B = self.B
        n = len(argfs)

        def call(L):
            vals = [f(L) for f in argfs]
            F = [None] * sig.nslots
            if this is not None:
                o = this(L)
                if o is None:
                    raise PdeError("runtime", f"calling {sig.name}() on null", line)
                F[1] = o
            F[2:2 + n] = vals
            B.depth += 1
            if B.depth > MAX_DEPTH:
                raise PdeError("depth", f"{sig.name}() calls itself too deeply", line)
            sig.body(F)
            B.depth -= 1
            B.steps += 1
            if B.steps >= B.next:
                B.check()
            return F[0]
        ret = sig.ret
        return E(call, ret if ret != "void" else "void", exact=False)

    def e_new(self, n):
        _, line, t, argn = n
        t = norm(t)
        name = t[1]
        args = [self.expr(a) for a in argn]
        B = self.B
        if name in self.classes:
            ci = self.classes[name]
            sig = self.pick(ci.ctors, args, line, name) if ci.ctors else None
            if ci.ctors and sig is None:
                self.err(f"no constructor of {name} takes these arguments", line)
            if not ci.ctors and args:
                self.err(f"{name} has no constructor with arguments", line)
            argfs = [self.store_conv(a, p, line).fn for a, p in zip(args, sig.params)] if sig else []
            nf = len(ci.fields)
            defaults = [default_value(ft) for _, ft in sorted(ci.fields.values(), key=lambda v: v[0])]

            def new(L):
                B.allocate(64 + 8 * nf)
                vals = [f(L) for f in argfs]
                o = Obj(ci, list(defaults))
                if ci.inits:
                    F0 = [None] * ci.init_slots
                    F0[1] = o
                    for idx, f in ci.inits:
                        o.f[idx] = f(F0)
                if sig is not None:
                    F = [None] * sig.nslots
                    F[1] = o
                    F[2:2 + len(vals)] = vals
                    B.depth += 1
                    if B.depth > MAX_DEPTH:
                        raise PdeError("depth", f"{name}() constructs too deeply", line)
                    sig.body(F)
                    B.depth -= 1
                B.steps += 1
                if B.steps >= B.next:
                    B.check()
                return o
            return E(new, ("C", name, ()))
        from .builtins import new_builtin
        return new_builtin(self, t, args, line)


def compile_program(ast: list, api: Api, budget: Budget, hints: dict | None = None) -> Program:
    return Compiler(api, budget, hints).compile(ast)
