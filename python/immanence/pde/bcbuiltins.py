"""builtins.py for the VM bytecode: each call becomes a NATIVE with its arguments converted the
way builtins.py passes them (numbers to the drawing API as doubles, PApplet's float maths as floats),
evaluated in builtins.py's order. What the VM does not have raises bytecode.Unsupported.
"""
from __future__ import annotations

from . import bcspec as S
from .builtins import CALLS as PY_CALLS
from .compiler import FLOATS, INTS, NOCONST, NUMS, E, tname
from .errors import PdeError

N = S.NATIVE


def _need_nums(c, args, line, name):
    for a in args:
        if a.t not in NUMS:
            c.err(f"{name}() needs numbers, got {tname(a.t)}", line)


def dbl(c, x) -> list:
    """A number on its way into the drawing API: the value as a double."""
    if x.t == "double":
        return list(x.code)
    if x.t == "float":
        return x.code + [("F2D",)]
    if x.t == "long":
        return x.code + [("L2D",)]
    if x.t in NUMS:
        return x.code + [("I2D",)]
    c.unsup(f"a {tname(x.t)} where the VM wants a number", 0)


def flt(c, x, line) -> list:
    return (c.conv(x, "float", line, explicit=True) if x.t != "float" else x).code


def nat(c, name, code, argc, t="void", flags=0):
    from .bytecode import X
    return X(t, code + [("NATIVE", N[name], argc, flags)])


def X_(t, code, const=NOCONST):
    from .bytecode import X
    return X(t, code, const)


def ignore(c, args, line, ret="void"):
    code: list = []
    for a in args:
        code += a.code + ([] if a.t == "void" else [("POP",)])
    if ret != "void":
        c.unsup("an ignored call with a value", line)
    return X_("void", code)


def py_check(name, c, args, line):
    """Run builtins.py's own handler for its compile errors (arity, types): same verdicts."""
    h = PY_CALLS.get(name)
    if h is None:
        c.err(f"{name}() is not defined", line)
    h(c.py, [E(lambda L: None, a.t, a.const, True) for a in args], line)


def api(native, arities, ret="void", num=True):
    def h(c, name, args, line):
        if len(args) not in arities:
            c.err(f"{name}() does not take {len(args)} argument(s)", line)
        if num:
            _need_nums(c, args, line, name)
        code: list = []
        for a in args:
            code += dbl(c, a)
        return nat(c, native, code, len(args), ret)
    return h


def colour(native, ret="void"):
    def h(c, name, args, line):
        if not 1 <= len(args) <= 4:
            c.err(f"{name}() takes 1 to 4 numbers", line)
        _need_nums(c, args, line, name)
        mask = 0
        code: list = []
        for i, a in enumerate(args):
            if a.t in ("int", "short", "byte", "char"):
                mask |= 1 << i
            code += dbl(c, a)
        return nat(c, native, code, len(args), ret, mask)
    return h


P3D_NAME = "processing.opengl.PGraphics3D"


def h_size(c, name, args, line):
    if len(args) < 2:
        c.err("size() takes a width and a height", line)
    _need_nums(c, args[:2], line, "size")
    flags = 0
    if len(args) >= 3 and args[2].t == "String":
        if args[2].const is NOCONST:
            c.unsup("size() with a renderer that is not a constant", line)
        flags = 1 if args[2].const == P3D_NAME else 0
    return nat(c, "size", dbl(c, args[0]) + dbl(c, args[1]), 2, "void", flags)


def h_get(c, name, args, line):
    if len(args) != 2:
        py_check(name, c, args, line)
        c.unsup("get() of an image on the VM", line)
    return api("get", (2,), "int")(c, name, args, line)


def h_set(c, name, args, line):
    if len(args) == 3 and isinstance(args[2].t, tuple):
        py_check(name, c, args, line)
        c.unsup("set() of an image on the VM", line)
    return api("set", (3,))(c, name, args, line)


def h_rotate(c, name, args, line):
    if len(args) == 4:
        return api("rotate_axis", (4,))(c, name, args, line)
    return api("rotate", (1,))(c, name, args, line)


def h_print(c, name, args, line):
    return ignore(c, args, line)


def h_ignore(c, name, args, line):
    return ignore(c, args, line)


def h_clock(what):
    def h(c, name, args, line):
        c.uses_clock = True
        return nat(c, "clock", [], 0, "int", ("second", "minute", "hour", "day", "month", "year").index(what))
    return h


