"""Java-mode Processing source -> tokens.

A token is (kind, value, line): kind is one of
  "id"   identifier or keyword (value = the word)
  "int"  integer literal (value = Python int; `long` flag in the kind "long")
  "long" long literal (123L)
  "float" / "double"  floating literal (Processing reads a plain `1.5` as float)
  "char" (value = code point)   "str" (value = text)   "color" (#RRGGBB -> 0xFFRRGGBB)
  "op"   operator or punctuation (value = the symbol)
  "eof"
Bounded: the source and the token count are capped before anything is built.
"""
from __future__ import annotations

from .errors import PdeError

MAX_SOURCE = 16 * 1024
MAX_TOKENS = 12000

OPS = sorted("""
>>>= <<= >>= >>> ... -> :: ++ -- && || == != <= >= += -= *= /= %= &= |= ^= << >>
( ) { } [ ] ; , . = > < ! ~ ? : + - * / & | ^ % @
""".split(), key=len, reverse=True)
_OP_FIRST = set(o[0] for o in OPS)
_ESC = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "0": "\0", "\\": "\\", "'": "'", '"': '"'}


def _escape(src: str, i: int, line: int) -> tuple[str, int]:
    """The character after a backslash at src[i]; returns (char, next index)."""
    if i >= len(src):
        raise PdeError("syntax", "unfinished escape", line)
    c = src[i]
    if c == "u":
        j = i + 1
        while j < len(src) and src[j] == "u":
            j += 1
        hexs = src[j:j + 4]
        try:
            return chr(int(hexs, 16)), j + 4
        except ValueError:
            raise PdeError("syntax", "bad \\u escape", line) from None
    if c in "01234567":
        j = i
        while j < len(src) and j < i + 3 and src[j] in "01234567":
            j += 1
        return chr(int(src[i:j], 8) & 0xFF), j
    if c in _ESC:
        return _ESC[c], i + 1
    raise PdeError("syntax", f"bad escape \\{c}", line)


def tokenize(src: str, first_line: int = 1, bounded: bool = True) -> list[tuple]:
    """`first_line` numbers the lines from there; `bounded=False` lifts the size caps (only for
    the art library's own trusted prelude, art/mathlib.pde -- never for a sketch)."""
    if bounded and len(src) > MAX_SOURCE:
        raise PdeError("syntax", f"the sketch is longer than {MAX_SOURCE // 1024} KB")
    out: list[tuple] = []
    i, n, line = 0, len(src), first_line
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c in " \t\r\f\v﻿ ":
            i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            if j < 0:
                raise PdeError("syntax", "unfinished /* comment", line)
            line += src.count("\n", i, j)
            i = j + 2
            continue
        if bounded and len(out) >= MAX_TOKENS:
            raise PdeError("syntax", "the sketch is too long")
        if c.isalpha() or c == "_" or c == "$":
            j = i + 1
            while j < n and (src[j].isalnum() or src[j] in "_$"):
                j += 1
            out.append(("id", src[i:j], line))
            i = j
            continue
        if c.isdigit() or (c == "." and i + 1 < n and src[i + 1].isdigit()):
            i = _number(src, i, line, out)
            continue
        if c == "#" and i + 7 <= n and all(ch in "0123456789abcdefABCDEF" for ch in src[i + 1:i + 7]):
            out.append(("color", 0xFF000000 | int(src[i + 1:i + 7], 16), line))
            i += 7
            continue
        if c == '"':
            j = i + 1
            buf = []
            while True:
                if j >= n or src[j] == "\n":
                    raise PdeError("syntax", "unfinished string", line)
                ch = src[j]
                if ch == '"':
                    break
                if ch == "\\":
                    e, j = _escape(src, j + 1, line)
                    buf.append(e)
                    continue
                buf.append(ch)
                j += 1
            out.append(("str", "".join(buf), line))
            i = j + 1
            continue
        if c == "'":
            j = i + 1
            if j < n and src[j] == "\\":
                ch, j = _escape(src, j + 1, line)
            elif j < n:
                ch, j = src[j], j + 1
            else:
                raise PdeError("syntax", "unfinished character", line)
            if j >= n or src[j] != "'":
                raise PdeError("syntax", "a character literal holds one character", line)
            out.append(("char", ord(ch), line))
            i = j + 1
            continue
        if c in _OP_FIRST:
            for op in OPS:
                if src.startswith(op, i):
                    out.append(("op", op, line))
                    i += len(op)
                    break
            continue
        # curly quotes and other look-alikes a model sometimes writes
        if c in "“”":
            j = src.find("”" if c == "“" else "“", i + 1)
            k = src.find("\n", i + 1)
            if j > 0 and (k < 0 or j < k):
                out.append(("str", src[i + 1:j], line))
                i = j + 1
                continue
        raise PdeError("syntax", f"unexpected character {c!r}", line)
    out.append(("eof", None, line))
    return out


