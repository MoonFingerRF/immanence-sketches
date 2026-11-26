"""The fixed table of what a sketch may call: Processing's functions, java.lang.Math, and the
methods of String, arrays, PVector and ArrayList. Each entry turns compiled arguments (compiler.E)
into one closure; the table IS the sandbox's surface -- a name not here does not exist.
"""
from __future__ import annotations

import math
from array import array

from .compiler import (FLOATS, INTS, NOCONST, NUMS, E, JList, PVec, _ITEMSIZE, _TYPECODE, const_e, d2i, d2l,
                       jfloat_str, jstr, norm, tname, w32, w64)
from .errors import PdeError
from .jrandom import f32

MAX_LIST = 262144


def _safe(fn):
    def g(*a):
        try:
            return fn(*a)
        except ValueError:
            return math.nan
        except OverflowError:
            return math.inf
    return g


_sqrt, _log, _asin, _acos, _exp, _pow = (_safe(math.sqrt), _safe(math.log), _safe(math.asin), _safe(math.acos),
                                         _safe(math.exp), _safe(math.pow))


def _tan(x):
    try:
        return math.tan(x)
    except (ValueError, OverflowError):
        return math.nan


def _trig(fn):
    def g(x):
        if x != x or x in (math.inf, -math.inf):
            return math.nan
        return fn(x)
    return g


_sin, _cos = _trig(math.sin), _trig(math.cos)


def _fdiv(a, b):
    """Java's float/double division: x/0 is +-inf (NaN for 0/0), never an error."""
    try:
        return a / b
    except ZeroDivisionError:
        if a != a or a == 0:
            return math.nan
        return math.copysign(math.inf, a) * math.copysign(1.0, b)


_DEG_TO_RAD = f32(f32(math.pi) / 180.0)      # PConstants: PI / 180.0f and 180.0f / PI, in float
_RAD_TO_DEG = f32(180.0 / f32(math.pi))


def _fargs(c, args, line):
    """Arguments of a PApplet method declared `float`: converted as Java converts them (an int
    rounds to float, a double narrows -- leniently, as a cast), evaluated once each."""
    return [(c.conv(a, "float", line, explicit=True) if a.t != "float" else a).fn for a in args]


def _f(e: E):
    """The argument as a number closure (ints are fine where Processing wants a float)."""
    return e.fn


def _need_nums(c, args, line, name):
    for a in args:
        if a.t not in NUMS:
            c.err(f"{name}() needs numbers, got {tname(a.t)}", line)


def _void(fn):
    return E(fn, "void")


def _jround(v):
    if v != v:
        return 0
    return d2i(math.floor(v + 0.5))


# ---------------------------------------------------------------------- builders
def _arg32(a: E):
    """An argument on its way into the drawing API: a float computed in double precision is
    rounded to Java float here, where Processing's own last rounding happens (5 * 17.3 is 86.5 in
    Java, 86.4999996 in double -- a whole pixel row apart once a crisp line rounds it)."""
    if a.t == "float" and not a.exact:
        f = a.fn
        return lambda L: f32(f(L))
    return a.fn


def api_call(method, arities, ret="void", num=True, name=None):
    """An API method taking plain numbers."""
    def h(c, args, line):
        nm = name or method
        if len(args) not in arities:
            c.err(f"{nm}() does not take {len(args)} argument(s)", line)
        if num:
            _need_nums(c, args, line, nm)
        m = getattr(c.api, method)
        fs = [_arg32(a) for a in args]
        if len(fs) == 0:
            fn = lambda L: m()
        elif len(fs) == 1:
            f0 = fs[0]
            fn = lambda L: m(f0(L))
        elif len(fs) == 2:
            f0, f1 = fs
            fn = lambda L: m(f0(L), f1(L))
        elif len(fs) == 3:
            f0, f1, f2 = fs
            fn = lambda L: m(f0(L), f1(L), f2(L))
        elif len(fs) == 4:
            f0, f1, f2, f3 = fs
            fn = lambda L: m(f0(L), f1(L), f2(L), f3(L))
        else:
            fn = lambda L: m(*[f(L) for f in fs])
        return E(fn, ret, exact=(ret == "float"))
    return h


def colour_call(method, ret="void"):
    def h(c, args, line):
        if not 1 <= len(args) <= 4:
            c.err(f"{method}() takes 1 to 4 numbers", line)
        _need_nums(c, args, line, method)
        ints = tuple(a.t in ("int", "short", "byte", "char") for a in args)
        m = getattr(c.api, method)
        fs = [a.fn for a in args]
        if len(fs) == 1:
            f0 = fs[0]
            return E(lambda L: m((f0(L),), ints), ret)
        return E(lambda L: m(tuple(f(L) for f in fs), ints), ret)
    return h


def math1(fn, ret="float"):
    def h(c, args, line):
        if len(args) != 1:
            c.err("this takes one number", line)
        _need_nums(c, args, line, "math")
        a0 = c.conv(args[0], "float", line, explicit=True) if args[0].t != "float" else args[0]
        f0 = a0.fn
        if a0.const is not NOCONST:
            return const_e(f32(fn(a0.const)) if ret == "float" else fn(a0.const), ret)
        if ret == "float":
            return E(lambda L: f32(fn(f0(L))), ret, exact=True)
        return E(lambda L: fn(f0(L)), ret)
    return h


def _minmax(pick, name):
    def h(c, args, line):
        if len(args) == 1:
            a = args[0]
            if not (isinstance(a.t, tuple) and a.t[0] == "[]" and a.t[1] in NUMS):
                c.err(f"{name}() of one thing needs a number array", line)
            f0 = a.fn

            def arr(L):
                v = f0(L)
                if not v:
                    raise PdeError("runtime", f"{name}() of an empty array", line)
                return pick(v)
            return E(arr, a.t[1])
        if len(args) not in (2, 3):
            c.err(f"{name}() takes 2 or 3 numbers", line)
        _need_nums(c, args, line, name)
        t = "float" if any(a.t in FLOATS for a in args) else "int"
        fs = [(c.conv(a, t, line) if a.t != t else a).fn for a in args]
        if len(fs) == 2:
            f0, f1 = fs
            return E(lambda L: pick(f0(L), f1(L)), t, exact=True)
        f0, f1, f2 = fs
        return E(lambda L: pick(f0(L), f1(L), f2(L)), t, exact=True)
    return h


def h_abs(c, args, line):
    if len(args) != 1 or args[0].t not in NUMS:
        c.err("abs() takes one number", line)
    a = args[0]
    f0 = a.fn
    if a.t in INTS:
        return E(lambda L: w32(abs(f0(L))), "int")
    return E(lambda L: abs(f0(L)), "float", exact=a.exact)


def h_constrain(c, args, line):
    if len(args) != 3:
        c.err("constrain() takes 3 numbers", line)
    _need_nums(c, args, line, "constrain")
    t = "float" if any(a.t in FLOATS for a in args) else "int"
    f0, f1, f2 = [(c.conv(a, t, line) if a.t != t else a).fn for a in args]

    def fn(L):
        v, lo, hi = f0(L), f1(L), f2(L)
        return lo if v < lo else hi if v > hi else v
    return E(fn, t, exact=True)


def h_map(c, args, line):
    if len(args) != 5:
        c.err("map() takes 5 numbers", line)
    _need_nums(c, args, line, "map")
    f0, f1, f2, f3, f4 = _fargs(c, args, line)

    def fn(L):
        v, a1, a2, b1, b2 = f0(L), f1(L), f2(L), f3(L), f4(L)
        # PApplet.map in Java float: every operation rounds; /0 gives inf or NaN, not an error
        return f32(b1 + f32(f32(b2 - b1) * f32(_fdiv(f32(v - a1), f32(a2 - a1)))))
    return E(fn, "float", exact=True)


def h_lerp(c, args, line):
    if len(args) != 3:
        c.err("lerp() takes 3 numbers", line)
    _need_nums(c, args, line, "lerp")
    f0, f1, f2 = _fargs(c, args, line)
    def fn(L):
        a, b, t = f0(L), f1(L), f2(L)
        return f32(a + f32(f32(b - a) * t))      # PApplet.lerp in Java float
    return E(fn, "float", exact=True)


def h_norm(c, args, line):
    if len(args) != 3:
        c.err("norm() takes 3 numbers", line)
    _need_nums(c, args, line, "norm")
    f0, f1, f2 = _fargs(c, args, line)

    def fn(L):
        v, lo, hi = f0(L), f1(L), f2(L)
        return f32(_fdiv(f32(v - lo), f32(hi - lo)))      # map(v, lo, hi, 0, 1) in Java float
    return E(fn, "float", exact=True)


def h_dist(c, args, line):
    _need_nums(c, args, line, "dist")
    fs = _fargs(c, args, line)
    if len(fs) == 4:
        f0, f1, f2, f3 = fs
        def d4(L):
            dx, dy = f32(f2(L) - f0(L)), f32(f3(L) - f1(L))
            return f32(_sqrt(f32(f32(dx * dx) + f32(dy * dy))))    # sqrt(sq(dx) + sq(dy)) in Java float
        return E(d4, "float", exact=True)
    if len(fs) == 6:
        f0, f1, f2, f3, f4, f5 = fs
        def d6(L):
            dx, dy, dz = f32(f3(L) - f0(L)), f32(f4(L) - f1(L)), f32(f5(L) - f2(L))
            return f32(_sqrt(f32(f32(f32(dx * dx) + f32(dy * dy)) + f32(dz * dz))))
        return E(d6, "float", exact=True)
    c.err("dist() takes 4 or 6 numbers", line)


def h_mag(c, args, line):
    _need_nums(c, args, line, "mag")
    fs = _fargs(c, args, line)
    if len(fs) not in (2, 3):
        c.err("mag() takes 2 or 3 numbers", line)
    def mg(L):
        acc = None
        for f in fs:
            v = f(L)
            sq = f32(v * v)
            acc = sq if acc is None else f32(acc + sq)
        return f32(_sqrt(acc))                   # PApplet.mag in Java float
    return E(mg, "float", exact=True)


def h_sq(c, args, line):
    if len(args) != 1 or args[0].t not in NUMS:
        c.err("sq() takes one number", line)
    f0 = _fargs(c, args, line)[0]

    def sq(L):
        v = f0(L)
        return f32(v * v)
    return E(sq, "float", exact=True)