def h_background(c, name, args, line):
    names = [i for i, a in enumerate(args) if a.t == "String"]
    if not names:
        return colour("background")(c, name, args, line)
    py_check("background", c, args, line)
    k = names[0]
    nums, after = args[:k], args[k + 1:]
    mask = 0
    code: list = []
    for i, a in enumerate(nums):
        if a.t in ("int", "short", "byte", "char"):
            mask |= 1 << i
        code += dbl(c, a)
    code += args[k].code
    for a in after:
        code += dbl(c, a)
    if after:
        mask |= 0x80
    return nat(c, "background_texture", code, len(args), "void", mask)


def h_texture(native):
    def h(c, name, args, line):
        if not args or args[0].t != "String" or len(args) > 2:
            c.err(f'{name}() takes a texture name and an optional number', line)
        _need_nums(c, args[1:], line, name)
        code = list(args[0].code)
        if len(args) == 2:
            code += dbl(c, args[1])
        return nat(c, native, code, len(args), "void", 1 if len(args) == 2 else 0)
    return h


def h_text(c, name, args, line):
    if len(args) not in (3, 4, 5):
        c.err("text() takes a value and a position (or a box)", line)
    a = args[0]
    for x in args[1:]:
        if x.t not in NUMS:
            c.err("text()'s position must be numbers", line)
    if a.t == "String":
        s = list(a.code)
    elif a.t == "char":
        s = a.code + [("TOSTR", S.TS["char"])]
    elif a.t in ("int", "long", "short", "byte"):
        s = a.code + [("TOSTR", S.TS["long" if a.t == "long" else "int"])]
    elif a.t in FLOATS:
        s = a.code + ([("F2D",)] if a.t == "float" else []) + [("TOSTR", S.TS["textfloat"])]
    elif isinstance(a.t, tuple) and a.t[0] == "[]" and a.t[1] == "char":
        s = a.code + [("NATIVE", N["s_fromchars"], 1, 0)]
    elif a.t == "boolean":
        s = a.code + [("TOSTR", S.TS["boolean"])]
    else:
        c.unsup(f"text() of a {tname(a.t)}", line)
    rest = args[1:]
    if len(rest) == 3 and c.p3d:      # text(s, x, y, z) in a P3D sketch: z is evaluated (runtime.text_z)
        return nat(c, "text_z", s + dbl(c, rest[0]) + dbl(c, rest[1]) + dbl(c, rest[2]), 4)
    if len(rest) in (2, 3):           # text(s, x, y[, z]): z is not evaluated
        return nat(c, "text", s + dbl(c, rest[0]) + dbl(c, rest[1]), 3)
    code = s
    for x in rest:
        code += dbl(c, x)
    return nat(c, "text_box", code, 5)


def h_text_width(c, name, args, line):
    if len(args) != 1:
        c.err(f"{name}() does not take {len(args)} argument(s)", line)
    if args[0].t != "String":
        c.unsup("textWidth() of a non-String", line)
    return nat(c, "text_width", list(args[0].code), 1, "float")


def h_create_font(c, name, args, line):
    if not args:
        c.err("createFont() needs a name and a size", line)
    if len(args) >= 2:
        if args[1].t not in NUMS:
            c.unsup("createFont() with a non-number size", line)
        return nat(c, "create_font", dbl(c, args[1]), 1, ("C", "PFont", ()))
    return nat(c, "create_font", c.push_const(12.0, "double"), 1, ("C", "PFont", ()))


def h_text_font(c, name, args, line):
    if not args:
        c.err("textFont() needs a font", line)
    code = list(args[0].code)
    if len(args) >= 2:
        if args[1].t not in NUMS:
            c.unsup("textFont() with a non-number size", line)
        code += dbl(c, args[1])
    return nat(c, "text_font", code, min(2, len(args)))


def h_blend(c, name, args, line):
    return api("blend_mode", (1,))(c, name, args, line)


def h_hue_sat(native):
    def h(c, name, args, line):
        if len(args) != 1 or args[0].t not in NUMS:
            c.unsup(f"{name}() of a non-number", line)
        return nat(c, native, dbl(c, args[0]), 1, "float")
    return h


def h_random(c, name, args, line):
    if len(args) not in (1, 2):
        c.err("random() takes 1 or 2 numbers", line)
    _need_nums(c, args, line, "random")
    code: list = []
    for a in args:
        code += flt(c, a, line) + [("F2D",)]
    return nat(c, "random" if len(args) == 1 else "random2", code, len(args), "float")


def h_noise(c, name, args, line):
    if not 1 <= len(args) <= 3:
        c.err("noise() takes 1 to 3 numbers", line)
    _need_nums(c, args, line, "noise")
    code: list = []
    for a in args:
        code += dbl(c, a)
    return nat(c, "noise", code, len(args), "float")


