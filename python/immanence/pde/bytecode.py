"""AST -> VM bytecode: the same static typing as compiler.py, emitted as a stack program.

The compiler parses a sketch with parser.py, type-checks it with EXACTLY the rules of compiler.py (Java int/float promotion, the
lenient narrowing, the same overload choice, the same constant folding -- folding goes through
compiler.py itself), and emits bytecode (bcspec.py) the C++ VM (src/) executes. Evaluation order
follows compiler.py's closures, so a sketch draws bit-identically in both (the parity test runs the
C++ VM on the host against the Python interpreter; a device can check golden frame hashes).

A sketch that uses something the VM does not have (PVector, ArrayList, some String methods,
printing an object...) raises Unsupported: it runs on the reference interpreter only.
The format: docs/BYTECODE.md.
"""
from __future__ import annotations

import math
import struct

from . import bcspec as S
from .builtins import CALLS as PY_CALLS
from .compiler import (FLOATS, INTS, NOCONST, NUMS, _RANK, Compiler, E, const_e, is_ref, jstr, norm,
                       sigs_text, tname, w32, w64)
from .errors import PdeError
from .jrandom import f32
from .parser import parse
from .runtime import CONST, Api, Budget

MAX_FUNCS = 4096
MAX_CODE = 65535


class Unsupported(Exception):
    """The sketch is valid but uses something the VM does not run (the reference interpreter can still run it)."""

    def __init__(self, what: str, line: int = 0) -> None:
        super().__init__(f"{what} (line {line})")
        self.what = what
        self.line = line


class Label:
    __slots__ = ("pos",)

    def __init__(self) -> None:
        self.pos = None


class X:
    """A compiled expression: static type, instruction list, constant value (NOCONST if none)."""
    __slots__ = ("t", "code", "const")

    def __init__(self, t, code, const=NOCONST) -> None:
        self.t, self.code, self.const = t, code, const


class Func:
    __slots__ = ("name", "params", "ret", "node", "cls", "index", "nslots", "code", "has_this", "kind")

    def __init__(self, name, params, ret, node, cls=None, kind="func") -> None:
        self.name, self.params, self.ret, self.node, self.cls, self.kind = name, params, ret, node, cls, kind
        self.index = -1
        self.nslots = 2 + len(params)
        self.code: list = []
        self.has_this = cls is not None


class ClassB:
    def __init__(self, name) -> None:
        self.name = name
        self.fields: dict[str, tuple[int, object]] = {}
        self.inits: list = []                  # field nodes with initialisers
        self.methods: dict[str, list[Func]] = {}
        self.ctors: list[Func] = []
        self.index = -1
        self.news: dict = {}                    # ctor (or None) -> synthetic constructor function


def kind_of(t) -> int:
    if t in S.KIND and t != "ref":
        return S.KIND[t]
    return S.KIND["ref"]


def _prim_slot(t) -> str:
    """Which slot flavour a value of type t occupies: I (int-ish and boolean), L, F, D or R."""
    if t in ("int", "short", "byte", "char", "boolean"):
        return "I"
    if t == "long":
        return "L"
    if t == "float":
        return "F"
    if t == "double":
        return "D"
    return "R"