def h_pow(c, args, line):
    if len(args) != 2:
        c.err("pow() takes 2 numbers", line)
    _need_nums(c, args, line, "pow")
    f0, f1 = _fargs(c, args, line)
    return E(lambda L: f32(_pow(f0(L), f1(L))), "float", exact=True)


def h_atan2(c, args, line):
    if len(args) != 2:
        c.err("atan2() takes 2 numbers", line)
    _need_nums(c, args, line, "atan2")
    f0, f1 = _fargs(c, args, line)
    return E(lambda L: f32(math.atan2(f0(L), f1(L))), "float", exact=True)


def h_floor(fn):
    def h(c, args, line):
        if len(args) != 1 or args[0].t not in NUMS:
            c.err("this takes one number", line)
        f0 = _fargs(c, args, line)[0]      # PApplet.floor/ceil(float)

        def fl(L):
            v = f0(L)                  # evaluated once (floor(random(9)) draws one number)
            return d2i(fn(v)) if v == v else 0
        return E(fl, "int")
    return h


def _floor_safe(v):
    try:
        return math.floor(v)
    except (ValueError, OverflowError):
        return v


def _ceil_safe(v):
    try:
        return math.ceil(v)
    except (ValueError, OverflowError):
        return v


def h_round(c, args, line):
    if len(args) != 1 or args[0].t not in NUMS:
        c.err("round() takes one number", line)
    f0 = _fargs(c, args, line)[0]          # PApplet.round(float)
    return E(lambda L: _jround(f0(L)), "int")


def h_random(c, args, line):
    if len(args) not in (1, 2):
        c.err("random() takes 1 or 2 numbers", line)
    _need_nums(c, args, line, "random")
    r = c.api.rand
    if len(args) == 1:
        f0 = _fargs(c, args, line)[0]
        return E(lambda L: r.random(f0(L)), "float", exact=True)
    f0, f1 = _fargs(c, args, line)
    return E(lambda L: r.random2(f0(L), f1(L)), "float", exact=True)


def h_noise(c, args, line):
    if not 1 <= len(args) <= 3:
        c.err("noise() takes 1 to 3 numbers", line)
    _need_nums(c, args, line, "noise")
    api = c.api
    fs = [a.fn for a in args]
    B = c.B
    r = api.rand
    if len(fs) == 1:
        f0 = fs[0]

        def n1(L):
            B.steps += 4
            return r.noise(f0(L))
        return E(n1, "float", exact=True)
    if len(fs) == 2:
        f0, f1 = fs

        def n2(L):
            B.steps += 4
            return r.noise(f0(L), f1(L))
        return E(n2, "float", exact=True)
    f0, f1, f2 = fs

    def n3(L):
        B.steps += 4
        return r.noise(f0(L), f1(L), f2(L))
    return E(n3, "float", exact=True)


# ---------------------------------------------------------------------- conversions
def h_int(c, args, line):
    if len(args) != 1:
        c.err("int() takes one value", line)
    a = args[0]
    if a.t in NUMS:
        return c.conv(a, "int", line, explicit=True)
    if a.t == "boolean":
        f0 = a.fn
        return E(lambda L: 1 if f0(L) else 0, "int")
    if a.t == "String":
        f0 = a.fn

        def parse(L):
            s = f0(L)
            try:
                return w32(int((s or "").strip()))
            except ValueError:
                try:
                    return d2i(float(s))
                except (ValueError, TypeError):
                    return 0
        return E(parse, "int")
    if isinstance(a.t, tuple) and a.t[0] == "[]" and a.t[1] in NUMS:
        f0 = a.fn
        return E(lambda L: array("i", [d2i(v) for v in f0(L)]), ("[]", "int"))
    c.err(f"int() of a {tname(a.t)}", line)


def h_float(c, args, line):
    if len(args) != 1:
        c.err("float() takes one value", line)
    a = args[0]
    if a.t in NUMS:
        return c.conv(a, "float", line, explicit=True)
    if a.t == "String":
        f0 = a.fn

        def parse(L):
            try:
                return f32(float((f0(L) or "").strip()))
            except ValueError:
                return math.nan
        return E(parse, "float", exact=True)
    if a.t == "boolean":
        f0 = a.fn
        return E(lambda L: 1.0 if f0(L) else 0.0, "float", exact=True)
    c.err(f"float() of a {tname(a.t)}", line)


def h_str(c, args, line):
    if len(args) != 1:
        c.err("str() takes one value", line)
    a = args[0]
    return c.conv(a, "String", line)


def h_char(c, args, line):
    if len(args) != 1 or args[0].t not in NUMS:
        c.err("char() takes a number", line)
    return c.conv(args[0], "char", line, explicit=True)


def h_byte(c, args, line):
    if len(args) != 1 or args[0].t not in NUMS:
        c.err("byte() takes a number", line)
    return c.conv(args[0], "byte", line, explicit=True)


def h_boolean(c, args, line):
    if len(args) != 1:
        c.err("boolean() takes one value", line)
    a = args[0]
    f0 = a.fn
    if a.t in NUMS:
        return E(lambda L: f0(L) != 0, "boolean")
    if a.t == "String":
        return E(lambda L: (f0(L) or "").lower() == "true", "boolean")
    if a.t == "boolean":
        return a
    c.err(f"boolean() of a {tname(a.t)}", line)


def _nf_num(v, left, right):
    neg = v < 0
    v = abs(v)
    if right is None:
        s = str(int(v)).rjust(max(0, int(left)), "0")
    else:
        s = f"{v:.{max(0, int(right))}f}"
        ip, _, fp = s.partition(".")
        ip = ip.rjust(max(0, int(left)), "0")
        s = ip + ("." + fp if fp else "")
    return ("-" if neg else "") + s


def h_nf(kind):
    def h(c, args, line):
        if len(args) not in (1, 2, 3):
            c.err("nf() takes 1 to 3 arguments", line)
        a = args[0]
        if a.t not in NUMS:
            c.err("nf() formats a number", line)
        fs = [x.fn for x in args]
        isint = a.t in INTS

        def fn(L):
            v = fs[0](L)
            left = fs[1](L) if len(fs) > 1 else 0
            right = fs[2](L) if len(fs) > 2 else (None if isint else None)
            if len(fs) == 1 and not isint:
                s = jfloat_str(v)
            else:
                s = _nf_num(v, left, right)
            if kind == "nfs" and v >= 0:
                s = " " + s
            elif kind == "nfp" and v >= 0:
                s = "+" + s
            elif kind == "nfc":
                ip, dot, fp = s.lstrip("-").partition(".")
                ip = f"{int(ip):,}" if ip.isdigit() else ip
                s = ("-" if s.startswith("-") else "") + ip + dot + fp
            return s
        return E(fn, "String")
    return h


def h_hex(c, args, line):
    if not args or args[0].t not in NUMS:
        c.err("hex() takes a number", line)
    f0 = args[0].fn
    n = args[1].fn if len(args) > 1 else None
    width = 8 if args[0].t == "int" else 4 if args[0].t == "char" else 2

    def fn(L):
        s = f"{f0(L) & 0xFFFFFFFF:08X}"[-(n(L) if n else width):]
        return s
    return E(fn, "String")


def h_unhex(c, args, line):
    f0 = args[0].fn

    def fn(L):
        try:
            return w32(int(f0(L), 16))
        except (ValueError, TypeError):
            return 0
    return E(fn, "int")


# ---------------------------------------------------------------------- arrays
def _arr_t(c, a, line, name):
    if not (isinstance(a.t, tuple) and a.t[0] == "[]"):
        c.err(f"{name}() needs an array", line)
    return a.t


def _copy_arr(v):
    return array(v.typecode, v) if isinstance(v, array) else list(v)


def h_append(c, args, line):
    if len(args) != 2:
        c.err("append() takes an array and a value", line)
    t = _arr_t(c, args[0], line, "append")
    val = c.store_conv(args[1], t[1], line)
    fa, fv = args[0].fn, val.fn
    B = c.B

    def fn(L):
        a = fa(L)
        B.allocate((len(a) + 1) * 8)
        b = _copy_arr(a)
        b.append(fv(L))
        return b
    return E(fn, t)


def h_shorten(c, args, line):
    t = _arr_t(c, args[0], line, "shorten")
    fa = args[0].fn
    return E(lambda L: _copy_arr(fa(L))[:-1], t)


def h_expand(c, args, line):
    t = _arr_t(c, args[0], line, "expand")
    fa = args[0].fn
    fn_ = args[1].fn if len(args) > 1 else None
    B = c.B
    tc = _TYPECODE.get(t[1])

    def fn(L):
        a = fa(L)
        n = fn_(L) if fn_ else max(1, len(a) * 2)
        if n > 4_000_000 or n < 0:
            raise PdeError("memory", "expand() to a huge array", line)
        B.allocate(n * 8)
        b = _copy_arr(a)[:n]
        pad = n - len(b)
        if pad > 0:
            if tc:
                b.extend(array(tc, bytes(pad * _ITEMSIZE[tc])))
            else:
                b.extend([None if t[1] not in ("boolean",) else False] * pad)
        return b
    return E(fn, t)


def h_subset(c, args, line):
    t = _arr_t(c, args[0], line, "subset")
    fa, fs = args[0].fn, args[1].fn
    fc = args[2].fn if len(args) > 2 else None

    def fn(L):
        a = fa(L)
        s = fs(L)
        n = fc(L) if fc else len(a) - s
        if s < 0 or n < 0 or s + n > len(a):
            raise PdeError("runtime", "subset() outside the array", line)
        return _copy_arr(a)[s:s + n]
    return E(fn, t)


def h_concat(c, args, line):
    t = _arr_t(c, args[0], line, "concat")
    fa, fb = args[0].fn, args[1].fn
    B = c.B

    def fn(L):
        a, b = fa(L), fb(L)
        B.allocate((len(a) + len(b)) * 8)
        out = _copy_arr(a)
        out.extend(b)
        return out
    return E(fn, t)


def h_reverse(c, args, line):
    t = _arr_t(c, args[0], line, "reverse")
    fa = args[0].fn
    return E(lambda L: _copy_arr(fa(L))[::-1], t)