def math1(native):
    def h(c, name, args, line):
        if len(args) != 1:
            c.err("this takes one number", line)
        _need_nums(c, args, line, "math")
        e = PY_CALLS[name](c.py, [c.pe(a) for a in args], line)
        if e.const is not NOCONST:            # folded by builtins.py itself (the host's libm)
            return c.cx(e.const, "float")
        return nat(c, native, flt(c, args[0], line) + [("F2D",)], 1, "float")
    return h


def fcall(native, counts, ret="float"):
    """PApplet maths declared with float parameters (map, lerp, dist, ...)."""
    def h(c, name, args, line):
        if len(args) not in counts:
            c.err(f"{name}() does not take {len(args)} argument(s)", line)
        _need_nums(c, args, line, name)
        code: list = []
        for a in args:
            code += flt(c, a, line) + [("F2D",)]
        return nat(c, native, code, len(args), ret)
    return h


def h_abs(c, name, args, line):
    if len(args) != 1 or args[0].t not in NUMS:
        c.err("abs() takes one number", line)
    a = args[0]
    if a.t == "long":
        return X_("int", a.code + [("NATIVE", N["m_absl"], 1, 0), ("L2I",)])
    if a.t in INTS:
        return nat(c, "absi", list(a.code), 1, "int")
    code = a.code + ([("D2F",)] if a.t == "double" else [])
    return nat(c, "absf", code + [("F2D",)], 1, "float")


def h_minmax(which):
    def h(c, name, args, line):
        if len(args) == 1:
            a = args[0]
            if not (isinstance(a.t, tuple) and a.t[0] == "[]" and a.t[1] in NUMS):
                c.err(f"{name}() of one thing needs a number array", line)
            if a.t[1] == "int":
                return nat(c, f"{which}_arri", list(a.code), 1, "int")
            if a.t[1] == "float":
                return nat(c, f"{which}_arrf", list(a.code), 1, "float")
            c.unsup(f"{name}() of a {tname(a.t)}", line)
        if len(args) not in (2, 3):
            c.err(f"{name}() takes 2 or 3 numbers", line)
        _need_nums(c, args, line, name)
        t = "float" if any(a.t in FLOATS for a in args) else "int"
        code: list = []
        for a in args:
            x = c.conv(a, t, line) if a.t != t else a
            code += x.code + ([("F2D",)] if t == "float" else [])
        return nat(c, which + ("f" if t == "float" else "i"), code, len(args), t)
    return h


def h_constrain(c, name, args, line):
    if len(args) != 3:
        c.err("constrain() takes 3 numbers", line)
    _need_nums(c, args, line, "constrain")
    t = "float" if any(a.t in FLOATS for a in args) else "int"
    code: list = []
    for a in args:
        x = c.conv(a, t, line) if a.t != t else a
        code += x.code + ([("F2D",)] if t == "float" else [])
    return nat(c, "constrainf" if t == "float" else "constraini", code, 3, t)


def h_floor(native):
    def h(c, name, args, line):
        if len(args) != 1 or args[0].t not in NUMS:
            c.err("this takes one number", line)
        return nat(c, native, flt(c, args[0], line) + [("F2D",)], 1, "int")
    return h


# ---------------------------------------------------------------------- conversions
def h_int(c, name, args, line):
    if len(args) != 1:
        c.err("int() takes one value", line)
    a = args[0]
    if a.t in NUMS:
        return c.conv(a, "int", line, explicit=True)
    if a.t == "boolean":
        return X_("int", list(a.code))
    if a.t == "String":
        return nat(c, "parse_int", list(a.code), 1, "int")
    if isinstance(a.t, tuple) and a.t[0] == "[]" and a.t[1] in NUMS:
        return nat(c, "a_toint", list(a.code), 1, ("[]", "int"), S.KIND[a.t[1]])
    c.err(f"int() of a {tname(a.t)}", line)


def h_float(c, name, args, line):
    if len(args) != 1:
        c.err("float() takes one value", line)
    a = args[0]
    if a.t in NUMS:
        return c.conv(a, "float", line, explicit=True)
    if a.t == "String":
        return nat(c, "parse_float", list(a.code), 1, "float")
    if a.t == "boolean":
        return X_("float", a.code + [("I2F",)])
    c.err(f"float() of a {tname(a.t)}", line)


def h_str(c, name, args, line):
    if len(args) != 1:
        c.err("str() takes one value", line)
    return c.conv(args[0], "String", line)