class BCompiler:
    def __init__(self, hints: dict | None = None) -> None:
        self.budget = Budget(4_000_000, 2.0, 32 << 20)
        self.api = Api(self.budget)
        # names the art library asks the SKETCH to write (pde.callback_hints), for the same
        # message the interpreter gives
        self.hints: dict[str, str] = hints or {}
        self.py = Compiler(self.api, self.budget, self.hints)   # for constant folding: compiler.py itself
        self.funcs: dict[str, list[Func]] = {}
        self.classes: dict[str, ClassB] = {}
        self._class_nodes: dict[str, list] = {}
        self.gtypes: dict[str, tuple[int, object]] = {}
        self.all_funcs: list[Func] = []
        self.scopes: list[dict] = []
        self.nslots = 2
        self.maxslots = 2
        self.cls: ClassB | None = None
        self.ret_t = "void"
        self.loops: list = []          # (break label, continue label or None)
        self.fn_end: Label | None = None
        self.in_global_init = False
        self.longs: dict = {}
        self.doubles: dict = {}
        self.strings: dict = {}
        self.uses_clock = False
        self.p3d = False                # size(..., P3D) somewhere in the sketch (text(s, x, y, z) then evaluates z)
        self.entry = {"globals": -1, "static": -1, "setup": -1, "draw": -1}

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def err(msg: str, line: int, kind: str = "compile"):
        raise PdeError(kind, msg, line)

    @staticmethod
    def unsup(what: str, line: int):
        raise Unsupported(what, line)

    def callback_hint(self, name: str, line: int) -> None:
        """As the interpreter's: `name` is one the art library asks the sketch to write."""
        msg = self.hints.get(name)
        if msg:
            self.err(msg, line)

    def add_func(self, f: Func) -> Func:
        f.index = len(self.all_funcs)
        if f.index >= MAX_FUNCS:
            self.unsup("too many functions", 0)
        self.all_funcs.append(f)
        return f

    def pool(self, table: dict, key, value) -> int:
        i = table.get(key)
        if i is None:
            i = table[key] = (len(table), value)
        else:
            i = i
        if len(table) > 65535:
            self.unsup("too many constants", 0)
        return table[key][0]

    def push_const(self, v, t) -> list:
        """Instructions pushing constant v of type t."""
        if t in ("int", "short", "byte", "char"):
            return [("ICONST", int(v))]
        if t == "boolean":
            return [("ICONST", 1 if v else 0)]
        if t == "long":
            return [("LCONST", self.pool(self.longs, int(v), int(v)))]
        if t == "float":
            return [("FCONST", float(v))]
        if t == "double":
            v = float(v)
            return [("DCONST", self.pool(self.doubles, struct.pack("<d", v), v))]
        if t == "String":
            if v is None:
                return [("NULL",)]
            self.check_str(v, 0)
            return [("SCONST", self.pool(self.strings, v, v))]
        if t == "null" or v is None:
            return [("NULL",)]
        self.unsup(f"a constant of type {tname(t)}", 0)

    def check_str(self, v: str, line: int) -> None:
        if any(ord(ch) > 0xFFFF for ch in v):
            self.unsup("a character outside the basic plane", line)

    def cx(self, v, t) -> X:
        e = const_e(v, t)
        return X(t, self.push_const(e.const, t), e.const)

    def pe(self, x: X) -> E:
        """A compiler.py expression standing for x (constants only matter: used for folding)."""
        return E(lambda L, v=x.const: v, x.t, x.const, True)

    # ------------------------------------------------------------------ program
    def compile(self, items: list) -> None:
        globals_: list = []
        statics: list = []
        for it in items:
            if it[0] == "class":
                if it[2] in self.classes:
                    self.err(f"class {it[2]} is declared twice", it[1])
                self.classes[it[2]] = ClassB(it[2])
                self._class_nodes[it[2]] = it[3]
        for i, ci in enumerate(self.classes.values()):
            ci.index = i
        for it in items:
            k = it[0]
            if k == "class":
                self.declare_class(it)
            elif k == "func":
                _, line, ret, name, params, body = it
                f = Func(name, [self.check_type(norm(p[0]), line) for p in params], self.check_type(norm(ret), line), it)
                self.funcs.setdefault(name, []).append(self.add_func(f))
            elif k == "global":
                globals_.append(it)
                for t, name, init in it[2][2]:
                    t = self.check_type(norm(t), it[1])
                    if name in self.gtypes:
                        self.err(f"{name} is declared twice", it[1])
                    self.gtypes[name] = (len(self.gtypes), t)
            elif k == "stmt":
                statics.append(it[2])
        # classes first: a global initialiser may call a constructor (`Cloud c = new Cloud("fern");`),
        # whose synthetic "new" function compile_class makes
        for ci in self.classes.values():
            self.compile_class(ci)
        # global initialisers, in order
        gf = self.add_func(Func("<globals>", [], "void", None, kind="globals"))
        self.in_global_init = True
        self.begin_function()
        self.fn_end = Label()
        code: list = []
        for it in globals_:
            for t, name, init in it[2][2]:
                if init is None:
                    continue
                slot, gt = self.gtypes[name]
                x = self.store_conv(self.value_for(init, gt, it[1]), gt, it[1])
                code += x.code + [("GSTORE", slot)]
        code += [("L", self.fn_end), ("RET",)]
        gf.code, gf.nslots = code, self.maxslots
        self.in_global_init = False
        self.entry["globals"] = gf.index
        for sigs in list(self.funcs.values()):
            for f in sigs:
                self.compile_func(f)
        for name in ("setup", "draw"):
            for f in self.funcs.get(name, []):
                if not f.params:
                    self.entry[name] = f.index
        if statics:
            sf = self.add_func(Func("<static>", [], "void", None, kind="static"))
            self.begin_function()
            self.ret_t = "void"
            self.fn_end = Label()
            body = self.block_of(statics, 0)
            sf.code = body + [("L", self.fn_end), ("RET",)]
            sf.nslots = self.maxslots
            self.entry["static"] = sf.index

    def check_type(self, t, line):
        if isinstance(t, tuple):
            if t[0] == "[]":
                self.check_type(t[1], line)
            elif t[1] not in self.classes and t[1] not in ("PVector", "ArrayList", "PFont", "Object", "List",
                                                            "PImage", "PGraphics", "IntList", "FloatList",
                                                            "StringList", "HashMap", "Map"):
                self.err(f"the type {t[1]} is not available here", line)
            elif t[1] in ("PVector", "ArrayList", "List", "Object", "PImage", "PGraphics", "IntList", "FloatList",
                          "StringList", "HashMap", "Map"):
                self.unsup(f"{t[1]} on the VM", line)
        return t

    # ------------------------------------------------------------------ scopes (as compiler.py)
    def begin_function(self, params=()):
        self.scopes = [{}]
        self.nslots = 2
        self.maxslots = 2
        self.loops = []
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

    def temp(self) -> int:
        return self.declare_local(f"<t{self.nslots}>", "int", 0)

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
                f = Func(mname, [self.check_type(norm(p[0]), mline) for p in params], self.check_type(norm(ret), mline), m, ci)
                ci.methods.setdefault(mname, []).append(self.add_func(f))
            elif k == "ctor":
                _, cline, _, params, body = m
                f = Func(name, [self.check_type(norm(p[0]), cline) for p in params], "void",
                         ("func", cline, "void", name, params, body), ci)
                ci.ctors.append(self.add_func(f))
            elif k == "class":
                self.err("a class inside a class is not available here", m[1])

    def compile_class(self, ci: ClassB):
        self.cls = ci
        self.begin_function()
        inits = []
        for it in [m for m in self._class_nodes.get(ci.name, []) if m[0] == "global"]:
            for t, fname, init in it[2][2]:
                if init is None:
                    continue
                idx, ft = ci.fields[fname]
                x = self.store_conv(self.value_for(init, ft, it[1]), ft, it[1])
                inits.append((idx, x))
        init_slots = self.maxslots
        # one synthetic "new" function per constructor: NEWOBJ, field initialisers, the constructor
        for ctor in (ci.ctors or [None]):
            params = ctor.params if ctor is not None else []
            nf = self.add_func(Func(f"<new {ci.name}>", params, ("C", ci.name, ()), None, kind="new"))
            n = len(params)
            obj = 2 + n                    # the new object's slot, after the arguments
            code = [("NEWOBJ", ci.index), ("STORE", obj)]
            for idx, x in inits:
                code += [("LOAD", obj)] + self.relocate(x.code, obj + 1) + [("PUTF", idx)]
            if ctor is not None:
                code += [("LOAD", 2 + i) for i in range(n)] + [("LOAD", obj), ("CALLM", ctor)]
            code += [("LOAD", obj), ("RETV",)]
            nf.code = code
            nf.nslots = obj + 1 + max(0, init_slots - 2)
            ci.news[id(ctor)] = nf
        for sigs in ci.methods.values():
            for f in sigs:
                self.compile_func(f)
        for f in ci.ctors:
            self.compile_func(f)
        self.cls = None

    def relocate(self, code: list, base: int) -> list:
        """Field initialiser code compiled with `this` in slot 1 and locals from 2: inside the
        synthetic constructor `this` is the object slot and the locals move past it."""
        out = []
        for ins in code:
            op = ins[0]
            if op in ("LOAD", "STORE", "IINC"):
                s = ins[1]
                if s == 1:
                    s = base - 1
                elif s >= 2:
                    s = base + s - 2
                out.append((op, s) + tuple(ins[2:]))
            else:
                out.append(ins)
        return out

    def compile_func(self, f: Func):
        _, line, ret, name, params, body = f.node
        self.cls = f.cls
        self.ret_t = f.ret
        self.begin_function([(t, p[1]) for t, p in zip(f.params, params)])
        self.fn_end = Label()
        code = self.stmt(body)
        tail = [("L", self.fn_end)]
        if f.ret == "void":
            tail.append(("RET",))
        else:
            # falling off the end: Python's frame returns None (null / an unusable number)
            tail += self.default_code(f.ret) + [("RETV",)]
        f.code = code + tail
        f.nslots = self.maxslots
        self.cls = None

    def default_code(self, t) -> list:
        s = _prim_slot(t)
        return {"I": [("ICONST", 0)], "L": self.push_const(0, "long"), "F": [("FCONST", 0.0)],
                "D": self.push_const(0.0, "double"), "R": [("NULL",)]}[s]

    # ------------------------------------------------------------------ statements
    def block_of(self, stmts, line) -> list:
        self.push_scope()
        code: list = []
        for s in stmts:
            code += self.stmt(s)
        self.pop_scope()
        return code

    def stmt(self, s) -> list:
        m = getattr(self, "s_" + s[0], None)
        if m is None:
            self.err(f"{s[0]} is not available here", s[1])
        return m(s)

    def s_block(self, s):
        return self.block_of(s[2], s[1])

    def s_empty(self, s):
        return []

    def s_expr(self, s):
        x = self.expr(s[2])
        code = list(x.code)
        if x.t == "void":
            return code
        # an assignment's value is not needed as a statement: drop the DUP instead of popping
        if len(code) >= 2 and code[-2][0] == "DUP" and code[-1][0] in ("STORE", "GSTORE"):
            return code[:-2] + [code[-1]]
        if len(code) >= 2 and code[-2][0] == "DUP_X1" and code[-1][0] == "PUTF":
            return code[:-2] + [code[-1]]
        if len(code) >= 2 and code[-2][0] == "DUP_X2" and code[-1][0] == "ASTORE":
            return code[:-2] + [code[-1]]
        return code + [("POP",)]

    def s_vardecl(self, s):
        code: list = []
        for t, name, init in s[2]:
            t = self.check_type(norm(t), s[1])
            x = self.store_conv(self.value_for(init, t, s[1]), t, s[1]) if init is not None else None
            slot = self.declare_local(name, t, s[1])
            code += (x.code if x is not None else self.default_code(t)) + [("STORE", slot)]
        return code

    def s_if(self, s):
        c = self.cond(s[2])
        then = self.stmt(s[3])
        els, end = Label(), Label()
        if s[4] is None:
            return c + [("JZ", end)] + then + [("L", end)]
        other = self.stmt(s[4])
        return c + [("JZ", els)] + then + [("JMP", end), ("L", els)] + other + [("L", end)]

    def _loop(self, body_node, brk, cnt):
        self.loops.append((brk, cnt))
        code = self.stmt(body_node)
        self.loops.pop()
        return code

    def s_while(self, s):
        top, brk, cnt = Label(), Label(), Label()
        c = self.cond(s[2])
        body = self._loop(s[3], brk, cnt)
        return [("L", top)] + c + [("JZ", brk)] + body + [("L", cnt), ("LOOP", top), ("L", brk)]

    def s_do(self, s):
        top, brk, cnt = Label(), Label(), Label()
        body = self._loop(s[2], brk, cnt)
        c = self.cond(s[3])
        return [("L", top)] + body + [("L", cnt)] + c + [("JZ", brk), ("LOOP", top), ("L", brk)]

    def s_for(self, s):
        _, line, init, cond, update, body = s
        self.push_scope()
        code: list = []
        for i in init:
            code += self.stmt(i)
        c = self.cond(cond) if cond is not None else None
        ups: list = []
        for u in update:
            ups += self.s_expr(("expr", line, u))
        top, brk, cnt = Label(), Label(), Label()
        b = self._loop(body, brk, cnt)
        self.pop_scope()
        code += [("L", top)]
        if c is not None:
            code += c + [("JZ", brk)]
        return code + b + [("L", cnt)] + ups + [("LOOP", top), ("L", brk)]

    def s_foreach(self, s):
        _, line, t, name, it, body = s
        src = self.expr(it)
        t = self.check_type(norm(t), line)
        st = src.t
        if isinstance(st, tuple) and st[0] == "[]":
            et = st[1]
        elif isinstance(st, tuple) and st[0] == "C" and st[1] in ("ArrayList", "List"):
            self.unsup("ArrayList on the VM", line)
        else:
            self.err(f"cannot loop over a {tname(st)}", line)
        conv = self.store_conv(X(et, []), t, line).code
        self.push_scope()
        arr, idx = self.temp(), self.temp()
        slot = self.declare_local(name, t, line)
        top, brk, cnt = Label(), Label(), Label()
        b = self._loop(body, brk, cnt)
        self.pop_scope()
        return (src.code + [("NULLCHK",), ("STORE", arr), ("ICONST", 0), ("STORE", idx), ("L", top),
                            ("LOAD", idx), ("LOAD", arr), ("ALEN",), ("ICMP", S.CMP["<"]), ("JZ", brk),
                            ("LOAD", arr), ("LOAD", idx), ("ALOAD", kind_of(et))] + conv +
                [("STORE", slot)] + b + [("L", cnt), ("IINC", idx, 1), ("LOOP", top), ("L", brk)])

    def s_switch(self, s):
        _, line, e, cases = s
        se = self.expr(e)
        if se.t not in INTS and se.t != "String":
            self.err("switch needs an int, a char or a String", line)
        table: dict = {}
        default = None
        self.push_scope()
        tmp = self.temp()
        brk = Label()
        # `continue` inside a switch goes to the enclosing loop (Python: CNT propagates)
        inner_cnt = next((c for _b, c in reversed(self.loops) if c is not None), None)
        self.loops.append((brk, inner_cnt))
        labels: list = []
        bodies: list = []
        for labs, body in cases:
            lab = Label()
            idx = len(labels)
            labels.append(lab)
            if labs is None:
                default = idx
            else:
                for lb in labs:
                    le = self.expr(lb)
                    if le.const is NOCONST:
                        self.err("a case label must be a constant", line)
                    table[le.const] = idx
            code: list = []
            for b in body:
                code += self.stmt(b)
            bodies.append(code)
        self.loops.pop()
        self.pop_scope()
        out = se.code + [("STORE", tmp)]
        for k, idx in table.items():
            if se.t == "String":
                if not isinstance(k, str):
                    continue
                out += [("LOAD", tmp)] + self.push_const(k, "String") + [("SCMP", S.CMP["=="]), ("JNZ", labels[idx])]
            else:
                if isinstance(k, str) or isinstance(k, float):
                    if isinstance(k, str):
                        continue
                    self.unsup("a non-integer case label", line)
                if se.t == "long":
                    out += [("LOAD", tmp)] + self.push_const(int(k), "long") + [("LCMP", S.CMP["=="]), ("JNZ", labels[idx])]
                else:
                    if not -2**31 <= int(k) < 2**31:
                        continue
                    out += [("LOAD", tmp), ("ICONST", int(k)), ("ICMP", S.CMP["=="]), ("JNZ", labels[idx])]
        out += [("JMP", labels[default] if default is not None else brk)]
        for lab, code in zip(labels, bodies):
            out += [("L", lab)] + code
        return out + [("L", brk)]

    def s_break(self, s):
        if s[2] is not None:
            self.err("labelled break is not available here", s[1])
        if not self.loops:
            self.err("break outside a loop or switch", s[1])
        return [("JMP", self.loops[-1][0])]

    def s_continue(self, s):
        if s[2] is not None:
            self.err("labelled continue is not available here", s[1])
        if not self.loops:
            self.err("continue outside a loop", s[1])
        cnt = self.loops[-1][1]
        if cnt is None:          # a switch outside any loop: Python's CNT ends the function
            return [("JMP", self.fn_end)]
        return [("JMP", cnt)]

    def s_labeled(self, s):
        return self.stmt(s[3])

    def s_return(self, s):
        if s[2] is None:
            if self.ret_t != "void":
                return self.default_code(self.ret_t) + [("RETV",)]
            return [("RET",)]
        if self.ret_t == "void":
            self.err("a void function cannot return a value", s[1])
        x = self.store_conv(self.value_for(s[2], self.ret_t, s[1]), self.ret_t, s[1])
        return x.code + [("RETV",)]

    # ------------------------------------------------------------------ conversions (as compiler.py)
    def value_for(self, node, t, line) -> X:
        if node[0] == "arrinit":
            if not (isinstance(t, tuple) and t[0] == "[]"):
                self.err("{...} needs an array type", line)
            return self.array_init(node, t)
        return self.expr(node)

    def conv(self, x: X, to, line, explicit=False) -> X:
        src = x.t
        if src == to:
            return x
        if x.const is not NOCONST and src in NUMS + ("boolean",) and to in NUMS:
            e = self.py.conv(self.pe(x), to, line, explicit)
            return self.cx(e.const, to)
        c = list(x.code)
        if to in NUMS and src in NUMS:
            ops = _num_conv(src, to)
            return X(to, c + [(o,) for o in ops])
        if to == "String":
            if src == "null":
                return X(to, c)
            return X(to, c + self.tostr(src, line))
        if src == "null" and is_ref(to):
            return X(to, c)
        if is_ref(to) and is_ref(src):
            # type errors exactly as compiler.py (it raises for the same pairs)
            self.py.conv(E(lambda L: None, src), to, line, explicit)
            return X(to, c)
        if isinstance(src, tuple) and src[0] == "C" and src[1] == "Object" and to in NUMS + ("boolean",):
            self.unsup("Object on the VM", line)
        self.err(f"cannot convert {tname(src)} to {tname(to)}", line)

    def tostr(self, t, line) -> list:
        if t in ("int", "short", "byte"):
            return [("TOSTR", S.TS["int"])]
        if t in ("long", "char", "boolean", "float", "double", "String"):
            return [("TOSTR", S.TS[t])]
        if t == "null":
            return [("POP",)] + self.push_const("null", "String")
        self.unsup(f"turning a {tname(t)} into text", line)

    def store_conv(self, x: X, t, line) -> X:
        if x.t != t and x.t in NUMS and t in NUMS:
            return self.conv(x, t, line, explicit=True)
        if x.t != t:
            return self.conv(x, t, line)
        return x

    def cond(self, node) -> list:
        x = self.expr(node)
        if x.t != "boolean":
            self.err(f"a condition must be true/false, not {tname(x.t)}", node[1])
        return x.code

    # ------------------------------------------------------------------ expressions
    def expr(self, n) -> X:
        m = getattr(self, "e_" + n[0], None)
        if m is None:
            self.err(f"{n[0]} is not available here", n[1])
        return m(n)

    def e_lit(self, n):
        _, line, t, v = n
        if t == "String":
            if len(v) > 65536:
                self.err("the string is too long", line)
            self.check_str(v, line)
        return self.cx(v, t)

    def e_this(self, n):
        if self.cls is None:
            self.err("this is only available inside a class", n[1])
        return X(("C", self.cls.name, ()), [("LOAD", 1)])

    def e_name(self, n):
        _, line, name = n
        loc = self.find_local(name)
        if loc is not None:
            slot, t = loc
            return X(t, [("LOAD", slot)])
        if self.cls is not None and name in self.cls.fields:
            idx, t = self.cls.fields[name]
            return X(t, [("LOAD", 1), ("GETF", idx)])
        g = self.gtypes.get(name)
        if g is not None:
            slot, t = g
            return X(t, [("GLOAD", slot)])
        return self.builtin_var(name, line)

    def builtin_var(self, name, line) -> X:
        if name in CONST:
            t, v = CONST[name]
            return self.cx(v, t)
        if name in ("width", "height"):
            if self.in_global_init:
                return self.cx(100, "int")
            return self.cx(240 if name == "width" else 128, "int")
        if name == "frameCount":
            return X("int", [("FRAMECOUNT",)])
        if name == "frameRate":
            return self.cx(60.0, "float")
        if name in ("mouseX", "mouseY", "pmouseX", "pmouseY", "keyCode"):
            return self.cx(0, "int")
        if name in ("mousePressed", "keyPressed", "focused"):
            return self.cx(False, "boolean")
        if name == "key":
            return self.cx(0, "char")
        if name in ("displayWidth", "displayHeight"):
            return self.cx(240 if name == "displayWidth" else 128, "int")
        if name == "pixels":
            return X(("[]", "int"), [("PIXELS",)])
        if name in ("Math", "PVector", "String", "Integer", "Float", "Double", "Long", "Character", "Boolean",
                    "PApplet", "System"):
            return X(("static", name), [])
        self.callback_hint(name, line)
        self.err(f"{name} is not defined", line)

    # -------------------------------------------------------------- operators
    def e_un(self, n):
        _, line, op, a = n
        x = self.expr(a)
        if op == "!":
            if x.t != "boolean":
                self.err("! needs true/false", line)
            if x.const is not NOCONST:
                return self.cx(not x.const, "boolean")
            return X("boolean", x.code + [("BNOT",)])
        if x.t not in NUMS:
            self.err(f"{op} needs a number", line)
        t = x.t if _RANK[x.t] >= 3 else "int"
        if op == "+":
            return self.conv(x, t, line) if t != x.t else x
        if op == "~":
            if t not in ("int", "long"):
                self.err("~ needs an integer", line)
            x = self.conv(x, t, line) if t != x.t else x
            return X(t, x.code + [("INOT",) if t == "int" else ("LNOT",)])
        x = self.conv(x, t, line) if t != x.t else x
        if x.const is not NOCONST:
            v = -x.const
            return self.cx(w32(v) if t == "int" else w64(v) if t == "long" else v, t)
        return X(t, x.code + [({"int": "INEG", "long": "LNEG", "float": "FNEG", "double": "DNEG"}[t],)])

    def promote(self, a: X, b: X, line, op):
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
            end = Label()
            if op == "&&":
                return X("boolean", a.code + [("DUP",), ("JZ", end), ("POP",)] + b.code + [("L", end)])
            return X("boolean", a.code + [("DUP",), ("JNZ", end), ("POP",)] + b.code + [("L", end)])
        a, b = self.expr(an), self.expr(bn)
        return self.binop(op, a, b, line)

    def fold(self, op, a: X, b: X, line, t) -> X | None:
        """Both constant: the value compiler.py folds to (None when it does not fold)."""
        if a.const is NOCONST or b.const is NOCONST:
            return None
        e = self.py.binop(op, self.pe(a), self.pe(b), line)
        if e.const is NOCONST:
            return None
        return self.cx(e.const, e.t)

    def binop(self, op, a: X, b: X, line) -> X:
        if op == "+" and ("String" in (a.t, b.t)):
            return self.concat(a, b, line)
        if op in ("==", "!="):
            return self.equality(op, a, b, line)
        if op in ("&", "|", "^") and a.t == "boolean" and b.t == "boolean":
            return X("boolean", a.code + b.code + [({"&": "IAND", "|": "IOR", "^": "IXOR"}[op],)])
        if op in ("<<", ">>", ">>>"):
            if a.t not in INTS or b.t not in INTS:
                self.err(f"{op} needs integers", line)
            t = "long" if a.t == "long" else "int"
            a = self.conv(a, t, line) if a.t != t else a
            f = self.fold(op, a, b, line, t)
            if f is not None:
                return f
            bc = b.code + ([("L2I",)] if b.t == "long" else [])
            name = {"<<": "SHL", ">>": "SHR", ">>>": "USHR"}[op]
            return X(t, a.code + bc + [(("I" if t == "int" else "L") + name,)])
        a, b, t = self.promote(a, b, line, op)
        f = self.fold(op, a, b, line, t)
        if f is not None:
            return f
        p = {"int": "I", "long": "L", "float": "F", "double": "D"}[t]
        if op in ("<", ">", "<=", ">="):
            return X("boolean", a.code + b.code + [(p + "CMP", S.CMP[op])])
        if op in ("&", "|", "^"):
            if t not in ("int", "long"):
                self.err(f"{op} needs integers", line)
            return X(t, a.code + b.code + [(p + {"&": "AND", "|": "OR", "^": "XOR"}[op],)])
        name = {"+": "ADD", "-": "SUB", "*": "MUL", "/": "DIV", "%": "REM"}[op]
        return X(t, a.code + b.code + [(p + name,)])

    def concat(self, a: X, b: X, line) -> X:
        if a.const is not NOCONST and b.const is not NOCONST:
            e = self.py.concat(self.pe(a), self.pe(b), line)
            return self.cx(e.const, "String")
        ca = a.code + self.tostr(a.t, line)
        cb = b.code + self.tostr(b.t, line)
        return X("String", ca + cb + [("SCAT",)])

    def equality(self, op, a: X, b: X, line) -> X:
        if a.t in NUMS and b.t in NUMS:
            a, b, t = self.promote(a, b, line, op)
            f = self.fold(op, a, b, line, t)
            if f is not None:
                return f
            p = {"int": "I", "long": "L", "float": "F", "double": "D"}[t]
            return X("boolean", a.code + b.code + [(p + "CMP", S.CMP[op])])
        if (a.t == "boolean") != (b.t == "boolean"):
            self.err(f"cannot compare {tname(a.t)} with {tname(b.t)}", line)
        f = self.fold(op, a, b, line, "boolean")
        if f is not None:
            return f
        if a.t == "boolean":
            return X("boolean", a.code + b.code + [("ICMP", S.CMP[op])])
        if a.t == "String" and b.t == "String":
            return X("boolean", a.code + b.code + [("SCMP", S.CMP[op])])
        return X("boolean", a.code + b.code + [("ACMP", S.CMP[op])])

    def e_cond(self, n):
        _, line, c, an, bn = n
        cc = self.cond(c)
        a, b = self.expr(an), self.expr(bn)
        if a.t in NUMS and b.t in NUMS and a.t != b.t:
            a, b, _ = self.promote(a, b, line, "?:")
        elif a.t != b.t:
            if a.t == "null":
                a = X(b.t, a.code)
            elif b.t == "null":
                b = X(a.t, b.code)
            elif a.t == "String" or b.t == "String":
                a, b = self.conv(a, "String", line), self.conv(b, "String", line)
        if _prim_slot(a.t) != _prim_slot(b.t):
            self.unsup("a ?: whose two sides differ in type", line)
        els, end = Label(), Label()
        return X(a.t, cc + [("JZ", els)] + a.code + [("JMP", end), ("L", els)] + b.code + [("L", end)])

    def e_cast(self, n):
        _, line, t, a = n
        t = norm(t)
        x = self.expr(a)
        if t == x.t:
            return x
        if t in NUMS and x.t in NUMS:
            return self.conv(x, t, line, explicit=True)
        if t == "String" and x.t != "String":
            if x.t == "null" or (isinstance(x.t, tuple) and x.t[1] == "Object"):
                return X("String", x.code)
            self.err(f"cannot cast {tname(x.t)} to String", line)
        return self.conv(x, t, line, explicit=True)

    # -------------------------------------------------------------- assignment
    def lvalue(self, n):
        """(type, kind, prefix code, arg): kind local/global/field/index."""
        k = n[0]
        line = n[1]
        if k == "name":
            name = n[2]
            loc = self.find_local(name)
            if loc is not None:
                return loc[1], "local", [], loc[0]
            if self.cls is not None and name in self.cls.fields:
                idx, t = self.cls.fields[name]
                return t, "field", [("LOAD", 1)], idx
            g = self.gtypes.get(name)
            if g is not None:
                return g[1], "global", [], g[0]
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
            return arr.t[1], "index", arr.code + ix.code, kind_of(arr.t[1])
        if k == "field":
            obj = self.expr(n[2])
            name = n[3]
            ot = obj.t
            if isinstance(ot, tuple) and ot[0] == "C" and ot[1] in self.classes:
                ci = self.classes[ot[1]]
                if name not in ci.fields:
                    self.err(f"{ot[1]} has no field {name}", line)
                idx, t = ci.fields[name]
                return t, "field", obj.code + [("NULLCHK",)], idx
            if isinstance(ot, tuple) and ot[0] == "C" and ot[1] == "PVector":
                self.unsup("PVector on the VM", line)
            self.err(f"cannot assign to {name} of a {tname(ot)}", line)
        self.err("the left of an assignment must be a variable", line)

    @staticmethod
    def _rw(kind, arg):
        """(read-after-prefix code, dup-value-for-result op, store code) for an lvalue kind."""
        if kind == "local":
            return [("LOAD", arg)], "DUP", [("STORE", arg)]
        if kind == "global":
            return [("GLOAD", arg)], "DUP", [("GSTORE", arg)]
        if kind == "field":
            return [("DUP",), ("GETF", arg)], "DUP_X1", [("PUTF", arg)]
        return [("DUP2",), ("ALOAD", arg)], "DUP_X2", [("ASTORE", arg)]

    def e_assign(self, n):
        _, line, op, target, value = n
        t, kind, pre, arg = self.lvalue(target)
        read, dup, store = self._rw(kind, arg)
        if op == "=":
            x = self.store_conv(self.value_for(value, t, line), t, line)
            return X(t, pre + x.code + [(dup,)] + store)
        cur = X(t, read)
        rhs = self.expr(value)
        r = self.store_conv(self.binop(op[:-1], cur, rhs, line), t, line)
        # binop may have folded nothing here (cur is not constant); its code starts with `read`
        return X(t, pre + r.code + [(dup,)] + store)

    def _incdec(self, n, prefix):
        _, line, op, target = n
        t, kind, pre, arg = self.lvalue(target)
        if t not in NUMS:
            self.err(f"{op} needs a number", line)
        d = 1 if op == "++" else -1
        if kind == "local" and t == "int":
            if prefix:
                return X(t, [("IINC", arg, d), ("LOAD", arg)])
            return X(t, [("LOAD", arg), ("IINC", arg, d)])
        read, dup, store = self._rw(kind, arg)
        if t == "float":
            step = [("FCONST", float(d)), ("FADD",)]
        elif t == "double":
            step = self.push_const(float(d), "double") + [("DADD",)]
        elif t == "long":
            step = self.push_const(d, "long") + [("LADD",)]
        else:
            step = [("ICONST", d), ("IADD",)] + {"int": [], "char": [("I2C",)], "short": [("I2S",)],
                                                   "byte": [("I2B",)]}[t]
        if prefix:
            return X(t, pre + read + step + [(dup,)] + store)
        return X(t, pre + read + [(dup,)] + step + store)

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
        et = arr.t[1]
        return X(et, arr.code + ix.code + [("ALOAD", kind_of(et))])

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
        code: list = []
        for d in dims:
            x = self.expr(d)
            if x.t not in INTS:
                self.err("an array size must be an int", line)
            if x.t == "long":
                self.unsup("a long array size", line)
            code += x.code
        et = base
        for _ in range(extra):
            et = ("[]", et)
        if len(dims) == 1:
            return X(t, code + [("NEWARR", kind_of(et))])
        if len(dims) > 8:
            self.unsup("an array of more than 8 dimensions", line)
        return X(t, code + [("NEWMULTI", kind_of(et), len(dims))])

    def array_init(self, node, t) -> X:
        _, line, items = node
        et = t[1]
        k = kind_of(et)
        code = [("ICONST", len(items)), ("NEWARR", k)]
        for i, it in enumerate(items):
            if it[0] == "arrinit":
                if not (isinstance(et, tuple) and et[0] == "[]"):
                    self.err("too many { } for this array type", line)
                x = self.array_init(it, et)
            else:
                x = self.store_conv(self.expr(it), et, line)
            code += [("DUP",), ("ICONST", i)] + x.code + [("ASTORE", k)]
        return X(t, code)

    # -------------------------------------------------------------- fields and methods
    def e_field(self, n):
        _, line, on, name = n
        obj = self.expr(on)
        ot = obj.t
        if isinstance(ot, tuple) and ot[0] == "[]" and name == "length":
            return X("int", obj.code + [("ALEN",)])
        if isinstance(ot, tuple) and ot[0] == "static":
            e = self.py.e_field(("field", line, ("name", line, ot[1]), name))
            return self.cx(e.const, e.t)
        if isinstance(ot, tuple) and ot[0] == "C" and ot[1] in self.classes:
            ci = self.classes[ot[1]]
            if name not in ci.fields:
                self.err(f"{ot[1]} has no field {name}", line)
            idx, t = ci.fields[name]
            return X(t, obj.code + [("NULLCHK",), ("GETF", idx)])
        if isinstance(ot, tuple) and ot[0] == "C" and ot[1] == "PVector":
            self.unsup("PVector on the VM", line)
        self.err(f"{tname(ot)} has no field {name}", line)

    def e_call(self, n):
        _, line, name, argn = n
        args = [self.expr(a) for a in argn]
        if self.cls is not None and name in self.cls.methods:
            f = Compiler.pick(self, self.cls.methods[name], args, line, name)
            if f is not None:
                return self.call(f, args, line, this=[("LOAD", 1)])
        if name in self.funcs:
            f = Compiler.pick(self, self.funcs[name], args, line, name)
            if f is not None:
                return self.call(f, args, line)
            from .bcbuiltins import CALLS
            if name not in CALLS:
                self.err(f"{name}() does not take these arguments: {sigs_text(self.funcs[name])}", line)
        self.callback_hint(name, line)
        from .bcbuiltins import builtin_call
        return builtin_call(self, name, args, line)

    def call(self, f: Func, args, line, this=None) -> X:
        code: list = []
        for a, p in zip(args, f.params):
            code += self.store_conv(a, p, line).code
        if this is not None:
            code += this + [("CALLM", f)]
        else:
            code += [("CALL", f)]
        return X(f.ret if f.ret != "void" else "void", code)

    def e_new(self, n):
        _, line, t, argn = n
        t = norm(t)
        name = t[1]
        args = [self.expr(a) for a in argn]
        if name in self.classes:
            ci = self.classes[name]
            ctor = Compiler.pick(self, ci.ctors, args, line, name) if ci.ctors else None
            if ci.ctors and ctor is None:
                self.err(f"no constructor of {name} takes these arguments", line)
            if not ci.ctors and args:
                self.err(f"{name} has no constructor with arguments", line)
            code: list = []
            if ctor is not None:
                for a, p in zip(args, ctor.params):
                    code += self.store_conv(a, p, line).code
            nf = ci.news[id(ctor)]
            return X(("C", name, ()), code + [("CALL", nf)])
        from .bcbuiltins import new_builtin
        return new_builtin(self, t, args, line)

    def e_mcall(self, n):
        from .bcbuiltins import method_call
        return method_call(self, n)

    # -------------------------------------------------------------- output
    def assemble(self) -> bytes:
        return assemble(self)


