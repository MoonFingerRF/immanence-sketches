"""The bytecode's instruction set and native table -- ONE list both sides are built from.

The compiler turns a sketch into this bytecode (bytecode.py); the C++ VM (src/) executes it.
`python3 -m immanence.pde.bcspec > src/pde_spec.h` regenerates the C++ header; a test checks the
copy in src/ is current. The format: docs/BYTECODE.md.

Operand formats: "" none, "i" int32, "f" float32 bits, "H" uint16, "B" uint8, "b" int8.
Stack effects are in 8-byte slots; CALL/CALLM/NATIVE/NEWMULTI/RETV are special-cased by both sides.
"""
from __future__ import annotations

VERSION = 1
MAGIC = b"PDEB"

# name, operand format, pops, pushes
OPS = [
    ("NOP", "", 0, 0),
    ("ICONST", "i", 0, 1), ("FCONST", "f", 0, 1), ("LCONST", "H", 0, 1), ("DCONST", "H", 0, 1),
    ("SCONST", "H", 0, 1), ("NULL", "", 0, 1),
    ("LOAD", "H", 0, 1), ("STORE", "H", 1, 0), ("GLOAD", "H", 0, 1), ("GSTORE", "H", 1, 0),
    ("DUP", "", 1, 2), ("DUP2", "", 2, 4), ("DUP_X1", "", 2, 3), ("DUP_X2", "", 3, 4), ("POP", "", 1, 0),
    ("SWAP", "", 2, 2),
    ("IADD", "", 2, 1), ("ISUB", "", 2, 1), ("IMUL", "", 2, 1), ("IDIV", "", 2, 1), ("IREM", "", 2, 1),
    ("INEG", "", 1, 1), ("IAND", "", 2, 1), ("IOR", "", 2, 1), ("IXOR", "", 2, 1), ("ISHL", "", 2, 1),
    ("ISHR", "", 2, 1), ("IUSHR", "", 2, 1), ("INOT", "", 1, 1),
    ("LADD", "", 2, 1), ("LSUB", "", 2, 1), ("LMUL", "", 2, 1), ("LDIV", "", 2, 1), ("LREM", "", 2, 1),
    ("LNEG", "", 1, 1), ("LAND", "", 2, 1), ("LOR", "", 2, 1), ("LXOR", "", 2, 1), ("LSHL", "", 2, 1),
    ("LSHR", "", 2, 1), ("LUSHR", "", 2, 1), ("LNOT", "", 1, 1),
    ("FADD", "", 2, 1), ("FSUB", "", 2, 1), ("FMUL", "", 2, 1), ("FDIV", "", 2, 1), ("FREM", "", 2, 1),
    ("FNEG", "", 1, 1),
    ("DADD", "", 2, 1), ("DSUB", "", 2, 1), ("DMUL", "", 2, 1), ("DDIV", "", 2, 1), ("DREM", "", 2, 1),
    ("DNEG", "", 1, 1),
    ("BNOT", "", 1, 1),
    ("I2L", "", 1, 1), ("I2F", "", 1, 1), ("I2D", "", 1, 1), ("L2I", "", 1, 1), ("L2F", "", 1, 1),
    ("L2D", "", 1, 1), ("F2I", "", 1, 1), ("F2L", "", 1, 1), ("F2D", "", 1, 1), ("D2I", "", 1, 1),
    ("D2L", "", 1, 1), ("D2F", "", 1, 1), ("I2B", "", 1, 1), ("I2S", "", 1, 1), ("I2C", "", 1, 1),
    ("ICMP", "B", 2, 1), ("LCMP", "B", 2, 1), ("FCMP", "B", 2, 1), ("DCMP", "B", 2, 1),
    ("ACMP", "B", 2, 1), ("SCMP", "B", 2, 1),
    ("JMP", "H", 0, 0), ("JZ", "H", 1, 0), ("JNZ", "H", 1, 0), ("LOOP", "H", 0, 0),
    ("CALL", "H", 0, 0), ("CALLM", "H", 0, 0), ("RET", "", 0, 0), ("RETV", "", 1, 0),
    ("NEWOBJ", "H", 0, 1), ("GETF", "H", 1, 1), ("PUTF", "H", 2, 0),
    ("NEWARR", "B", 1, 1), ("NEWMULTI", "BB", 0, 1), ("ALOAD", "B", 2, 1), ("ASTORE", "B", 3, 0),
    ("ALEN", "", 1, 1),
    ("TOSTR", "B", 1, 1), ("SCAT", "", 2, 1),
    ("NATIVE", "HBB", 0, 0),
    ("IINC", "Hb", 0, 0),
    ("FAULT", "B", 0, 0),
    ("PIXELS", "", 0, 1), ("FRAMECOUNT", "", 0, 1),
    ("NULLCHK", "", 1, 1),
]
OP = {name: i for i, (name, *_rest) in enumerate(OPS)}
CMP = {"==": 0, "!=": 1, "<": 2, ">": 3, "<=": 4, ">=": 5}