def h_char(c, name, args, line):
    if len(args) != 1 or args[0].t not in NUMS:
        c.err("char() takes a number", line)
    return c.conv(args[0], "char", line, explicit=True)


def h_byte(c, name, args, line):
    if len(args) != 1 or args[0].t not in NUMS:
        c.err("byte() takes a number", line)
    return c.conv(args[0], "byte", line, explicit=True)


def h_boolean(c, name, args, line):
    if len(args) != 1:
        c.err("boolean() takes one value", line)
    a = args[0]
    if a.t in NUMS:
        z = {"float": [("FCONST", 0.0), ("FCMP", 1)], "double": c.push_const(0.0, "double") + [("DCMP", 1)],
             "long": c.push_const(0, "long") + [("LCMP", 1)]}.get(a.t, [("ICONST", 0), ("ICMP", 1)])
        return X_("boolean", a.code + z)
    if a.t == "String":
        return nat(c, "parse_bool", list(a.code), 1, "boolean")
    if a.t == "boolean":
        return a
    c.err(f"boolean() of a {tname(a.t)}", line)


def h_nf(kind):
    def h(c, name, args, line):
        if len(args) not in (1, 2, 3):
            c.err("nf() takes 1 to 3 arguments", line)
        a = args[0]
        if a.t not in NUMS:
            c.err("nf() formats a number", line)
        if a.t == "long":
            c.unsup("nf() of a long", line)
        for x in args[1:]:
            if x.t not in NUMS:
                c.unsup("nf() with non-number digits", line)
        code: list = []
        for x in args:
            code += dbl(c, x)
        flags = kind | (4 if a.t in INTS else 0) | (8 if a.t == "double" else 0)
        return nat(c, "nf", code, len(args), "String", flags)
    return h


def h_hex(c, name, args, line):
    if not args or args[0].t not in NUMS:
        c.err("hex() takes a number", line)
    if args[0].t not in ("int", "char", "short", "byte") or len(args) > 2:
        c.unsup("hex() of this type", line)
    width = 8 if args[0].t == "int" else 4 if args[0].t == "char" else 2
    code = list(args[0].code)
    if len(args) > 1:
        if args[1].t not in ("int", "short", "byte", "char"):
            c.unsup("hex() digits", line)
        code += args[1].code
    return nat(c, "hex", code, len(args), "String", width)


def h_unhex(c, name, args, line):
    if len(args) != 1 or args[0].t != "String":
        c.unsup("unhex() of a non-String", line)
    return nat(c, "unhex", list(args[0].code), 1, "int")


# ---------------------------------------------------------------------- arrays
def _arr(c, a, line, name):
    if not (isinstance(a.t, tuple) and a.t[0] == "[]"):
        c.err(f"{name}() needs an array", line)
    return a.t


def h_append(c, name, args, line):
    if len(args) != 2:
        c.err("append() takes an array and a value", line)
    t = _arr(c, args[0], line, "append")
    v = c.store_conv(args[1], t[1], line)
    from .bytecode import kind_of
    return nat(c, "a_append", args[0].code + v.code, 2, t, kind_of(t[1]))


def _arr_simple(native, counts):
    def h(c, name, args, line):
        if len(args) not in counts:
            c.unsup(f"{name}() with {len(args)} arguments", line)
        t = _arr(c, args[0], line, name)
        from .bytecode import kind_of
        code = list(args[0].code)
        for a in args[1:]:
            if a.t not in ("int", "short", "byte", "char"):
                c.unsup(f"{name}() with a {tname(a.t)}", line)
            code += a.code
        return nat(c, native, code, len(args), t, kind_of(t[1]))
    return h


def h_concat(c, name, args, line):
    t = _arr(c, args[0], line, "concat")
    if len(args) != 2 or args[1].t != t:
        c.unsup("concat() of different arrays", line)
    from .bytecode import kind_of
    return nat(c, "a_concat", args[0].code + args[1].code, 2, t, kind_of(t[1]))


def h_sort(c, name, args, line):
    t = _arr(c, args[0], line, "sort")
    if len(args) != 1 or t[1] not in NUMS + ("String",):
        c.unsup("sort() of this array", line)
    from .bytecode import kind_of
    return nat(c, "a_sort", list(args[0].code), 1, t, kind_of(t[1]))