def _num_conv(src, to) -> list:
    """The instruction names converting a value of type src to type to (both numeric)."""
    if to == "double":
        return {"float": ["F2D"], "long": ["L2D"]}.get(src, ["I2D"])
    if to == "float":
        return {"double": ["D2F"], "long": ["L2F"]}.get(src, ["I2F"])
    if src in FLOATS:
        p = "F" if src == "float" else "D"
        if to == "long":
            return [p + "2L"]
        if to == "int":
            return [p + "2I"]
        return [p + "2I", {"short": "I2S", "byte": "I2B", "char": "I2C"}[to]]
    if to == "long":
        return ["I2L"] if src != "long" else []
    if to == "int":
        return ["L2I"] if src == "long" else []
    pre = ["L2I"] if src == "long" else []
    if to == "short":
        return pre + ([] if src == "byte" else ["I2S"])
    if to == "byte":
        return pre + ["I2B"]
    if to == "char":
        return pre + ["I2C"]
    raise AssertionError((src, to))


# ---------------------------------------------------------------------- assembly
_FMT = {"i": "<i", "f": "<f", "H": "<H", "B": "<B", "b": "<b"}


def _stack_effect(ins, funcs) -> tuple[int, int]:
    op = ins[0]
    if op == "CALL":
        f = ins[1]
        return len(f.params), (0 if f.ret == "void" else 1)
    if op == "CALLM":
        f = ins[1]
        return len(f.params) + 1, (0 if f.ret == "void" else 1)
    if op == "NATIVE":
        return ins[2], (1 if S.NATIVES[ins[1]][1] else 0)
    if op == "NEWMULTI":
        return ins[2], 1
    _n, _f, pops, pushes = S.OPS[S.OP[op]]
    return pops, pushes