# The longest numeric literal worth reading, in characters (digits, `_`, `.`, exponent). Java's
# longest legal integer is 19 digits and no float needs more than ~25 significant ones; a piece
# with more is not a number anybody meant, and a 5,000-digit decimal reached Python's
# int-from-string limit as a ValueError that no caller expected.
MAX_NUMBER_CHARS = 80


def _number(src: str, i: int, line: int, out: list) -> int:
    """Read one numeric literal at `i` into `out`; return where it ends. Every way a literal can be
    wrong is a syntax PdeError -- never a bare ValueError/OverflowError -- so one bad piece is
    reported as that piece's error and the rest of an answer is still judged."""
    try:
        return _read_number(src, i, line, out)
    except (ValueError, OverflowError):
        raise PdeError("syntax", "bad number", line) from None


def _read_number(src: str, i: int, line: int, out: list) -> int:
    n = len(src)
    end = i
    while end < n and (src[end].isalnum() or src[end] in "_."):
        end += 1
    if end - i > MAX_NUMBER_CHARS and not src.startswith("...", i):
        # checked BEFORE any conversion: int() of thousands of digits is quadratic, and raises
        raise PdeError("syntax", "number too long", line)
    if src.startswith(("0x", "0X"), i):
        j = i + 2
        while j < n and (src[j] in "0123456789abcdefABCDEF_"):
            j += 1
        digits = src[i + 2:j].replace("_", "")
        if not digits:
            raise PdeError("syntax", "bad hex number", line)
        v = int(digits, 16)
        if j < n and src[j] in "lL":
            out.append(("long", _wrap(v, 64), line))
            return j + 1
        if v > 0xFFFFFFFF:
            raise PdeError("syntax", "integer number too large", line)
        out.append(("int", _wrap(v, 32), line))
        return j
    if src.startswith(("0b", "0B"), i):
        j = i + 2
        while j < n and src[j] in "01_":
            j += 1
        v = int(src[i + 2:j].replace("_", "") or "0", 2)
        if j < n and src[j] in "lL":
            out.append(("long", _wrap(v, 64), line))
            return j + 1
        out.append(("int", _wrap(v, 32), line))
        return j
    j = i
    while j < n and (src[j].isdigit() or src[j] == "_"):
        j += 1
    is_float = False
    if j < n and src[j] == "." and not src.startswith("...", j):
        # `1.` is a float in Java; `1..` never happens in Java code
        is_float = True
        j += 1
        while j < n and (src[j].isdigit() or src[j] == "_"):
            j += 1
    if j < n and src[j] in "eE":
        k = j + 1
        if k < n and src[k] in "+-":
            k += 1
        if k < n and src[k].isdigit():
            is_float = True
            j = k
            while j < n and src[j].isdigit():
                j += 1
    text = src[i:j].replace("_", "")
    if j < n and src[j] in "fFdD":
        kind = "double" if src[j] in "dD" else "float"
        out.append((kind, float(text), line))
        return j + 1
    if is_float:
        out.append(("float", float(text), line))   # Processing: a plain 1.5 is a float
        return j
    if j < n and src[j] in "lL":
        out.append(("long", _wrap(int(text), 64), line))
        return j + 1
    v = int(text, 8) if len(text) > 1 and text.startswith("0") and text.isdigit() and "8" not in text \
        and "9" not in text else int(text)
    if v > 0x80000000:
        raise PdeError("syntax", "integer number too large", line)
    out.append(("int", v, line))    # 2147483648 only legal after unary minus; wrapped there
    return j


def _wrap(v: int, bits: int) -> int:
    m = 1 << bits
    v &= m - 1
    return v - m if v >= m >> 1 else v