def h_sort(c, args, line):
    t = _arr_t(c, args[0], line, "sort")
    fa = args[0].fn
    B = c.B

    def fn(L):
        a = fa(L)
        B.charge(len(a) * 2)
        b = _copy_arr(a)
        s = sorted(b)
        return array(b.typecode, s) if isinstance(b, array) else s
    return E(fn, t)


def h_arraycopy(c, args, line):
    fs = [a.fn for a in args]
    B = c.B
    if len(fs) == 2:
        def fn2(L):
            s, d = fs[0](L), fs[1](L)
            n = min(len(s), len(d))
            B.charge(n)
            d[:n] = s[:n]
        return _void(fn2)
    if len(fs) == 3:
        def fn3(L):
            s, d, n = fs[0](L), fs[1](L), fs[2](L)
            if n > len(s) or n > len(d) or n < 0:
                raise PdeError("runtime", "arrayCopy() outside the array", line)
            B.charge(n)
            d[:n] = s[:n]
        return _void(fn3)
    if len(fs) == 5:
        def fn5(L):
            s, sp, d, dp, n = (f(L) for f in fs)
            if sp < 0 or dp < 0 or n < 0 or sp + n > len(s) or dp + n > len(d):
                raise PdeError("runtime", "arrayCopy() outside the array", line)
            B.charge(n)
            d[dp:dp + n] = s[sp:sp + n]
        return _void(fn5)
    c.err("arrayCopy() takes 2, 3 or 5 arguments", line)


def h_join(c, args, line):
    fa, fs = args[0].fn, args[1].fn
    et = args[0].t[1] if isinstance(args[0].t, tuple) else "String"
    return E(lambda L: fs(L).join(jstr(x, et) for x in fa(L)), "String")


def h_split(c, args, line):
    fa, fs = args[0].fn, args[1].fn
    ch = args[1].t == "char"
    B = c.B

    def fn(L):
        s = fa(L) or ""
        d = chr(fs(L)) if ch else fs(L)
        parts = s.split(d) if d else list(s)
        B.allocate(len(parts) * 8 + len(s))
        return parts
    return E(fn, ("[]", "String"))


def h_split_tokens(c, args, line):
    fa = args[0].fn
    fd = args[1].fn if len(args) > 1 else None

    def fn(L):
        s = fa(L) or ""
        delims = fd(L) if fd else " \t\n\r\f "
        out, cur = [], ""
        for ch in s:
            if ch in delims:
                if cur:
                    out.append(cur)
                cur = ""
            else:
                cur += ch
        if cur:
            out.append(cur)
        return out
    return E(fn, ("[]", "String"))


def h_trim(c, args, line):
    fa = args[0].fn
    return E(lambda L: (fa(L) or "").strip(" \t\n\r\f "), "String")


def h_print(c, args, line):
    fs = [a.fn for a in args]
    B = c.B

    def fn(L):
        for f in fs:
            f(L)
        B.charge(16)
    return _void(fn)


def h_text(c, args, line):
    if len(args) not in (3, 4, 5):
        c.err("text() takes a value and a position (or a box)", line)
    a = args[0]
    api = c.api
    for x in args[1:]:
        if x.t not in NUMS:
            c.err("text()'s position must be numbers", line)
    if a.t == "String":
        sf = a.fn
    elif a.t == "char":
        f0 = a.fn
        sf = lambda L: chr(f0(L))
    elif a.t in ("int", "long", "short", "byte"):
        f0 = a.fn
        sf = lambda L: str(f0(L))
    elif a.t in FLOATS:
        f0 = a.fn
        # PGraphics.text(float) prints nfs(num, 0, 3) without the sign's space
        sf = lambda L: _text_float(f0(L))
    elif isinstance(a.t, tuple) and a.t[0] == "[]" and a.t[1] == "char":
        f0 = a.fn
        sf = lambda L: "".join(chr(ch) for ch in f0(L))
    else:
        f0, t0 = a.fn, a.t
        sf = lambda L: jstr(f0(L), t0)
    fs = [x.fn for x in args[1:]]
    if len(fs) == 2:
        fx, fy = fs
        return _void(lambda L: api.text(sf(L), fx(L), fy(L)))
    if len(fs) == 3:          # text(s, x, y, z)
        fx, fy, fz = fs
        # z is evaluated only in a P3D sketch (a 2D sketch's text(s, x, y, z) never ran it)
        return _void(lambda L: api.text_z(sf(L), fx(L), fy(L), fz(L)) if api.p3d else api.text(sf(L), fx(L), fy(L)))
    fx, fy, fw, fh = fs
    return _void(lambda L: api.text(sf(L), fx(L), fy(L), fw(L), fh(L)))


def _text_float(v):
    s = f"{abs(v):.3f}" if v == v else "NaN"
    return ("-" if v < 0 else "") + s


def h_ignore(ret="void"):
    def h(c, args, line):
        fs = [a.fn for a in args]

        def fn(L):
            for f in fs:
                f(L)
        return E(fn, ret)
    return h


def h_unavailable(what):
    def h(c, args, line):
        c.err(f"{what} is not available here", line)
    return h


def h_create_font(c, args, line):
    if not args:
        c.err("createFont() needs a name and a size", line)
    api = c.api
    fs = [a.fn for a in args]
    if len(fs) >= 2:
        f1 = fs[1]
        return E(lambda L: api.create_font(None, f1(L)), ("C", "PFont", ()))
    return E(lambda L: api.create_font(None, 12.0), ("C", "PFont", ()))


def h_text_font(c, args, line):
    api = c.api
    fs = [a.fn for a in args]
    if len(fs) == 1:
        f0 = fs[0]
        return _void(lambda L: api.text_font(f0(L)))
    f0, f1 = fs[:2]
    return _void(lambda L: api.text_font(f0(L), f1(L)))


def h_size(c, args, line):
    if len(args) < 2:
        c.err("size() takes a width and a height", line)
    api = c.api
    f0, f1 = args[0].fn, args[1].fn
    if len(args) >= 3 and args[2].t == "String":
        f2 = args[2].fn
        return _void(lambda L: api.size(f0(L), f1(L), f2(L)))
    return _void(lambda L: api.size(f0(L), f1(L)))


def h_rotate(c, args, line):
    """rotate(angle), or rotate(angle, x, y, z) about an axis (P3D)."""
    if len(args) == 4:
        return api_call("rotate_axis", (4,), name="rotate")(c, args, line)
    return api_call("rotate", (1,))(c, args, line)


def h_clock(what):
    def h(c, args, line):
        api = c.api
        return E(lambda L: api.clock(what), "int")
    return h


def h_blend_mode(c, args, line):
    return api_call("blend_mode", (1,), name="blendMode")(c, args, line)


PIMG = ("C", "PImage", ())
PGRT = ("C", "PGraphics", ())


def _is_image(t) -> bool:
    return isinstance(t, tuple) and t[0] == "C" and t[1] in ("PImage", "PGraphics")


def h_get(c, args, line):
    api = c.api
    fs = [a.fn for a in args]
    _need_nums(c, args, line, "get")
    if len(fs) == 0:
        return E(lambda L: api.get_image(), PIMG)
    if len(fs) == 2:
        f0, f1 = fs
        return E(lambda L: api.get(f0(L), f1(L)), "int")
    if len(fs) == 4:
        return E(lambda L: api.get_image(*[f(L) for f in fs]), PIMG)
    c.err("get() takes 0, 2 or 4 numbers", line)


def h_set(c, args, line):
    if len(args) != 3:
        c.err("set() takes x, y and a colour or an image", line)
    api = c.api
    f0, f1, f2 = (a.fn for a in args)
    if _is_image(args[2].t):
        return _void(lambda L: api.set_image(f0(L), f1(L), f2(L)))
    _need_nums(c, args, line, "set")
    return _void(lambda L: api.set(f0(L), f1(L), f2(L)))


def h_copy(c, args, line):
    api = c.api
    fs = [a.fn for a in args]
    if len(args) == 9 and _is_image(args[0].t):
        _need_nums(c, args[1:], line, "copy")
        return _void(lambda L: api.copy_region(*[f(L) for f in fs]))
    if len(args) == 8:
        _need_nums(c, args, line, "copy")
        return _void(lambda L: api.copy_region(*[f(L) for f in fs]))
    c.err("copy() takes 8 numbers (or an image and 8 numbers)", line)


def h_image(c, args, line):
    if len(args) not in (3, 5) or not _is_image(args[0].t):
        c.err("image() takes an image (a PImage or PGraphics) and x, y (and a width and height)", line)
    _need_nums(c, args[1:], line, "image")
    api = c.api
    fs = [_arg32(a) for a in args]
    if len(fs) == 3:
        f0, f1, f2 = fs
        return _void(lambda L: api.image(f0(L), f1(L), f2(L)))
    f0, f1, f2, f3, f4 = fs
    return _void(lambda L: api.image(f0(L), f1(L), f2(L), f3(L), f4(L)))


def h_create_graphics(c, args, line):
    if len(args) not in (2, 3):
        c.err("createGraphics() takes a width and a height", line)
    api = c.api
    f0, f1 = args[0].fn, args[1].fn
    if len(args) == 3:
        f2 = args[2].fn
        return E(lambda L: api.create_graphics(f0(L), f1(L), f2(L)), PGRT)
    return E(lambda L: api.create_graphics(f0(L), f1(L)), PGRT)


def h_create_image(c, args, line):
    if len(args) != 3:
        c.err("createImage() takes a width, a height and RGB, ARGB or ALPHA", line)
    api = c.api
    f0, f1, f2 = (a.fn for a in args)
    return E(lambda L: api.create_image(f0(L), f1(L), f2(L)), PIMG)