def h_arraycopy(c, name, args, line):
    if len(args) not in (2, 3, 5):
        c.err("arrayCopy() takes 2, 3 or 5 arguments", line)
    code: list = []
    for i, a in enumerate(args):
        if i in ((0, 1) if len(args) != 5 else (0, 2)):
            if not (isinstance(a.t, tuple) and a.t[0] == "[]"):
                c.unsup("arrayCopy() of a non-array", line)
        elif a.t not in ("int", "short", "byte", "char"):
            c.unsup("arrayCopy() with a non-int count", line)
        code += a.code
    return nat(c, "a_copy", code, len(args), "void")


def h_trim(c, name, args, line):
    if len(args) != 1 or args[0].t != "String":
        c.unsup("trim() of a non-String", line)
    return nat(c, "ptrim", list(args[0].code), 1, "String")


def h_unsup(c, name, args, line):
    py_check(name, c, args, line)
    c.unsup(f"{name}() on the VM", line)


CALLS = {
    "size": h_size, "smooth": api("smooth", (0, 1)), "noSmooth": api("no_smooth", (0,)),
    "frameRate": h_ignore, "noLoop": api("no_loop", (0,)), "loop": api("loop", (0,)),
    "redraw": api("redraw", (0,)), "exit": api("exit", (0,)), "delay": h_ignore, "pixelDensity": h_ignore,
    "cursor": h_ignore, "noCursor": h_ignore, "millis": api("millis", (0,), "int"), "println": h_print,
    "print": h_print, "printArray": h_print, "second": h_clock("second"), "minute": h_clock("minute"),
    "hour": h_clock("hour"), "day": h_clock("day"), "month": h_clock("month"), "year": h_clock("year"),
    "textMode": h_ignore, "hint": api("hint", (1,)), "blendMode": h_blend,
    "background": h_background, "texture": h_texture("texture"), "strokeTexture": h_texture("stroke_texture"),
    "noTexture": api("no_texture", (0,)), "noStrokeTexture": api("no_stroke_texture", (0,)),
    "textureSeed": api("texture_seed", (1,)), "fill": colour("fill"), "stroke": colour("stroke"),
    "color": colour("color", "int"), "noFill": api("no_fill", (0,)), "noStroke": api("no_stroke", (0,)),
    "clear": api("clear", (0,)), "strokeWeight": api("stroke_weight", (1,)), "strokeCap": api("stroke_cap", (1,)),
    "strokeJoin": api("stroke_join", (1,)), "colorMode": api("color_mode", (1, 2, 4, 5)),
    "red": api("red", (1,), "float"), "green": api("green", (1,), "float"), "blue": api("blue", (1,), "float"),
    "alpha": api("alpha", (1,), "float"), "brightness": api("brightness", (1,), "float"),
    "hue": h_hue_sat("hue"), "saturation": h_hue_sat("saturation"), "lerpColor": api("lerp_color", (3,), "int"),
    "tint": h_ignore, "noTint": h_ignore,
    "point": api("point", (2, 3)), "line": api("line", (4, 6)), "rect": api("rect", (4, 5, 8)),
    "square": api("square", (3,)), "ellipse": api("ellipse", (4,)), "circle": api("circle", (3,)),
    "arc": api("arc", (6, 7)), "triangle": api("triangle", (6,)), "quad": api("quad", (8,)),
    "bezier": api("bezier", (8,)), "curve": api("curve", (8,)), "beginShape": api("begin_shape", (0, 1)),
    "endShape": api("end_shape", (0, 1)), "vertex": api("vertex", (2, 3, 4)),
    "bezierVertex": api("bezier_vertex", (6, 9)), "quadraticVertex": api("quadratic_vertex", (4, 6)),
    "curveVertex": api("curve_vertex", (2, 3)), "beginContour": api("begin_contour", (0,)),
    "endContour": api("end_contour", (0,)), "rectMode": api("rect_mode", (1,)),
    "ellipseMode": api("ellipse_mode", (1,)), "curveTightness": api("curve_tightness", (1,)),
    "bezierDetail": h_ignore, "curveDetail": h_ignore,
    "bezierPoint": api("bezier_point", (5,), "float"), "bezierTangent": api("bezier_tangent", (5,), "float"),
    "curvePoint": api("curve_point", (5,), "float"),
    "pushMatrix": api("push_matrix", (0,)), "popMatrix": api("pop_matrix", (0,)), "push": api("push", (0,)),
    "pop": api("pop", (0,)), "pushStyle": api("push_style", (0,)), "popStyle": api("pop_style", (0,)),
    "translate": api("translate", (2, 3)), "rotate": h_rotate, "rotateZ": api("rotate", (1,)),
    "scale": api("scale", (1, 2, 3)), "shearX": api("shear_x", (1,)), "shearY": api("shear_y", (1,)),
    "resetMatrix": api("reset_matrix", (0,)), "screenX": api("screen_x", (2, 3), "float"),
    "screenY": api("screen_y", (2, 3), "float"),
    "text": h_text, "textSize": api("text_size", (1,)), "textAlign": api("text_align", (1, 2)),
    "textLeading": api("text_leading", (1,)), "textWidth": h_text_width,
    "textAscent": api("text_ascent", (0,), "float"), "textDescent": api("text_descent", (0,), "float"),
    "createFont": h_create_font, "loadFont": h_create_font, "textFont": h_text_font,
    "loadPixels": api("load_pixels", (0,)), "updatePixels": api("update_pixels", (0, 4)),
    "get": h_get, "set": h_set,
    "random": h_random, "randomSeed": api("random_seed", (1,)), "randomGaussian": api("random_gaussian", (0,), "float"),
    "noise": h_noise, "noiseSeed": api("noise_seed", (1,)), "noiseDetail": api("noise_detail", (1, 2)),
    "sin": math1("sin"), "cos": math1("cos"), "tan": math1("tan"), "asin": math1("asin"), "acos": math1("acos"),
    "atan": math1("atan"), "sqrt": math1("sqrt"), "exp": math1("exp"), "log": math1("log"),
    "degrees": math1("degrees"), "radians": math1("radians"), "atan2": fcall("atan2", (2,)), "pow": fcall("pow", (2,)),
    "abs": h_abs, "min": h_minmax("min"), "max": h_minmax("max"), "constrain": h_constrain,
    "map": fcall("map", (5,)), "lerp": fcall("lerp", (3,)), "norm": fcall("norm", (3,)), "dist": fcall("dist", (4, 6)),
    "mag": fcall("mag", (2, 3)), "sq": fcall("sq", (1,)),
    "floor": h_floor("floor"), "ceil": h_floor("ceil"), "round": h_floor("round"),
    "int": h_int, "float": h_float, "str": h_str, "char": h_char, "byte": h_byte, "boolean": h_boolean,
    "nf": h_nf(0), "nfs": h_nf(1), "nfp": h_nf(2), "nfc": h_nf(3), "hex": h_hex, "unhex": h_unhex,
    "append": h_append, "shorten": _arr_simple("a_shorten", (1,)), "expand": _arr_simple("a_expand", (1, 2)),
    "subset": _arr_simple("a_subset", (2, 3)), "concat": h_concat, "reverse": _arr_simple("a_reverse", (1,)),
    "sort": h_sort, "arrayCopy": h_arraycopy, "arraycopy": h_arraycopy, "trim": h_trim,
    "join": h_unsup, "split": h_unsup, "splitTokens": h_unsup,
    # P3D (three.py)
    "rotateX": api("rotate_x", (1,)), "rotateY": api("rotate_y", (1,)), "box": api("box", (1, 3)),
    "sphere": api("sphere", (1,)), "sphereDetail": api("sphere_detail", (1, 2)), "camera": api("camera", (0, 9)),
    "perspective": api("perspective", (0, 4)), "ortho": api("ortho", (0, 4, 6)), "frustum": api("frustum", (6,)),
    "lights": api("lights", (0,)), "noLights": api("no_lights", (0,)),
    "ambientLight": api("ambient_light", (3, 6)), "directionalLight": api("directional_light", (6,)),
    "pointLight": api("point_light", (6,)), "spotLight": api("spot_light", (11,)),
    "lightFalloff": api("light_falloff", (3,)), "lightSpecular": api("light_specular", (3,)),
    "ambient": colour("ambient"), "specular": colour("specular"), "emissive": colour("emissive"),
    "shininess": api("shininess", (1,)), "normal": api("normal", (3,)), "screenZ": api("screen_z", (2, 3), "float"),
    "applyMatrix": api("apply_matrix", (6, 16)), "filter": api("filter", (1, 2)),
}