# array element kinds (NEWARR/ALOAD/ASTORE operand); bool arrays hold bytes
KIND = {"boolean": 0, "byte": 1, "short": 2, "char": 3, "int": 4, "long": 5, "float": 6, "double": 7, "ref": 8}
# TOSTR kinds
TS = {"int": 0, "long": 1, "char": 2, "boolean": 3, "float": 4, "double": 5, "String": 6, "textfloat": 7}
# FAULT kinds (as the VM reports them)
FAULTS = ("none", "runtime", "memory", "size", "budget", "deadline", "depth", "bad_program", "unsupported")

# Natives: name, return ("" void, "I" int/boolean/char, "L" long, "F" float, "D" double, "R" reference).
# Numbers reach API natives as doubles (Python passes int or float; an int is exact in a double).
NATIVES = [
    # structure
    ("size", ""), ("smooth", ""), ("no_smooth", ""), ("no_loop", ""), ("loop", ""), ("redraw", ""),
    ("exit", ""), ("millis", "I"), ("clock", "I"),
    # colour (colour natives: flags = the int-argument bitmask)
    ("background", ""), ("fill", ""), ("stroke", ""), ("color", "I"), ("no_fill", ""), ("no_stroke", ""),
    ("clear", ""), ("stroke_weight", ""), ("stroke_cap", ""), ("stroke_join", ""), ("color_mode", ""),
    ("red", "F"), ("green", "F"), ("blue", "F"), ("alpha", "F"), ("brightness", "F"), ("hue", "F"),
    ("saturation", "F"), ("lerp_color", "I"),
    # textures (flags bit 0: has the optional number; background_texture: flags = ints mask, bit 7 = has extra)
    ("texture", ""), ("stroke_texture", ""), ("no_texture", ""), ("no_stroke_texture", ""),
    ("texture_seed", ""), ("background_texture", ""),
    # shapes
    ("point", ""), ("line", ""), ("rect", ""), ("square", ""), ("ellipse", ""), ("circle", ""), ("arc", ""),
    ("triangle", ""), ("quad", ""), ("bezier", ""), ("curve", ""), ("begin_shape", ""), ("end_shape", ""),
    ("vertex", ""), ("bezier_vertex", ""), ("quadratic_vertex", ""), ("curve_vertex", ""),
    ("begin_contour", ""), ("end_contour", ""), ("rect_mode", ""), ("ellipse_mode", ""),
    ("curve_tightness", ""), ("bezier_point", "F"), ("bezier_tangent", "F"), ("curve_point", "F"),
    # matrix
    ("push_matrix", ""), ("pop_matrix", ""), ("push", ""), ("pop", ""), ("push_style", ""), ("pop_style", ""),
    ("translate", ""), ("rotate", ""), ("scale", ""), ("shear_x", ""), ("shear_y", ""), ("reset_matrix", ""),
    ("screen_x", "F"), ("screen_y", "F"),
    # text
    ("text", ""), ("text_box", ""), ("text_size", ""), ("text_align", ""), ("text_leading", ""),
    ("text_width", "F"), ("text_ascent", "F"), ("text_descent", "F"), ("create_font", "R"), ("text_font", ""),
    # pixels
    ("load_pixels", ""), ("update_pixels", ""), ("get", "I"), ("set", ""),
    # randomness
    ("random", "F"), ("random2", "F"), ("random_seed", ""), ("random_gaussian", "F"), ("noise", "F"),
    ("noise_seed", ""), ("noise_detail", ""),
    # maths (F arguments are floats, D doubles)
    ("sin", "F"), ("cos", "F"), ("tan", "F"), ("asin", "F"), ("acos", "F"), ("atan", "F"), ("sqrt", "F"),
    ("exp", "F"), ("log", "F"), ("degrees", "F"), ("radians", "F"), ("atan2", "F"), ("pow", "F"),
    ("absi", "I"), ("absf", "F"), ("mini", "I"), ("minf", "F"), ("maxi", "I"), ("maxf", "F"),
    ("min_arri", "I"), ("min_arrf", "F"), ("max_arri", "I"), ("max_arrf", "F"),
    ("constraini", "I"), ("constrainf", "F"), ("map", "F"), ("lerp", "F"), ("norm", "F"), ("dist", "F"),
    ("mag", "F"), ("sq", "F"), ("floor", "I"), ("ceil", "I"), ("round", "I"),
    ("m_sin", "D"), ("m_cos", "D"), ("m_tan", "D"), ("m_asin", "D"), ("m_acos", "D"), ("m_atan", "D"),
    ("m_sqrt", "D"), ("m_exp", "D"), ("m_log", "D"), ("m_floor", "D"), ("m_ceil", "D"), ("m_toradians", "D"),
    ("m_todegrees", "D"), ("m_cbrt", "D"), ("m_log10", "D"), ("m_signum", "D"), ("m_rint", "D"),
    ("m_pow", "D"), ("m_atan2", "D"), ("m_hypot", "D"), ("m_roundd", "L"), ("m_random", "D"),
    ("m_absl", "L"), ("m_absd", "D"), ("m_minl", "L"), ("m_maxl", "L"), ("m_mind", "D"), ("m_maxd", "D"),
    # conversions and strings
    ("parse_int", "I"), ("parse_float", "F"), ("parse_bool", "I"), ("nf", "R"), ("hex", "R"), ("unhex", "I"),
    ("s_length", "I"), ("s_charat", "I"), ("s_substring", "R"), ("s_indexof", "I"), ("s_lastindexof", "I"),
    ("s_upper", "R"), ("s_lower", "R"), ("s_trim", "R"), ("s_isempty", "I"), ("s_equals", "I"),
    ("s_equalsic", "I"), ("s_startswith", "I"), ("s_endswith", "I"), ("s_contains", "I"),
    ("s_compareto", "I"), ("s_replace", "R"), ("s_hashcode", "I"), ("s_repeat", "R"), ("s_fromchars", "R"),
    ("s_tochars", "R"), ("ptrim", "R"), ("char_test", "I"),
    # arrays (flags: element kind)
    ("a_append", "R"), ("a_shorten", "R"), ("a_expand", "R"), ("a_subset", "R"), ("a_concat", "R"),
    ("a_reverse", "R"), ("a_sort", "R"), ("a_copy", ""), ("a_clone", "R"), ("a_toint", "R"),
    # P3D (three.py; appended: older programs keep their numbers). size() carries flags bit 0 = P3D.
    ("rotate_x", ""), ("rotate_y", ""), ("rotate_axis", ""), ("apply_matrix", ""), ("camera", ""),
    ("perspective", ""), ("ortho", ""), ("frustum", ""), ("screen_z", "F"), ("lights", ""), ("no_lights", ""),
    ("ambient_light", ""), ("directional_light", ""), ("point_light", ""), ("spot_light", ""),
    ("light_falloff", ""), ("light_specular", ""), ("ambient", ""), ("specular", ""), ("emissive", ""),
    ("shininess", ""), ("normal", ""), ("hint", ""), ("box", ""), ("sphere_detail", ""), ("sphere", ""),
    ("text_z", ""),
    # blendMode() and filter() on the grey canvas
    ("blend_mode", ""), ("filter", ""),
]
NATIVE = {name: i for i, (name, _r) in enumerate(NATIVES)}