# The drawing calls a PGraphics has (pg.ellipse(...)): compiled like the sketch's own calls, with
# the surface's state swapped in while the call runs.
PG_CALLS = {
    "background", "fill", "noFill", "stroke", "noStroke", "strokeWeight", "strokeCap", "strokeJoin", "clear",
    "colorMode", "smooth", "noSmooth", "point", "line", "rect", "square", "ellipse", "circle", "arc", "triangle",
    "quad", "bezier", "curve", "beginShape", "endShape", "vertex", "bezierVertex", "quadraticVertex",
    "curveVertex", "beginContour", "endContour", "rectMode", "ellipseMode", "curveTightness", "pushMatrix",
    "popMatrix", "push", "pop", "pushStyle", "popStyle", "translate", "rotate", "scale", "shearX", "shearY",
    "resetMatrix", "text", "textSize", "textAlign", "textLeading", "textWidth", "textFont", "loadPixels",
    "updatePixels", "get", "set", "image", "imageMode", "filter", "blendMode", "texture", "noTexture",
    "strokeTexture", "noStrokeTexture", "textureSeed", "copy", "tint", "noTint", "applyMatrix",
}


class _Proxy:
    """Stands for "the surface being drawn on" inside a pg.method() call: every Api method looked up
    at compile time (a fixed name from this table, never the sketch's) goes to the current target."""
    __slots__ = ("target",)

    def __init__(self, target) -> None:
        self.target = target

    def __getattr__(self, name):
        def call(*a):
            return getattr(self.target, name)(*a)
        return call


def pg_method(c, fo, name, args, line) -> E:
    if name == "beginDraw" and not args:
        o = _this_obj(fo, line, "PGraphics")
        return _void(lambda L: o(L).api.begin_draw())
    if name == "endDraw" and not args:
        o = _this_obj(fo, line, "PGraphics")
        return _void(lambda L: o(L).api.end_draw())
    if name not in PG_CALLS:
        c.err(f"PGraphics has no method {name}() here", line)
    proxy = _Proxy(c.api)
    real = c.api
    c.api = proxy
    try:
        e = CALLS[name](c, args, line)
        inner, ret = e.fn, e.t
    finally:
        c.api = real
    o = _this_obj(fo, line, "PGraphics")

    def call(L):
        pg = o(L)
        prev = proxy.target
        proxy.target = pg.api
        try:
            return inner(L)
        finally:
            proxy.target = prev
    return E(call, ret)


def pimage_method(c, fo, name, args, line) -> E:
    o = _this_obj(fo, line, "PImage")
    fs = [a.fn for a in args]
    if name in ("loadPixels", "updatePixels") and len(args) in (0, 4):
        return _void(lambda L: o(L))
    if name == "get" and len(args) == 2:
        f0, f1 = fs

        def gt(L):
            img = o(L)
            x, y = int(f0(L)), int(f1(L))
            return img.pixels[y * img.w + x] if 0 <= x < img.w and 0 <= y < img.h else 0
        return E(gt, "int")
    if name == "set" and len(args) == 3:
        f0, f1, f2 = fs

        def st(L):
            img = o(L)
            x, y = int(f0(L)), int(f1(L))
            if 0 <= x < img.w and 0 <= y < img.h:
                img.pixels[y * img.w + x] = w32(int(f2(L)))
        return _void(st)
    if name in ("copy", "get") and not args:
        def cp(L):
            img = o(L)
            c.B.allocate(4 * len(img.pixels))
            return type(img)(img.w, img.h, img.fmt, array("i", img.pixels))
        return E(cp, PIMG)
    c.err(f"PImage has no method {name}() here", line)


def h_hue_sat(which):
    def h(c, args, line):
        f0 = args[0].fn
        api = c.api

        def fn(L):
            v = int(f0(L))
            r, g, b = (v >> 16) & 255, (v >> 8) & 255, v & 255
            mx, mn = max(r, g, b), min(r, g, b)
            if which == "saturation":
                s = (mx - mn) / mx if mx else 0.0
                return f32(s * api.st.cmax[1])
            if mx == mn:
                return 0.0
            rc, gc, bc = (mx - r) / (mx - mn), (mx - g) / (mx - mn), (mx - b) / (mx - mn)
            hh = bc - gc if r == mx else 2.0 + rc - bc if g == mx else 4.0 + gc - rc
            hh = (hh / 6.0) % 1.0
            return f32(hh * api.st.cmax[0])
        return E(fn, "float", exact=True)
    return h


def h_background(c, args, line):
    """background(...) as in Processing, plus the texture extension: background(gray, "dots", 8),
    background(r, g, b, "hatch"), background(g1, g2, "vgradient")."""
    names = [i for i, a in enumerate(args) if a.t == "String"]
    if not names:
        return colour_call("background")(c, args, line)
    k = names[0]
    nums, after = args[:k], args[k + 1:]
    if len(names) > 1 or not 1 <= len(nums) <= 4 or len(after) > 1:
        c.err('a textured background is background(gray, "texture", scale) or background(g1, g2, "vgradient")', line)
    _need_nums(c, nums + after, line, "background")
    ints = tuple(a.t in ("int", "short", "byte", "char") for a in nums)
    api = c.api
    fs = [a.fn for a in nums]
    fname = args[k].fn
    fe = after[0].fn if after else None
    return _void(lambda L: api.background_texture([f(L) for f in fs], ints, fname(L), fe(L) if fe else None))


def h_texture(method):
    def h(c, args, line):
        if not args or args[0].t != "String" or len(args) > 2:
            c.err(f'{method}() takes a texture name and an optional number, e.g. {method}("hatch", 6)', line)
        _need_nums(c, args[1:], line, method)
        m = getattr(c.api, "texture" if method == "texture" else "stroke_texture")
        f0 = args[0].fn
        if len(args) == 2:
            f1 = args[1].fn
            return _void(lambda L: m(f0(L), f1(L)))
        return _void(lambda L: m(f0(L)))
    return h