def _encode_func(f: Func, funcs) -> tuple[bytes, int]:
    """Instructions -> bytes (labels resolved) and the maximum stack depth."""
    # pass 1: offsets
    pos = 0
    for ins in f.code:
        if ins[0] == "L":
            ins[1].pos = pos
            continue
        fmt = S.OPS[S.OP[ins[0]]][1]
        pos += 1 + sum(struct.calcsize(_FMT[c]) for c in fmt)
    if pos > MAX_CODE:
        raise Unsupported(f"{f.name}() is too long for the VM")
    out = bytearray()
    for ins in f.code:
        if ins[0] == "L":
            continue
        name = ins[0]
        fmt = S.OPS[S.OP[name]][1]
        out.append(S.OP[name])
        for c, v in zip(fmt, ins[1:]):
            if isinstance(v, Label):
                v = v.pos
            elif isinstance(v, Func):
                v = v.index
            if c == "f":
                out += struct.pack("<f", v)
            elif c == "i":
                out += struct.pack("<i", v)
            else:
                out += struct.pack(_FMT[c], v)
    # stack depth: a walk over the flow graph (both sides verify the same way)
    depth_at: dict[int, int] = {}
    idx_of_pos: dict[int, int] = {}
    p = 0
    items = [ins for ins in f.code if ins[0] != "L"]
    offs = []
    for i, ins in enumerate(items):
        idx_of_pos[p] = i
        offs.append(p)
        p += 1 + sum(struct.calcsize(_FMT[c]) for c in S.OPS[S.OP[ins[0]]][1])
    work = [(0, 0)]
    best = 0
    while work:
        i, d = work.pop()
        while i < len(items):
            if offs[i] in depth_at:
                if depth_at[offs[i]] != d:
                    raise AssertionError(f"stack depth mismatch in {f.name} at {offs[i]}")
                break
            depth_at[offs[i]] = d
            ins = items[i]
            pops, pushes = _stack_effect(ins, funcs)
            if d < pops:
                raise AssertionError(f"stack underflow in {f.name} at {offs[i]} ({ins[0]})")
            d = d - pops + pushes
            best = max(best, d)
            op = ins[0]
            if op in ("RET", "RETV", "FAULT"):
                break
            if op in ("JMP", "JZ", "JNZ", "LOOP"):
                tgt = idx_of_pos[ins[1].pos] if ins[1].pos in idx_of_pos else len(items)
                if op in ("JMP", "LOOP"):
                    i = tgt
                    continue
                work.append((tgt, d))
            i += 1
    return bytes(out), best