def builtin_call(c, name, args, line):
    h = CALLS.get(name)
    if h is None:
        py_check(name, c, args, line)          # the interpreter's own error for this name
        c.unsup(f"{name}() on the VM", line)
    return h(c, name, args, line)


def new_builtin(c, t, args, line):
    name = t[1]
    if name == "String":
        if len(args) == 1 and args[0].t == ("[]", "char"):
            return nat(c, "s_fromchars", list(args[0].code), 1, "String")
        if not args:
            return c.cx("", "String")
        return c.conv(args[0], "String", line)
    if name in ("PVector", "ArrayList", "List", "IntList", "FloatList", "StringList", "HashMap", "Map"):
        c.unsup(f"new {name}() on the VM", line)
    c.err(f"new {name}() is not available here", line)


# ---------------------------------------------------------------------- methods
def string_method(c, obj, name, args, line):
    n = len(args)
    ts = [a.t for a in args]
    recv = obj.code + [("NULLCHK",)]

    def ints(i):
        if ts[i] not in ("int", "short", "byte", "char"):
            c.unsup(f"String.{name}() with a {tname(ts[i])}", line)
        return args[i].code
    if name == "length" and n == 0:
        return nat(c, "s_length", recv, 1, "int")
    if name == "charAt" and n == 1:
        return nat(c, "s_charat", recv + ints(0), 2, "char")
    if name == "substring" and n in (1, 2):
        code = recv + ints(0) + (ints(1) if n == 2 else [])
        return nat(c, "s_substring", code, 1 + n, "String")
    if name in ("indexOf", "lastIndexOf") and n in (1, 2):
        is_char = ts[0] in INTS
        if not is_char and ts[0] != "String":
            c.unsup(f"String.{name}() of a {tname(ts[0])}", line)
        if ts[0] == "long":
            c.unsup(f"String.{name}() of a long", line)
        code = recv + args[0].code + (ints(1) if n == 2 else [])
        return nat(c, "s_indexof" if name == "indexOf" else "s_lastindexof", code, 1 + n, "int", 1 if is_char else 0)
    if name in ("toUpperCase", "toLowerCase", "trim", "isEmpty", "hashCode") and n == 0:
        nm = {"toUpperCase": "s_upper", "toLowerCase": "s_lower", "trim": "s_trim", "isEmpty": "s_isempty",
              "hashCode": "s_hashcode"}[name]
        return nat(c, nm, recv, 1, {"s_isempty": "boolean", "s_hashcode": "int"}.get(nm, "String"))
    if name == "equals" and n == 1:
        if ts[0] != "String":
            return c.cx(False, "boolean")     # builtins.py: `t0 == "String" and ...` evaluates nothing
        return nat(c, "s_equals", recv + args[0].code, 2, "boolean")
    if name == "equalsIgnoreCase" and n == 1:
        if ts[0] != "String":
            c.unsup("equalsIgnoreCase() of a non-String", line)
        return nat(c, "s_equalsic", args[0].code + recv, 2, "boolean")      # the argument first
    if name in ("startsWith", "endsWith", "contains") and n == 1:
        if ts[0] != "String":
            c.unsup(f"String.{name}() of a non-String", line)
        nm = {"startsWith": "s_startswith", "endsWith": "s_endswith", "contains": "s_contains"}[name]
        return nat(c, nm, recv + args[0].code + [("NULLCHK",)], 2, "boolean")
    if name == "compareTo" and n == 1:
        if ts[0] != "String":
            c.unsup("compareTo() of a non-String", line)
        return nat(c, "s_compareto", recv + args[0].code + [("NULLCHK",)], 2, "int")
    if name == "concat" and n == 1:
        from .bytecode import X
        return c.binop("+", X("String", recv), args[0], line)
    if name == "replace" and n == 2:
        is_char = ts[0] == "char"
        if is_char and ts[1] != "char" or not is_char and (ts[0] != "String" or ts[1] != "String"):
            c.unsup("String.replace() of these types", line)
        code = recv + args[0].code + ([] if is_char else [("NULLCHK",)]) + args[1].code + ([] if is_char else [("NULLCHK",)])
        return nat(c, "s_replace", code, 3, "String", 1 if is_char else 0)
    if name == "toCharArray" and n == 0:
        return nat(c, "s_tochars", recv, 1, ("[]", "char"))
    if name == "repeat" and n == 1:
        return nat(c, "s_repeat", recv + ints(0), 2, "String")
    if name == "split":
        c.unsup("String.split() on the VM", line)
    c.err(f"String has no method {name}() here", line)