CALLS = {
    # structure
    "size": h_size, "fullScreen": h_unavailable("fullScreen()"), "smooth": api_call("smooth", (0, 1)),
    "noSmooth": api_call("no_smooth", (0,), name="noSmooth"), "frameRate": h_ignore(),
    "noLoop": api_call("no_loop", (0,), name="noLoop"), "loop": api_call("loop", (0,)),
    "redraw": api_call("do_redraw", (0,), name="redraw"), "exit": api_call("exit", (0,)),
    "delay": h_ignore(), "pixelDensity": h_ignore(), "cursor": h_ignore(), "noCursor": h_ignore(),
    "millis": api_call("millis", (0,), "int"), "println": h_print, "print": h_print, "printArray": h_print,
    "second": h_clock("second"), "minute": h_clock("minute"), "hour": h_clock("hour"), "day": h_clock("day"),
    "month": h_clock("month"), "year": h_clock("year"), "textMode": h_ignore(),
    "surface": h_unavailable("surface"), "blendMode": h_blend_mode, "hint": api_call("hint", (1,)),
    # colour
    "background": h_background, "texture": h_texture("texture"), "strokeTexture": h_texture("strokeTexture"),
    "noTexture": api_call("no_texture", (0,), name="noTexture"),
    "noStrokeTexture": api_call("no_stroke_texture", (0,), name="noStrokeTexture"),
    "textureSeed": api_call("texture_seed", (1,), name="textureSeed"), "fill": colour_call("fill"), "stroke": colour_call("stroke"),
    "color": colour_call("color", "int"), "noFill": api_call("no_fill", (0,), name="noFill"),
    "noStroke": api_call("no_stroke", (0,), name="noStroke"), "clear": api_call("clear", (0,)),
    "strokeWeight": api_call("stroke_weight", (1,), name="strokeWeight"),
    "strokeCap": api_call("stroke_cap", (1,), name="strokeCap"),
    "strokeJoin": api_call("stroke_join", (1,), name="strokeJoin"),
    "colorMode": api_call("color_mode", (1, 2, 4, 5), name="colorMode"),
    "red": api_call("red", (1,), "float"), "green": api_call("green", (1,), "float"),
    "blue": api_call("blue", (1,), "float"), "alpha": api_call("alpha", (1,), "float"),
    "brightness": api_call("brightness", (1,), "float"), "hue": h_hue_sat("hue"),
    "saturation": h_hue_sat("saturation"), "lerpColor": api_call("lerp_color", (3,), "int", name="lerpColor"),
    "tint": h_ignore(), "noTint": h_ignore(),
    # shapes
    "point": api_call("point", (2, 3)), "line": api_call("line", (4, 6)), "rect": api_call("rect", (4, 5, 8)),
    "square": api_call("square", (3,)), "ellipse": api_call("ellipse", (4,)), "circle": api_call("circle", (3,)),
    "arc": api_call("arc", (6, 7)), "triangle": api_call("triangle", (6,)), "quad": api_call("quad", (8,)),
    "bezier": api_call("bezier", (8,)), "curve": api_call("curve", (8,)),
    "beginShape": api_call("begin_shape", (0, 1), name="beginShape"),
    "endShape": api_call("end_shape", (0, 1), name="endShape"),
    "vertex": api_call("vertex", (2, 3, 4)), "bezierVertex": api_call("bezier_vertex", (6, 9), name="bezierVertex"),
    "quadraticVertex": api_call("quadratic_vertex", (4, 6), name="quadraticVertex"),
    "curveVertex": api_call("curve_vertex", (2, 3), name="curveVertex"),
    "beginContour": api_call("begin_contour", (0,), name="beginContour"),
    "endContour": api_call("end_contour", (0,), name="endContour"),
    "rectMode": api_call("rect_mode", (1,), name="rectMode"),
    "ellipseMode": api_call("ellipse_mode", (1,), name="ellipseMode"),
    "curveTightness": api_call("curve_tightness_set", (1,), name="curveTightness"),
    "bezierDetail": h_ignore(), "curveDetail": h_ignore(),
    "bezierPoint": api_call("bezier_point", (5,), "float", name="bezierPoint"),
    "bezierTangent": api_call("bezier_tangent", (5,), "float", name="bezierTangent"),
    "curvePoint": api_call("curve_point", (5,), "float", name="curvePoint"),
    # matrix
    "pushMatrix": api_call("push_matrix", (0,), name="pushMatrix"),
    "popMatrix": api_call("pop_matrix", (0,), name="popMatrix"), "push": api_call("push", (0,)),
    "pop": api_call("pop", (0,)), "pushStyle": api_call("push_style", (0,), name="pushStyle"),
    "popStyle": api_call("pop_style", (0,), name="popStyle"), "translate": api_call("translate", (2, 3)),
    "rotate": h_rotate, "rotateZ": api_call("rotate", (1,), name="rotateZ"),
    "scale": api_call("scale", (1, 2, 3)), "shearX": api_call("shear_x", (1,), name="shearX"),
    "shearY": api_call("shear_y", (1,), name="shearY"), "resetMatrix": api_call("reset_matrix", (0,), name="resetMatrix"),
    "screenX": api_call("screen_x", (2, 3), "float", name="screenX"),
    "screenY": api_call("screen_y", (2, 3), "float", name="screenY"),
    # text
    "text": h_text, "textSize": api_call("text_size", (1,), name="textSize"),
    "textAlign": api_call("text_align", (1, 2), name="textAlign"),
    "textLeading": api_call("text_leading", (1,), name="textLeading"),
    "textWidth": api_call("text_width", (1,), "float", num=False, name="textWidth"),
    "textAscent": api_call("text_ascent", (0,), "float", name="textAscent"),
    "textDescent": api_call("text_descent", (0,), "float", name="textDescent"),
    "createFont": h_create_font, "loadFont": h_create_font, "textFont": h_text_font,
    # pixels
    "loadPixels": api_call("load_pixels", (0,), name="loadPixels"),
    "updatePixels": api_call("update_pixels", (0, 4), name="updatePixels"),
    "get": h_get, "set": h_set, "copy": h_copy,
    # randomness
    "random": h_random, "randomSeed": api_call("random_seed", (1,), name="randomSeed"),
    "randomGaussian": api_call("random_gaussian", (0,), "float", name="randomGaussian"),
    "noise": h_noise, "noiseSeed": api_call("noise_seed", (1,), name="noiseSeed"),
    "noiseDetail": api_call("noise_detail", (1, 2), name="noiseDetail"),
    # math
    "sin": math1(_sin), "cos": math1(_cos), "tan": math1(_tan), "asin": math1(_asin), "acos": math1(_acos),
    "atan": math1(math.atan), "sqrt": math1(_sqrt), "exp": math1(_exp), "log": math1(_log),
    "degrees": math1(lambda v: v * _RAD_TO_DEG), "radians": math1(lambda v: v * _DEG_TO_RAD), "atan2": h_atan2, "pow": h_pow,
    "abs": h_abs, "min": _minmax(min, "min"), "max": _minmax(max, "max"), "constrain": h_constrain,
    "map": h_map, "lerp": h_lerp, "norm": h_norm, "dist": h_dist, "mag": h_mag, "sq": h_sq,
    "floor": h_floor(_floor_safe), "ceil": h_floor(_ceil_safe), "round": h_round,
    # conversions
    "int": h_int, "float": h_float, "str": h_str, "char": h_char, "byte": h_byte, "boolean": h_boolean,
    "nf": h_nf("nf"), "nfs": h_nf("nfs"), "nfp": h_nf("nfp"), "nfc": h_nf("nfc"), "hex": h_hex, "unhex": h_unhex,
    # arrays and strings
    "append": h_append, "shorten": h_shorten, "expand": h_expand, "subset": h_subset, "concat": h_concat,
    "reverse": h_reverse, "sort": h_sort, "arrayCopy": h_arraycopy, "arraycopy": h_arraycopy, "join": h_join,
    "split": h_split, "splitTokens": h_split_tokens, "trim": h_trim,
    # never here
    "loadImage": h_unavailable("loadImage()"), "image": h_image,
    "createGraphics": h_create_graphics, "createImage": h_create_image,
    "imageMode": api_call("image_mode", (1,), name="imageMode"), "loadStrings": h_unavailable("loadStrings()"),
    "saveFrame": h_unavailable("saveFrame()"), "save": h_unavailable("save()"),
    "loadShape": h_unavailable("loadShape()"), "shape": h_unavailable("shape()"),
    "filter": api_call("filter", (1, 2)), "thread": h_unavailable("thread()"),
    "link": h_unavailable("link()"), "launch": h_unavailable("launch()"), "exec": h_unavailable("exec()"),
    "saveStrings": h_unavailable("saveStrings()"), "loadJSONObject": h_unavailable("loadJSONObject()"),
    "selectInput": h_unavailable("selectInput()"), "createWriter": h_unavailable("createWriter()"),
    "loadBytes": h_unavailable("loadBytes()"), "loadTable": h_unavailable("loadTable()"),
    # 3D (P3D, three.py); in a JAVA2D sketch each is an error that names size(240, 128, P3D),
    # except lights()/noLights(), which Processing's JAVA2D only warns about
    "rotateX": api_call("rotate_x", (1,), name="rotateX"), "rotateY": api_call("rotate_y", (1,), name="rotateY"),
    "box": api_call("box", (1, 3)), "sphere": api_call("sphere", (1,)),
    "sphereDetail": api_call("sphere_detail", (1, 2), name="sphereDetail"),
    "camera": api_call("camera", (0, 9)), "perspective": api_call("perspective", (0, 4)),
    "ortho": api_call("ortho", (0, 4, 6)), "frustum": api_call("frustum", (6,)),
    "lights": api_call("lights", (0,)), "noLights": api_call("no_lights", (0,), name="noLights"),
    "ambientLight": api_call("ambient_light", (3, 6), name="ambientLight"),
    "directionalLight": api_call("directional_light", (6,), name="directionalLight"),
    "pointLight": api_call("point_light", (6,), name="pointLight"),
    "spotLight": api_call("spot_light", (11,), name="spotLight"),
    "lightFalloff": api_call("light_falloff", (3,), name="lightFalloff"),
    "lightSpecular": api_call("light_specular", (3,), name="lightSpecular"),
    "ambient": colour_call("ambient"), "specular": colour_call("specular"), "emissive": colour_call("emissive"),
    "shininess": api_call("shininess", (1,)), "normal": api_call("normal", (3,)),
    "screenZ": api_call("screen_z", (2, 3), "float", name="screenZ"),
    "applyMatrix": api_call("apply_matrix", (6, 16), name="applyMatrix"),
    "beginCamera": h_unavailable("beginCamera()"), "endCamera": h_unavailable("endCamera()"),
}


def builtin_call(c, name, args, line) -> E:
    h = CALLS.get(name)
    if h is None:
        c.err(f"{name}() is not defined", line)
    return h(c, args, line)


# ---------------------------------------------------------------------- new
def new_builtin(c, t, args, line) -> E:
    name = t[1]
    B = c.B
    if name == "PVector":
        if len(args) not in (0, 2, 3):
            c.err("new PVector() takes 0, 2 or 3 numbers", line)
        _need_nums(c, args, line, "PVector")
        fs = [a.fn for a in args]

        def pv(L):
            B.allocate(64)
            return PVec(*[f(L) for f in fs])
        return E(pv, ("C", "PVector", ()))
    if name in ("IntList", "FloatList", "StringList"):
        et = LIST_ET[name]
        if len(args) > 1:
            c.err(f"new {name}() takes nothing or an array", line)
        f0 = args[0].fn if args else None

        def pl(L):
            B.allocate(64)
            j = JList()
            if f0 is not None:
                j.a = list(f0(L))
            return j
        return E(pl, ("C", name, ()))
    if name in ("HashMap", "Map"):
        def hm(L):
            B.allocate(64)
            return JMap()
        return E(hm, ("C", "HashMap", t[2] if t[2] else ()))
    if name in ("ArrayList", "List"):
        et = t[2] if t[2] else ()

        def al(L):
            B.allocate(64)
            return JList()
        return E(al, ("C", "ArrayList", et))
    if name == "String":
        if len(args) == 1 and args[0].t == ("[]", "char"):
            f0 = args[0].fn
            return E(lambda L: "".join(chr(ch) for ch in f0(L)), "String")
        if not args:
            return const_e("", "String")
        return c.conv(args[0], "String", line)
    c.err(f"new {name}() is not available here", line)


# ---------------------------------------------------------------------- methods
def _this_obj(fo, line, what):
    def get(L):
        o = fo(L)
        if o is None:
            raise PdeError("runtime", f"calling a method on a null {what}", line)
        return o
    return get


def _jhash(s: str) -> int:
    h = 0
    for ch in s:
        h = (31 * h + ord(ch)) & 0xFFFFFFFF
    return w32(h)


