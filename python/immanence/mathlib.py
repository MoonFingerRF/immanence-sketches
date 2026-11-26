"""BitMapMathLib, the prelude: small helpers a sketch can call without writing them out.

The library is Processing source (mathlib.pde next to this file; the same library is published for
desktop Processing at github.com/MoonFingerRF/BitMapMathLib, MIT), used as a *prelude*: nothing is
imported. `link(src)` appends to a sketch exactly the parts of the prelude it calls (and what
those need), so

* a sketch that calls none of it is left untouched (byte-identical source, program and frames);
* the compiler (`pde.library_parts`) and real Processing (which runs `link(src)`) compile the
  same code;
* a sketch that defines a function or class with a library name keeps its own (the library's is
  left out).

Which parts a sketch "calls": a library function named at a call (`name(` not after a `.`), or a
library class or constant (PHI, CX ...) named anywhere. A few functions call a function the SKETCH writes (CALLBACKS):
plot() calls `float fy(float x)`, polarPlot() and polarShape() `float fr(float a)`, shadeField()
`float shade(float x, float y)`.

Pure standard-library Python. The API reference a model reads is REFERENCE (mathlib_reference.txt);
`immanence link piece.pde` prints a piece with the parts it uses linked in, for real Processing.
"""
from __future__ import annotations

import functools
import hashlib
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PRELUDE_PATH = os.path.join(HERE, "mathlib.pde")
VERSION = 1
LINE_BASE = 10000          # prelude line k is reported as line LINE_BASE + k

CALLBACKS = {"plot": ("fy", "float fy(float x)"), "polarPlot": ("fr", "float fr(float a)"),
             "polarShape": ("fr", "float fr(float a)"),
             "shadeField": ("shade", "float shade(float x, float y)")}

with open(PRELUDE_PATH, encoding="utf-8") as _f:
    PRELUDE = _f.read()
PRELUDE_SHA = hashlib.sha256(PRELUDE.encode()).hexdigest()[:16]


# ----------------------------------------------------------------------------- a light Java lexer
_TOK = re.compile(r"""
    (?P<ws>\s+)
  | (?P<lc>//[^\n]*)
  | (?P<bc>/\*.*?(\*/|\Z))
  | (?P<str>"(\\.|[^"\\\n])*"?)
  | (?P<chr>'(\\.|[^'\\\n])*'?)
  | (?P<num>\.?\d[\w.]*)
  | (?P<id>[A-Za-z_$][\w$]*)
  | (?P<op>.)
""", re.S | re.X)


def tokens(src: str) -> list[tuple[str, str, int]]:
    """(kind, text, line) for identifiers and punctuation; comments, strings, numbers dropped
    (numbers and strings leave a placeholder token so `x = 1,` still reads as a declaration)."""
    out = []
    line = 1
    for m in _TOK.finditer(src):
        k, t = m.lastgroup, m.group()
        if k in ("id", "op"):
            out.append((k, t, line))
        elif k in ("num", "str", "chr"):
            out.append(("lit", "", line))
        line += t.count("\n")
    return out


def _defs(toks: list) -> tuple[set, set, set]:
    """Top-level (brace depth 0) definitions: (functions, classes, globals)."""
    funcs, classes, globs = set(), set(), set()
    depth = paren = 0
    for i, (k, t, _) in enumerate(toks):
        if k == "op":
            if t == "{":
                depth += 1
            elif t == "}":
                depth = max(0, depth - 1)
            elif t == "(":
                paren += 1
            elif t == ")":
                paren = max(0, paren - 1)
            continue
        if depth or k != "id":
            continue
        prev = toks[i - 1] if i else ("op", ";", 0)
        nxt = toks[i + 1] if i + 1 < len(toks) else ("op", ";", 0)
        if prev[1] == "class":
            classes.add(t)
        elif paren == 0 and nxt[1] == "(" and (prev[0] == "id" or prev[1] in ("]", ">")) and prev[1] != "new":
            funcs.add(t)
        elif paren == 0 and nxt[1] in ("=", ";", ",") and (prev[0] == "id" or prev[1] in ("]", ">", ",")):
            globs.add(t)
    return funcs, classes, globs


def _uses(toks: list, names: set) -> set:
    """Names a piece of code refers to: calls `name(` (not `.name(`), and `names` (the library's
    classes and public constants) anywhere."""
    out = set()
    for i, (k, t, _) in enumerate(toks):
        if k != "id" or (i and toks[i - 1][1] == "."):
            continue
        nxt = toks[i + 1][1] if i + 1 < len(toks) else ""
        if nxt == "(" or t in names:
            out.add(t)
    return out


class Chunk:
    __slots__ = ("index", "text", "line", "names", "kind", "refs")

    def __init__(self, index, text, line, names, kind, refs):
        self.index, self.text, self.line, self.names, self.kind, self.refs = index, text, line, names, kind, refs


@functools.lru_cache(maxsize=1)
def chunks() -> tuple:
    """The prelude cut into its top-level items (one function, class or global declaration each)."""
    toks = tokens(PRELUDE)
    lines = PRELUDE.split("\n")
    items = []
    depth = 0
    start = None
    for i, (k, t, ln) in enumerate(toks):
        if start is None:
            start = i
        if k == "op" and t == "{":
            depth += 1
        elif k == "op" and t == "}":
            depth -= 1
            if depth == 0 and not (i + 1 < len(toks) and toks[i + 1][1] in (";", ",")):
                items.append((start, i))
                start = None
        elif k == "op" and t == ";" and depth == 0:
            items.append((start, i))
            start = None
    all_f, all_c, all_g = _defs(toks)
    names_all = all_f | all_c | all_g
    out = []
    for n, (a, b) in enumerate(items):
        part = toks[a:b + 1]
        f, c, g = _defs(part)
        first, last = part[0][2], part[-1][2]
        text = "\n".join(lines[first - 1:last])
        refs = {t for k, t, _ in part if k == "id"} & names_all
        kind = "class" if c else ("func" if f else "global")
        out.append(Chunk(n, text, first, frozenset(f | c | g), kind, frozenset(refs - (f | c | g))))
    return tuple(out)