_MATH_ONE = {"sin": "m_sin", "cos": "m_cos", "tan": "m_tan", "asin": "m_asin", "acos": "m_acos", "atan": "m_atan",
             "sqrt": "m_sqrt", "exp": "m_exp", "log": "m_log", "floor": "m_floor", "ceil": "m_ceil",
             "toRadians": "m_toradians", "toDegrees": "m_todegrees", "cbrt": "m_cbrt", "log10": "m_log10",
             "signum": "m_signum", "rint": "m_rint"}


def math_static(c, name, args, line):
    n = len(args)
    ts = [a.t for a in args]
    if name in _MATH_ONE and n == 1:
        _need_nums(c, args, line, "Math." + name)
        return nat(c, _MATH_ONE[name], dbl(c, args[0]), 1, "double")
    if name == "abs" and n == 1:
        t = ts[0] if ts[0] in ("int", "long", "float", "double") else "int"
        a = args[0]
        if t == "int":
            return nat(c, "absi", list(a.code), 1, "int")
        if t == "long":
            return nat(c, "m_absl", list(a.code), 1, "long")
        if t == "float":
            return nat(c, "absf", a.code + [("F2D",)], 1, "float")
        return nat(c, "m_absd", list(a.code), 1, "double")
    if name in ("min", "max") and n == 2:
        _need_nums(c, args, line, "Math." + name)
        a, b, t = c.promote(args[0], args[1], line, name)
        if t == "int":
            return nat(c, name + "i", a.code + b.code, 2, "int")
        if t == "float":
            return nat(c, name + "f", a.code + [("F2D",)] + b.code + [("F2D",)], 2, "float")
        return nat(c, "m_" + name + ("l" if t == "long" else "d"), a.code + b.code, 2, t)
    if name in ("pow", "atan2", "hypot") and n == 2:
        _need_nums(c, args, line, "Math." + name)
        return nat(c, "m_" + name, dbl(c, args[0]) + dbl(c, args[1]), 2, "double")
    if name == "round" and n == 1:
        if ts[0] == "double":
            return nat(c, "m_roundd", list(args[0].code), 1, "long")
        if ts[0] != "float":
            c.unsup("Math.round() of an int", line)
        return nat(c, "round", args[0].code + [("F2D",)], 1, "int")
    if name == "random" and n == 0:
        return nat(c, "m_random", [], 0, "double")
    c.err(f"Math.{name}() is not available here", line)