def string_method(c, fo, name, args, line) -> E:
    s = _this_obj(fo, line, "String")
    fs = [a.fn for a in args]
    ts = [a.t for a in args]
    B = c.B
    n = len(args)
    if name == "length" and n == 0:
        return E(lambda L: len(s(L)), "int")
    if name == "charAt" and n == 1:
        f0 = fs[0]

        def ca(L):
            v = s(L)
            i = f0(L)
            if not 0 <= i < len(v):
                raise PdeError("runtime", f"charAt({i}) outside the string", line)
            return ord(v[i])
        return E(ca, "char")
    if name == "substring" and n in (1, 2):
        def sub(L):
            v = s(L)
            a = fs[0](L)
            b = fs[1](L) if n == 2 else len(v)
            if a < 0 or b > len(v) or a > b:
                raise PdeError("runtime", "substring() outside the string", line)
            return v[a:b]
        return E(sub, "String")
    if name in ("indexOf", "lastIndexOf") and n in (1, 2):
        conv = (lambda v: chr(v)) if ts[0] in INTS else (lambda v: v)

        def idx(L):
            v = s(L)
            needle = conv(fs[0](L))
            if n == 2:
                start = fs[1](L)
                return v.find(needle, max(0, start)) if name == "indexOf" else v.rfind(needle, 0, max(0, start) + len(needle))
            return v.find(needle) if name == "indexOf" else v.rfind(needle)
        return E(idx, "int")
    if name == "toUpperCase" and n == 0:
        return E(lambda L: s(L).upper(), "String")
    if name == "toLowerCase" and n == 0:
        return E(lambda L: s(L).lower(), "String")
    if name == "trim" and n == 0:
        return E(lambda L: s(L).strip(), "String")
    if name == "isEmpty" and n == 0:
        return E(lambda L: len(s(L)) == 0, "boolean")
    if name == "equals" and n == 1:
        f0, t0 = fs[0], ts[0]
        return E(lambda L: t0 == "String" and s(L) == f0(L), "boolean")
    if name == "equalsIgnoreCase" and n == 1:
        f0 = fs[0]
        return E(lambda L: (f0(L) or "").lower() == s(L).lower(), "boolean")
    if name in ("startsWith", "endsWith", "contains") and n == 1:
        f0 = fs[0]
        op = {"startsWith": str.startswith, "endsWith": str.endswith, "contains": str.__contains__}[name]
        return E(lambda L: op(s(L), f0(L)), "boolean")
    if name == "compareTo" and n == 1:
        f0 = fs[0]

        def cmp(L):
            a, b = s(L), f0(L)
            for x, y in zip(a, b):
                if x != y:
                    return ord(x) - ord(y)
            return len(a) - len(b)
        return E(cmp, "int")
    if name == "concat" and n == 1:
        return c.binop("+", E(s, "String"), args[0], line)
    if name == "replace" and n == 2:
        conv = (lambda v: chr(v)) if ts[0] == "char" else (lambda v: v)
        f0, f1 = fs

        def rep(L):
            r = s(L).replace(conv(f0(L)), conv(f1(L)))
            if len(r) > 65536:
                raise PdeError("memory", "a string grew past 64 K characters", line)
            return r
        return E(rep, "String")
    if name == "split" and n == 1:
        f0 = fs[0]

        def spl(L):
            v, d = s(L), f0(L)
            if d in ("\\s+", " +", "\\s"):
                parts = v.split()
            else:
                parts = v.split(d.replace("\\", "")) if d else list(v)
            while parts and parts[-1] == "":
                parts.pop()
            B.allocate(len(parts) * 8 + len(v))
            return parts
        return E(spl, ("[]", "String"))
    if name == "toCharArray" and n == 0:
        return E(lambda L: array("H", [ord(ch) & 0xFFFF for ch in s(L)]), ("[]", "char"))
    if name == "hashCode" and n == 0:
        return E(lambda L: _jhash(s(L)), "int")
    if name == "repeat" and n == 1:
        f0 = fs[0]

        def rp(L):
            v, k = s(L), f0(L)
            if k < 0 or len(v) * k > 65536:
                raise PdeError("memory", "repeat() makes too long a string", line)
            return v * k
        return E(rp, "String")
    c.err(f"String has no method {name}() here", line)


def _pv_args(c, args, line, name):
    """A PVector method's vector argument: (x, y, z) closures from a PVector or 2-3 numbers."""
    if len(args) == 1 and args[0].t == ("C", "PVector", ()):
        f0 = args[0].fn

        def xyz(L):
            v = f0(L)
            if v is None:
                raise PdeError("runtime", "a null PVector", line)
            return v.x, v.y, v.z
        return xyz
    if len(args) in (2, 3):
        _need_nums(c, args, line, name)
        fs = [a.fn for a in args]
        if len(fs) == 2:
            return lambda L: (fs[0](L), fs[1](L), 0.0)
        return lambda L: (fs[0](L), fs[1](L), fs[2](L))
    c.err(f"PVector.{name}() needs a PVector or 2-3 numbers", line)


PVT = ("C", "PVector", ())


def pvector_method(c, fo, name, args, line) -> E:
    o = _this_obj(fo, line, "PVector")
    n = len(args)
    fs = [a.fn for a in args]
    B = c.B
    rand = c.api.rand
    if name in ("add", "sub"):
        xyz = _pv_args(c, args, line, name)
        sg = 1 if name == "add" else -1

        def addsub(L):
            v = o(L)
            x, y, z = xyz(L)
            v.x, v.y, v.z = f32(v.x + sg * x), f32(v.y + sg * y), f32(v.z + sg * z)
            return v
        return E(addsub, PVT)
    if name in ("mult", "div") and n == 1 and args[0].t in NUMS:
        f0 = fs[0]

        def md(L):
            v = o(L)
            k = f0(L)
            if name == "mult":
                v.x, v.y, v.z = f32(v.x * k), f32(v.y * k), f32(v.z * k)
            else:
                v.x, v.y, v.z = f32(_fd(v.x, k)), f32(_fd(v.y, k)), f32(_fd(v.z, k))
            return v
        return E(md, PVT)
    if name == "set":
        xyz = _pv_args(c, args, line, name)

        def st(L):
            v = o(L)
            v.x, v.y, v.z = (f32(a) for a in xyz(L))
            return v
        return E(st, PVT)
    if name in ("copy", "get") and n == 0:
        def cp(L):
            v = o(L)
            B.allocate(64)
            return PVec(v.x, v.y, v.z)
        return E(cp, PVT)
    if name == "mag" and n == 0:
        return E(lambda L: f32(math.sqrt(o(L).x ** 2 + o(L).y ** 2 + o(L).z ** 2)), "float", exact=True)
    if name == "magSq" and n == 0:
        return E(lambda L: f32(o(L).x ** 2 + o(L).y ** 2 + o(L).z ** 2), "float", exact=True)
    if name == "dist" and n == 1:
        f0 = fs[0]
        return E(lambda L: f32(math.sqrt((o(L).x - f0(L).x) ** 2 + (o(L).y - f0(L).y) ** 2 + (o(L).z - f0(L).z) ** 2)),
                 "float", exact=True)
    if name == "dot":
        xyz = _pv_args(c, args, line, name)

        def dot(L):
            v = o(L)
            x, y, z = xyz(L)
            return f32(v.x * x + v.y * y + v.z * z)
        return E(dot, "float", exact=True)
    if name == "normalize" and n == 0:
        def nz(L):
            v = o(L)
            m = math.sqrt(v.x ** 2 + v.y ** 2 + v.z ** 2)
            if m != 0 and m != 1:
                v.x, v.y, v.z = f32(v.x / m), f32(v.y / m), f32(v.z / m)
            return v
        return E(nz, PVT)
    if name in ("limit", "setMag") and n == 1:
        f0 = fs[0]

        def lim(L):
            v = o(L)
            k = f0(L)
            m = math.sqrt(v.x ** 2 + v.y ** 2 + v.z ** 2)
            if m and (name == "setMag" or m * m > k * k):
                v.x, v.y, v.z = f32(v.x / m * k), f32(v.y / m * k), f32(v.z / m * k)
            return v
        return E(lim, PVT)
    if name in ("heading", "heading2D") and n == 0:
        return E(lambda L: f32(math.atan2(o(L).y, o(L).x)), "float", exact=True)
    if name == "rotate" and n == 1:
        f0 = fs[0]

        def rot(L):
            v = o(L)
            a = f0(L)
            x = v.x
            v.x = f32(v.x * math.cos(a) - v.y * math.sin(a))
            v.y = f32(x * math.sin(a) + v.y * math.cos(a))
            return v
        return E(rot, PVT)
    if name == "lerp" and n in (2, 4):
        xyz = _pv_args(c, args[:-1], line, name)
        ft = fs[-1]

        def lp(L):
            v = o(L)
            x, y, z = xyz(L)
            t = ft(L)
            v.x, v.y, v.z = f32(v.x + (x - v.x) * t), f32(v.y + (y - v.y) * t), f32(v.z + (z - v.z) * t)
            return v
        return E(lp, PVT)
    if name == "cross" and n == 1:
        f0 = fs[0]

        def cr(L):
            a, b = o(L), f0(L)
            B.allocate(64)
            return PVec(a.y * b.z - b.y * a.z, a.z * b.x - b.z * a.x, a.x * b.y - b.x * a.y)
        return E(cr, PVT)
    if name == "array" and n == 0:
        return E(lambda L: array("f", [o(L).x, o(L).y, o(L).z]), ("[]", "float"))
    if name == "setHeading" and n == 1:
        f0 = fs[0]

        def sh(L):
            v = o(L)
            m = math.sqrt(v.x * v.x + v.y * v.y)
            a = f0(L)
            v.x, v.y = f32(m * math.cos(a)), f32(m * math.sin(a))
            return v
        return E(sh, PVT)
    if name == "div" and n == 1 and args[0].t == PVT:
        f0 = fs[0]

        def dv(L):
            v, w = o(L), f0(L)
            v.x, v.y, v.z = f32(_fd(v.x, w.x)), f32(_fd(v.y, w.y)), f32(_fd(v.z, w.z))
            return v
        return E(dv, PVT)
    if name == "mult" and n == 1 and args[0].t == PVT:
        f0 = fs[0]

        def mv(L):
            v, w = o(L), f0(L)
            v.x, v.y, v.z = f32(v.x * w.x), f32(v.y * w.y), f32(v.z * w.z)
            return v
        return E(mv, PVT)
    if name == "random2D" and n == 0:
        def r2(L):
            v = o(L)
            a = rand.random(2 * math.pi)
            v.x, v.y, v.z = f32(math.cos(a)), f32(math.sin(a)), 0.0
            return v
        return E(r2, PVT)
    del rand
    c.err(f"PVector has no method {name}() here", line)


def _fd(a, b):
    try:
        return a / b
    except ZeroDivisionError:
        return math.nan if a == 0 else math.copysign(math.inf, a)