@functools.lru_cache(maxsize=1)
def _index() -> tuple[dict, set]:
    by_name = {}
    classes = set()
    for ch in chunks():
        for nm in ch.names:
            by_name[nm] = ch.index
        if ch.kind == "class":
            classes |= ch.names
    return by_name, classes


@functools.lru_cache(maxsize=1)
def _constants() -> frozenset:
    """The library's public globals (PHI, CX ...): linked wherever a sketch names them."""
    return frozenset(nm for ch in chunks() if ch.kind == "global" for nm in ch.names if not nm.startswith("_"))


PUBLIC = tuple(nm for ch in chunks() for nm in sorted(ch.names) if not nm.startswith("_"))


class LinkError(Exception):
    """A sketch calls a library function that needs a function of its own it does not have."""


@functools.lru_cache(maxsize=64)
def plan(src: str) -> tuple[int, ...]:
    """Indices of the prelude chunks `src` needs (empty: the sketch does not use the library).
    Raises LinkError when a needed callback (CALLBACKS) is missing."""
    by_name, classes = _index()
    consts = _constants()
    toks = tokens(src)
    funcs, cls, globs = _defs(toks)
    own = funcs | cls | (globs & consts)
    want = [nm for nm in _uses(toks, classes | consts) if nm in by_name and nm not in own]
    if not want:
        return ()
    need: set[int] = set()
    stack = [by_name[nm] for nm in want]
    allc = chunks()
    while stack:
        k = stack.pop()
        if k in need:
            continue
        need.add(k)
        for r in allc[k].refs:
            if r not in own and r in by_name:
                stack.append(by_name[r])
    for k in sorted(need):
        for nm in allc[k].names:
            cb = CALLBACKS.get(nm)
            if cb and cb[0] not in funcs:
                raise LinkError(f"{nm}() needs your own function: {cb[1]}")
    return tuple(sorted(need))


def uses_library(src: str) -> bool:
    try:
        return bool(plan(src))
    except LinkError:
        return True


def split_plan(src: str) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """(global declarations, everything else) of plan(src). The library's globals (PHI, CX, its
    caches) go BEFORE the sketch so a sketch's own global initialisers can use them: Java runs field
    initialisers in text order (after the sketch they would read 0 here, and real Processing
    refuses them as an illegal forward reference). Functions and classes go after it."""
    need = plan(src)
    allc = chunks()
    return (tuple(k for k in need if allc[k].kind == "global"),
            tuple(k for k in need if allc[k].kind != "global"))


def library_text(src: str) -> str:
    """The prelude parts `src` needs, as source ('' when none)."""
    return "\n\n".join(chunks()[k].text for k in plan(src))


def link(src: str) -> str:
    """`src` with the library parts it needs linked in (unchanged when it needs none): the library's
    global declarations on ONE line in front of the sketch's first line (so every line of the sketch
    keeps its number), its functions and classes appended. For real Processing; the compiler orders
    its AST the same way (pde.library_parts)."""
    pre, post = split_plan(src)
    if not pre and not post:
        return src
    allc = chunks()
    head = " ".join(" ".join(allc[k].text.split()) for k in pre)
    tail = "\n\n".join(allc[k].text for k in post)
    out = (head + " " + src) if head else src
    if tail:
        out = out.rstrip("\n") + "\n\n// ---- mathlib (linked) ----\n" + tail + "\n"
    return out


# ----------------------------------------------------------------------------- the reference
REFERENCE = open(os.path.join(HERE, "mathlib_reference.txt"), encoding="utf-8").read().rstrip("\n") \
    if os.path.exists(os.path.join(HERE, "mathlib_reference.txt")) else ""


def library_json() -> dict:
    """The prelude as the JavaScript compiler takes it (js/pde-compiler.js `library`)."""
    return {"classes": sorted(_index()[1]), "callbacks": CALLBACKS,
            "chunks": [{"text": c.text, "line": LINE_BASE + c.line, "names": sorted(c.names), "kind": c.kind,
                        "refs": sorted(c.refs)} for c in chunks()]}


# ----------------------------------------------------------------------------- command line
def _main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python3 -m immanence.mathlib",
                                 description="Link and inspect sketches that use BitMapMathLib.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ln = sub.add_parser("link", help="print a piece with the library parts it uses appended (for real Processing)")
    ln.add_argument("file")
    sub.add_parser("reference", help="print the API reference")
    sub.add_parser("names", help="print the public names")
    sub.add_parser("json", help="print the chunk table the JavaScript compiler links from (js/mathlib.json)")
    a = ap.parse_args(argv)
    if a.cmd == "json":
        import json
        print(json.dumps(library_json(), separators=(",", ":")))
        return 0
    if a.cmd == "reference":
        print(REFERENCE)
        return 0
    if a.cmd == "names":
        print(" ".join(PUBLIC))
        return 0
    print(link(open(a.file, encoding="utf-8").read()), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