def static_call(c, cls, name, args, line):
    if cls == "Math":
        return math_static(c, name, args, line)
    if cls == "String" and name == "valueOf" and len(args) == 1:
        return c.conv(args[0], "String", line)
    if cls in ("Integer", "Float", "Double", "Long") and name in ("parseInt", "parseFloat", "valueOf", "parseDouble") \
            and len(args) == 1:
        return (h_int if cls in ("Integer", "Long") else h_float)(c, name, args, line)
    if cls == "Integer" and name == "toString" and len(args) == 1:
        return c.conv(args[0], "String", line)
    if cls == "Character" and name in ("isLetter", "isDigit", "isWhitespace", "isUpperCase", "isLowerCase") \
            and len(args) == 1:
        which = ("isLetter", "isDigit", "isWhitespace", "isUpperCase", "isLowerCase").index(name)
        if args[0].t not in ("int", "short", "byte", "char"):
            c.unsup(f"Character.{name}() of a {tname(args[0].t)}", line)
        return nat(c, "char_test", list(args[0].code), 1, "boolean", which)
    if cls == "PApplet":
        return builtin_call(c, name, args, line)
    if cls == "PVector":
        c.unsup("PVector on the VM", line)
    c.err(f"{cls}.{name}() is not available here", line)


def method_call(c, n):
    _, line, on, name, argn = n
    if on[0] == "field" and on[2] == ("name", on[2][1], "System") and on[3] in ("out", "err"):
        return h_print(c, name, [c.expr(a) for a in argn], line)
    obj = c.expr(on)
    args = [c.expr(a) for a in argn]
    ot = obj.t
    if isinstance(ot, tuple) and ot[0] == "static":
        return static_call(c, ot[1], name, args, line)
    if ot == "String":
        return string_method(c, obj, name, args, line)
    if isinstance(ot, tuple) and ot[0] == "[]":
        if name == "clone" and not args:
            from .bytecode import kind_of
            return nat(c, "a_clone", list(obj.code), 1, ot, kind_of(ot[1]))
        c.err(f"arrays have no method {name}() here (use .length)", line)
    if isinstance(ot, tuple) and ot[0] == "C":
        if ot[1] in ("PVector", "ArrayList", "List"):
            c.unsup(f"{ot[1]} on the VM", line)
        if ot[1] in c.classes:
            ci = c.classes[ot[1]]
            if name not in ci.methods:
                c.err(f"{ot[1]} has no method {name}()", line)
            from .compiler import Compiler
            f = Compiler.pick(c, ci.methods[name], args, line, name)
            if f is None:
                c.err(f"no {ot[1]}.{name}() takes these arguments", line)
            return c.call(f, args, line, this=obj.code)
        if ot[1] == "PFont":
            c.err(f"PFont has no method {name}() here", line)
    c.err(f"{tname(ot)} has no method {name}()", line)


del PdeError