def pvector_static(c, name, args, line) -> E:
    fs = [a.fn for a in args]
    n = len(args)
    B = c.B
    rand = c.api.rand
    if name == "fromAngle" and n in (1, 2):
        f0 = fs[0]

        def fa(L):
            a = f0(L)
            B.allocate(64)
            return PVec(math.cos(a), math.sin(a), 0.0)
        return E(fa, PVT)
    if name == "random2D" and n == 0:
        def r2(L):
            a = rand.random(2 * math.pi)
            B.allocate(64)
            return PVec(math.cos(a), math.sin(a), 0.0)
        return E(r2, PVT)
    if name == "random3D" and n == 0:
        def r3(L):
            # PVector.random3D: angle, then vz in [-1, 1) (Processing 4)
            angle = rand.random(2 * math.pi)
            vz = rand.random2(-1.0, 1.0)
            vx = math.sqrt(1 - vz * vz) * math.cos(angle)
            vy = math.sqrt(1 - vz * vz) * math.sin(angle)
            B.allocate(64)
            return PVec(vx, vy, vz)
        return E(r3, PVT)
    if name == "cross" and n == 2:
        f0, f1 = fs

        def cr(L):
            a, b = f0(L), f1(L)
            B.allocate(64)
            return PVec(a.y * b.z - b.y * a.z, a.z * b.x - b.z * a.x, a.x * b.y - b.x * a.y)
        return E(cr, PVT)
    if name in ("add", "sub") and n == 2:
        f0, f1 = fs
        sg = 1 if name == "add" else -1

        def asb(L):
            a, b = f0(L), f1(L)
            B.allocate(64)
            return PVec(a.x + sg * b.x, a.y + sg * b.y, a.z + sg * b.z)
        return E(asb, PVT)
    if name in ("mult", "div") and n == 2:
        f0, f1 = fs

        def md(L):
            a, k = f0(L), f1(L)
            B.allocate(64)
            if name == "mult":
                return PVec(a.x * k, a.y * k, a.z * k)
            return PVec(_fd(a.x, k), _fd(a.y, k), _fd(a.z, k))
        return E(md, PVT)
    if name == "dist" and n == 2:
        f0, f1 = fs
        return E(lambda L: f32(math.sqrt((f0(L).x - f1(L).x) ** 2 + (f0(L).y - f1(L).y) ** 2 + (f0(L).z - f1(L).z) ** 2)),
                 "float", exact=True)
    if name == "dot" and n == 2:
        f0, f1 = fs
        return E(lambda L: f32(f0(L).x * f1(L).x + f0(L).y * f1(L).y + f0(L).z * f1(L).z), "float", exact=True)
    if name == "angleBetween" and n == 2:
        f0, f1 = fs

        def ab(L):
            a, b = f0(L), f1(L)
            ma = math.sqrt(a.x ** 2 + a.y ** 2 + a.z ** 2)
            mb = math.sqrt(b.x ** 2 + b.y ** 2 + b.z ** 2)
            if not ma or not mb:
                return 0.0
            v = max(-1.0, min(1.0, (a.x * b.x + a.y * b.y + a.z * b.z) / (ma * mb)))
            return f32(math.acos(v))
        return E(ab, "float", exact=True)
    if name == "lerp" and n == 3:
        f0, f1, f2 = fs

        def lp(L):
            a, b, t = f0(L), f1(L), f2(L)
            B.allocate(64)
            return PVec(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t)
        return E(lp, PVT)
    c.err(f"PVector.{name}() is not available here", line)


def list_method(c, fo, lt, name, args, line) -> E:
    o = _this_obj(fo, line, "ArrayList")
    et = norm(lt[2][0]) if len(lt) > 2 and lt[2] else ("C", "Object", ())
    n = len(args)
    B = c.B

    def check_i(i, size, add=False):
        if i < 0 or i > size or (i == size and not add):
            raise PdeError("runtime", f"index {i} is outside the list (size {size})", line)
    if name == "add" and n in (1, 2):
        v = c.store_conv(args[-1], et, line) if et != ("C", "Object", ()) else args[-1]
        fv = v.fn
        if n == 1:
            def add(L):
                lst = o(L).a
                if len(lst) >= MAX_LIST:
                    raise PdeError("memory", "a list grew past 262144 items", line)
                B.allocate(16)
                lst.append(fv(L))
                return True
            return E(add, "boolean")
        fi = args[0].fn

        def ins(L):
            lst = o(L).a
            i = fi(L)
            check_i(i, len(lst), True)
            if len(lst) >= MAX_LIST:
                raise PdeError("memory", "a list grew past 262144 items", line)
            B.allocate(16)
            B.charge(len(lst) >> 4)
            lst.insert(i, fv(L))
        return E(ins, "void")
    if name == "get" and n == 1:
        fi = args[0].fn

        def get(L):
            lst = o(L).a
            i = fi(L)
            check_i(i, len(lst))
            return lst[i]
        return E(get, et, exact=(et == "float"))
    if name == "set" and n == 2:
        fi = args[0].fn
        fv = (c.store_conv(args[1], et, line) if et != ("C", "Object", ()) else args[1]).fn

        def st(L):
            lst = o(L).a
            i = fi(L)
            check_i(i, len(lst))
            old = lst[i]
            lst[i] = fv(L)
            return old
        return E(st, et)
    if name == "size" and n == 0:
        return E(lambda L: len(o(L).a), "int")
    if name == "isEmpty" and n == 0:
        return E(lambda L: not o(L).a, "boolean")
    if name == "clear" and n == 0:
        return E(lambda L: o(L).a.clear(), "void")
    if name == "remove" and n == 1:
        if args[0].t in ("int", "short", "byte", "char") and et != "int":
            fi = args[0].fn

            def rem(L):
                lst = o(L).a
                i = fi(L)
                check_i(i, len(lst))
                B.charge(len(lst) >> 4)
                return lst.pop(i)
            return E(rem, et)
        fv = args[0].fn

        def remo(L):
            lst = o(L).a
            v = fv(L)
            for k, x in enumerate(lst):
                if x is v or x == v:
                    del lst[k]
                    return True
            return False
        return E(remo, "boolean")
    if name in ("contains", "indexOf") and n == 1:
        fv = args[0].fn

        def find(L):
            lst = o(L).a
            v = fv(L)
            B.charge(len(lst) >> 2)
            for k, x in enumerate(lst):
                if x is v or x == v:
                    return True if name == "contains" else k
            return False if name == "contains" else -1
        return E(find, "boolean" if name == "contains" else "int")
    c.err(f"ArrayList has no method {name}() here", line)


# IntList / FloatList / StringList (Processing's own) and HashMap<K, V>
LIST_ET = {"IntList": "int", "FloatList": "float", "StringList": "String"}


def plist_method(c, fo, lt, name, args, line) -> E:
    """IntList, FloatList, StringList: a JList of ints, floats or Strings."""
    kind = lt[1]
    et = LIST_ET[kind]
    o = _this_obj(fo, line, kind)
    n = len(args)
    B = c.B
    rand = c.api.rand

    def conv(a):
        return c.store_conv(a, et, line).fn

    def chk(i, size):
        if i < 0 or i >= size:
            raise PdeError("runtime", f"index {i} is outside the {kind} (size {size})", line)
    if name in ("append", "push") and n == 1:
        fv = conv(args[0])

        def ap(L):
            a = o(L).a
            if len(a) >= MAX_LIST:
                raise PdeError("memory", "a list grew past 262144 items", line)
            B.allocate(16)
            a.append(fv(L))
        return _void(ap)
    if name == "get" and n == 1:
        fi = args[0].fn

        def gt(L):
            a = o(L).a
            i = fi(L)
            chk(i, len(a))
            return a[i]
        return E(gt, et, exact=(et == "float"))
    if name == "set" and n == 2:
        fi, fv = args[0].fn, conv(args[1])

        def st(L):
            a = o(L).a
            i = fi(L)
            if i < 0:
                raise PdeError("runtime", f"index {i} is outside the {kind}", line)
            if i >= len(a):
                if i >= MAX_LIST:
                    raise PdeError("memory", "a list grew past 262144 items", line)
                a.extend([0 if et == "int" else 0.0 if et == "float" else None] * (i + 1 - len(a)))
            a[i] = fv(L)
        return _void(st)
    if name == "size" and n == 0:
        return E(lambda L: len(o(L).a), "int")
    if name == "clear" and n == 0:
        return _void(lambda L: o(L).a.clear())
    if name == "remove" and n == 1:
        fi = args[0].fn

        def rm(L):
            a = o(L).a
            i = fi(L)
            chk(i, len(a))
            B.charge(len(a) >> 4)
            return a.pop(i)
        return E(rm, et)
    if name == "insert" and n == 2:
        fi, fv = args[0].fn, conv(args[1])

        def ins(L):
            a = o(L).a
            i = fi(L)
            if i < 0 or i > len(a):
                raise PdeError("runtime", f"insert() index {i} is outside the {kind}", line)
            a.insert(i, fv(L))
        return _void(ins)
    if name == "hasValue" and n == 1:
        fv = conv(args[0])
        return E(lambda L: fv(L) in o(L).a, "boolean")
    if name == "index" and n == 1:
        fv = conv(args[0])

        def ix(L):
            a = o(L).a
            v = fv(L)
            return a.index(v) if v in a else -1
        return E(ix, "int")
    if et != "String" and name in ("sum", "min", "max") and n == 0:
        def agg(L):
            a = o(L).a
            if not a:
                raise PdeError("runtime", f"{name}() of an empty {kind}", line)
            B.charge(len(a) >> 2)
            if name == "sum":
                if et == "int":
                    return w32(sum(a))
                t = 0.0
                for v in a:
                    t = f32(t + v)
                return t
            return min(a) if name == "min" else max(a)
        return E(agg, et, exact=(et == "float"))
    if et != "String" and name in ("add", "sub", "mult", "div") and n == 2:
        fi, fv = args[0].fn, c.store_conv(args[1], et, line).fn

        def op(L):
            a = o(L).a
            i = fi(L)
            chk(i, len(a))
            v = fv(L)
            if name == "add":
                r = a[i] + v
            elif name == "sub":
                r = a[i] - v
            elif name == "mult":
                r = a[i] * v
            else:
                if et == "int":
                    if v == 0:
                        raise PdeError("runtime", "division by zero", line)
                    q = abs(a[i]) // abs(v)
                    r = q if (a[i] >= 0) == (v > 0) else -q
                else:
                    r = _fd(a[i], v)
            a[i] = w32(r) if et == "int" else f32(r)
        return _void(op)
    if kind == "IntList" and name == "increment" and n == 1:
        fi = args[0].fn

        def inc(L):
            a = o(L).a
            i = fi(L)
            if i < 0:
                raise PdeError("runtime", f"index {i} is outside the IntList", line)
            if i >= len(a):
                a.extend([0] * (i + 1 - len(a)))
            a[i] = w32(a[i] + 1)
        return _void(inc)
    if name in ("sort", "sortReverse", "reverse") and n == 0:
        def so(L):
            a = o(L).a
            B.charge(len(a) * 2)
            if name == "reverse":
                a.reverse()
            else:
                a.sort(key=lambda v: (v is None, v))
                if name == "sortReverse":
                    a.reverse()
        return _void(so)
    if name == "shuffle" and n == 0:
        def sh(L):
            a = o(L).a
            # IntList.shuffle(): Fisher-Yates from the end with the sketch's random()
            i = len(a)
            while i > 1:
                j = int(rand.random(float(i)))
                i -= 1
                a[i], a[j] = a[j], a[i]
        return _void(sh)
    if name in ("array", "values") and n == 0:
        tc = {"int": "i", "float": "f"}.get(et)
        return E(lambda L: array(tc, o(L).a) if tc else list(o(L).a), ("[]", et))
    if name == "copy" and n == 0:
        def cp(L):
            j = JList()
            j.a = list(o(L).a)
            return j
        return E(cp, lt)
    if kind == "StringList" and name in ("upper", "lower") and n == 0:
        def ul(L):
            a = o(L).a
            for i, v in enumerate(a):
                if v is not None:
                    a[i] = v.upper() if name == "upper" else v.lower()
        return _void(ul)
    if name == "join" and n == 1 and kind == "StringList":
        f0 = args[0].fn
        return E(lambda L: f0(L).join("null" if v is None else v for v in o(L).a), "String")
    c.err(f"{kind} has no method {name}() here", line)