def assemble(bc: BCompiler) -> bytes:
    """The program as the VM loads it (little endian):
    magic "PDEB", u8 version, u8 flags (bit0: uses the clock), u16 reserved,
    i16 entry[globals, static, setup, draw] (-1 = none),
    u16 nglobals + u8 slot flavour each (I L F D R), u16 nclasses + per class (u16 nfields + u8 flavour each),
    u16 nlongs + i64 each, u16 ndoubles + f64 each, u16 nstrings + (u16 len + u16 units) each,
    u16 nfuncs + per function (u8 nparams, u8 has_this, u16 nslots, u16 maxstack, u8 returns, u32 code offset, u16 code length),
    u32 code size + code."""
    funcs = bc.all_funcs
    blobs = []
    for f in funcs:
        code, depth = _encode_func(f, funcs)
        if f.nslots > 255 * 4 or len(f.params) > 250:
            raise Unsupported(f"{f.name}() has too many variables for the VM")
        blobs.append((f, code, depth))
    out = bytearray(S.MAGIC)
    out += struct.pack("<BBH", S.VERSION, 1 if bc.uses_clock else 0, 0)
    out += struct.pack("<4h", bc.entry["globals"], bc.entry["static"], bc.entry["setup"], bc.entry["draw"])
    gl = sorted(bc.gtypes.values(), key=lambda v: v[0])
    out += struct.pack("<H", len(gl)) + bytes(ord(_prim_slot(t)) for _s, t in gl)
    out += struct.pack("<H", len(bc.classes))
    for ci in bc.classes.values():
        fl = sorted(ci.fields.values(), key=lambda v: v[0])
        out += struct.pack("<H", len(fl)) + bytes(ord(_prim_slot(t)) for _s, t in fl)
    for table, fmt in ((bc.longs, "<q"), (bc.doubles, "<d")):
        vals = sorted(table.values())
        out += struct.pack("<H", len(vals))
        for _i, v in vals:
            out += struct.pack(fmt, v)
    strs = sorted(bc.strings.values())
    out += struct.pack("<H", len(strs))
    for _i, v in strs:
        units = v.encode("utf-16-le")
        out += struct.pack("<H", len(units) // 2) + units
    out += struct.pack("<H", len(funcs))
    off = 0
    for f, code, depth in blobs:
        # flags: bit 0 a method (the receiver is on top of the arguments), bit 1 a synthetic
        # constructor wrapper (not counted as a call level: Python counts the constructor only)
        fl = (1 if f.has_this else 0) | (2 if f.kind == "new" else 0)
        out += struct.pack("<BBHHBIH", len(f.params), fl, f.nslots, depth + 2,
                           0 if f.ret == "void" else 1, off, len(code))
        off += len(code)
    out += struct.pack("<I", off)
    for _f, code, _d in blobs:
        out += code
    return bytes(out)


def _calls_size_p3d(node) -> bool:
    """Whether the AST calls size(w, h, P3D) anywhere."""
    stack = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, (list, tuple)):
            if len(n) == 4 and n[0] == "call" and n[2] == "size" and isinstance(n[3], list) and len(n[3]) == 3:
                a = n[3][2]
                if isinstance(a, tuple) and a[:1] == ("name",) and a[-1] == "P3D":
                    return True
            stack.extend(x for x in n if isinstance(x, (list, tuple)))
    return False


def compile_bytecode(src: str) -> bytes:
    """Source -> VM program. PdeError when the sketch is invalid (as the interpreter would
    say), Unsupported when it is valid but the VM cannot run it."""
    from . import callback_hints, link_ast    # the art library's parts the sketch calls (art/mathlib.py)
    try:
        ast = link_ast(src, parse(src))
        bc = BCompiler(callback_hints())
        bc.p3d = _calls_size_p3d(ast)
        bc.compile(ast)
        return bc.assemble()
    except RecursionError:
        raise PdeError("depth", "the code is nested too deeply") from None


del math, f32, jstr, PY_CALLS