def c_header() -> str:
    out = ["// GENERATED by `python3 -m immanence.pde.bcspec` (python/immanence/pde/bcspec.py) -- do not edit.",
           "#pragma once", "#include <cstdint>", "namespace pde {", f"constexpr int BC_VERSION = {VERSION};",
           "enum Op : uint8_t {"]
    out += [f"  OP_{n} = {i}," for i, (n, *_r) in enumerate(OPS)]
    out += ["  OP_COUNT_", "};", "struct OpInfo { const char *fmt; int8_t pops, pushes; };",
            "static const OpInfo OP_INFO[] = {"]
    out += [f'  {{"{f}", {p}, {q}}},  // {n}' for n, f, p, q in OPS]
    out += ["};", "enum Native : uint16_t {"]
    out += [f"  N_{n} = {i}," for i, (n, _r) in enumerate(NATIVES)]
    out += ["  N_COUNT_", "};", "// return kind per native: 0 void, 'I', 'L', 'F', 'D', 'R'", "static const char NATIVE_RET[] = {"]
    out += [f"  {repr(r) if r else 0},  // {n}" for n, r in NATIVES]
    out += ["};"]
    out += [f"constexpr uint8_t K_{k.upper()} = {v};" for k, v in KIND.items()]
    out += [f"constexpr uint8_t TS_{k.upper()} = {v};" for k, v in TS.items()]
    out += [f"constexpr uint8_t F_{k.upper()} = {i};" for i, k in enumerate(FAULTS)]
    out += ["}  // namespace pde", ""]
    return "\n".join(out)


if __name__ == "__main__":
    print(c_header(), end="")