class JMap:
    """HashMap<K, V>. Iteration (keySet(), values()) follows java.util.HashMap's bucket order for
    String, Integer, Character and Float keys (the order a Java sketch would print), insertion order
    within a bucket."""
    __slots__ = ("d",)

    def __init__(self) -> None:
        self.d: dict = {}

    @staticmethod
    def _hash(k, kt) -> int:
        if kt == "String":
            h = 0
            for ch in k:
                h = (31 * h + ord(ch)) & 0xFFFFFFFF
        elif kt in ("int", "char", "short", "byte"):
            h = k & 0xFFFFFFFF
        elif kt == "float":
            import struct
            h = struct.unpack("<I", struct.pack("<f", k))[0] if k == k else 0x7FC00000
        elif kt == "boolean":
            h = 1231 if k else 1237
        else:
            return 0
        return (h ^ (h >> 16)) & 0xFFFFFFFF

    def keys(self, kt) -> list:
        ks = list(self.d)
        cap = 16
        while len(ks) > cap * 3 // 4:
            cap *= 2
        order = sorted(range(len(ks)), key=lambda i: (self._hash(ks[i], kt) & (cap - 1), i))
        return [ks[i] for i in order]


def map_method(c, fo, mt, name, args, line) -> E:
    o = _this_obj(fo, line, "HashMap")
    kt = norm(mt[2][0]) if len(mt) > 2 and len(mt[2]) == 2 else ("C", "Object", ())
    vt = norm(mt[2][1]) if len(mt) > 2 and len(mt[2]) == 2 else ("C", "Object", ())
    n = len(args)
    B = c.B

    def kconv(a):
        return c.store_conv(a, kt, line).fn if kt != ("C", "Object", ()) else a.fn
    if name == "put" and n == 2:
        fk, fv = kconv(args[0]), (c.store_conv(args[1], vt, line).fn if vt != ("C", "Object", ()) else args[1].fn)

        def put(L):
            d = o(L).d
            k = fk(L)
            if k not in d and len(d) >= MAX_LIST:
                raise PdeError("memory", "a map grew past 262144 entries", line)
            B.allocate(32)
            old = d.get(k)
            d[k] = fv(L)
            return old
        return E(put, vt)
    if name in ("get", "getOrDefault") and n in (1, 2):
        fk = kconv(args[0])
        if n == 2:
            fdv = c.store_conv(args[1], vt, line).fn if vt != ("C", "Object", ()) else args[1].fn
            return E(lambda L: o(L).d.get(fk(L), fdv(L)) if fk(L) in o(L).d else fdv(L), vt,
                     exact=(vt == "float"))
        return E(lambda L: o(L).d.get(fk(L)), vt, exact=(vt == "float"))
    if name == "containsKey" and n == 1:
        fk = kconv(args[0])
        return E(lambda L: fk(L) in o(L).d, "boolean")
    if name == "containsValue" and n == 1:
        fv = args[0].fn
        return E(lambda L: fv(L) in o(L).d.values(), "boolean")
    if name == "remove" and n == 1:
        fk = kconv(args[0])
        return E(lambda L: o(L).d.pop(fk(L), None), vt)
    if name == "size" and n == 0:
        return E(lambda L: len(o(L).d), "int")
    if name == "isEmpty" and n == 0:
        return E(lambda L: not o(L).d, "boolean")
    if name == "clear" and n == 0:
        return _void(lambda L: o(L).d.clear())
    if name in ("keySet", "values") and n == 0:
        ktn = kt if isinstance(kt, str) else None

        def ks(L):
            m = o(L)
            keys = m.keys(ktn)
            B.allocate(16 * len(keys))
            j = JList()
            j.a = keys if name == "keySet" else [m.d[k] for k in keys]
            return j
        return E(ks, ("C", "ArrayList", ((kt if name == "keySet" else vt),)))
    c.err(f"HashMap has no method {name}() here", line)


def math_static(c, name, args, line) -> E:
    fs = [a.fn for a in args]
    n = len(args)
    ts = [a.t for a in args]
    one = {"sin": _sin, "cos": _cos, "tan": _tan, "asin": _asin, "acos": _acos, "atan": math.atan, "sqrt": _sqrt,
           "exp": _exp, "log": _log, "floor": _floor_safe, "ceil": _ceil_safe, "toRadians": math.radians,
           "toDegrees": math.degrees, "cbrt": lambda v: math.copysign(abs(v) ** (1 / 3), v), "log10": _safe(math.log10),
           "signum": lambda v: (v > 0) - (v < 0) if v == v else v, "rint": lambda v: float(round(v))}
    if name in one and n == 1:
        _need_nums(c, args, line, "Math." + name)
        f0, fn = fs[0], one[name]
        return E(lambda L: float(fn(f0(L))), "double")
    if name == "abs" and n == 1:
        f0 = fs[0]
        t = ts[0] if ts[0] in ("int", "long", "float", "double") else "int"
        return E(lambda L: abs(f0(L)), t)
    if name in ("min", "max") and n == 2:
        _need_nums(c, args, line, "Math." + name)
        a, b, t = c.promote(args[0], args[1], line, name)
        f0, f1 = a.fn, b.fn
        pick = min if name == "min" else max
        return E(lambda L: pick(f0(L), f1(L)), t)
    if name == "pow" and n == 2:
        f0, f1 = fs
        return E(lambda L: _pow(f0(L), f1(L)), "double")
    if name == "atan2" and n == 2:
        f0, f1 = fs
        return E(lambda L: math.atan2(f0(L), f1(L)), "double")
    if name == "hypot" and n == 2:
        f0, f1 = fs
        return E(lambda L: math.hypot(f0(L), f1(L)), "double")
    if name == "round" and n == 1:
        f0 = fs[0]
        if ts[0] == "double":
            def rl(L):
                v = f0(L)
                return d2l(math.floor(v + 0.5)) if v == v else 0
            return E(rl, "long")
        return E(lambda L: _jround(f0(L)), "int")
    if name == "random" and n == 0:
        r = c.api.rand
        return E(lambda L: r.rnd.next_double(), "double")
    c.err(f"Math.{name}() is not available here", line)


def static_call(c, cls, name, args, line) -> E:
    if cls == "Math":
        return math_static(c, name, args, line)
    if cls == "PVector":
        return pvector_static(c, name, args, line)
    if cls == "String" and name == "valueOf" and len(args) == 1:
        return c.conv(args[0], "String", line)
    if cls in ("Integer", "Float", "Double", "Long") and name in ("parseInt", "parseFloat", "valueOf", "parseDouble") \
            and len(args) == 1:
        from_ = h_int if cls in ("Integer", "Long") else h_float
        return from_(c, args, line)
    if cls == "Integer" and name == "toString" and len(args) == 1:
        return c.conv(args[0], "String", line)
    if cls == "Character" and name in ("isLetter", "isDigit", "isWhitespace", "isUpperCase", "isLowerCase") \
            and len(args) == 1:
        f0 = args[0].fn
        test = {"isLetter": str.isalpha, "isDigit": str.isdigit, "isWhitespace": str.isspace,
                "isUpperCase": str.isupper, "isLowerCase": str.islower}[name]
        return E(lambda L: test(chr(f0(L))), "boolean")
    if cls == "PApplet":
        return builtin_call(c, name, args, line)
    c.err(f"{cls}.{name}() is not available here", line)


def method_call(c, n) -> E:
    _, line, on, name, argn = n
    # System.out.println(...) prints nothing here
    if on[0] == "field" and on[2] == ("name", on[2][1], "System") and on[3] in ("out", "err"):
        return h_print(c, [c.expr(a) for a in argn], line)
    obj = c.expr(on)
    args = [c.expr(a) for a in argn]
    ot = obj.t
    if isinstance(ot, tuple) and ot[0] == "static":
        return static_call(c, ot[1], name, args, line)
    if ot == "String":
        return string_method(c, obj.fn, name, args, line)
    if isinstance(ot, tuple) and ot[0] == "[]":
        if name == "clone" and not args:
            f0 = obj.fn
            return E(lambda L: _copy_arr(f0(L)), ot)
        c.err(f"arrays have no method {name}() here (use .length)", line)
    if isinstance(ot, tuple) and ot[0] == "C":
        if ot[1] == "PVector":
            return pvector_method(c, obj.fn, name, args, line)
        if ot[1] in ("ArrayList", "List"):
            return list_method(c, obj.fn, ot, name, args, line)
        if ot[1] in LIST_ET:
            return plist_method(c, obj.fn, ot, name, args, line)
        if ot[1] in ("HashMap", "Map"):
            return map_method(c, obj.fn, ot, name, args, line)
        if ot[1] == "PGraphics":
            return pg_method(c, obj.fn, name, args, line)
        if ot[1] == "PImage":
            return pimage_method(c, obj.fn, name, args, line)
        if ot[1] in c.classes:
            ci = c.classes[ot[1]]
            if name not in ci.methods:
                c.err(f"{ot[1]} has no method {name}()", line)
            sig = c.pick(ci.methods[name], args, line, name)
            if sig is None:
                c.err(f"no {ot[1]}.{name}() takes these arguments", line)
            return c.call_sig(sig, args, line, this=obj.fn)
        if ot[1] == "PFont":
            c.err(f"PFont has no method {name}() here", line)
    c.err(f"{tname(ot)} has no method {name}()", line)
