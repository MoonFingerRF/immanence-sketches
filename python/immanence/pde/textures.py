"""Textures: 1-bit pen patterns for any filled shape -- an extension to Processing.

Processing's look, plus a set of 1-bit textures. A textured fill
paints every covered pixel pure ink or pure paper by a pattern rule, so the final dither leaves it
exactly as drawn (crisp 1-bit). The level (0 paper .. 16 solid) comes from the fill grey, mapped as
the dither maps greys: level = ((255 - g) * 16 + 127) // 255.

* The nine of the old language (flat, noise, gated, cloud, hatch, lines, vlines, cross, dots) are
  `ink.inked` itself, so they match ink.py pixel for pixel.
* Motif presets (checker, brick, weave, wavy): level 8 (fill(128)) shows the pure motif; darker
  levels fill the gaps with the flat dither, lighter levels fade the motif.
* stipple: a 16x16 blue-noise threshold matrix (void-and-cluster, generated once), density level/16.
* halftone: a 45-degree dot screen whose dots grow with the level.
* Gradients (vgradient, hgradient, radial): the flat dither of a level that runs from the fill grey
  to a second grey across the shape's box (the whole canvas for background()).

Pattern coordinates are layer-local: pixel minus the matrix's translation, rounded -- a texture
moves with translate() (so frameCount can drift it) and does not rotate or scale.
"""
from __future__ import annotations

from .. import ink
from ..tables import BAYER4, SIN

OLD = ("flat", "noise", "gated", "cloud", "hatch", "lines", "vlines", "cross", "dots")
NEW = ("checker", "brick", "weave", "wavy", "stipple", "halftone")
GRADIENTS = ("vgradient", "hgradient", "radial")
ALL = OLD + NEW + GRADIENTS
DEFAULT_P = {"gated": 12, "cloud": 12, "checker": 8, "brick": 6, "weave": 8, "wavy": 8, "stipple": 1,
             "halftone": 6}

BLUE16 = (
    234, 50, 188, 19, 58, 171, 121, 47, 163, 3, 247, 104, 22, 132, 14, 65,
    209, 8, 118, 97, 240, 205, 23, 228, 138, 64, 123, 170, 72, 224, 99, 149,
    85, 139, 229, 165, 78, 146, 111, 84, 176, 216, 30, 231, 153, 201, 42, 180,
    25, 62, 195, 29, 43, 185, 7, 249, 41, 100, 191, 48, 87, 5, 128, 243,
    221, 152, 101, 253, 130, 220, 59, 200, 156, 12, 136, 112, 254, 174, 69, 109,
    46, 189, 2, 73, 172, 90, 142, 116, 80, 237, 210, 61, 147, 33, 206, 160,
    81, 124, 217, 113, 208, 15, 241, 27, 168, 45, 178, 20, 193, 96, 225, 18,
    242, 164, 60, 35, 157, 53, 181, 68, 223, 105, 125, 83, 236, 131, 55, 141,
    197, 10, 227, 134, 246, 95, 126, 198, 148, 1, 244, 161, 71, 9, 182, 106,
    40, 93, 179, 75, 192, 6, 218, 36, 91, 57, 202, 34, 215, 155, 233, 74,
    252, 120, 150, 24, 110, 63, 166, 119, 232, 183, 133, 103, 49, 117, 31, 167,
    16, 212, 51, 238, 207, 137, 255, 21, 76, 151, 13, 250, 190, 88, 203, 135,
    102, 184, 82, 169, 38, 89, 187, 52, 204, 98, 173, 67, 129, 4, 222, 56,
    230, 144, 0, 127, 226, 11, 154, 114, 239, 39, 219, 28, 235, 145, 175, 77,
    196, 37, 248, 70, 107, 199, 66, 177, 17, 143, 115, 159, 86, 44, 108, 26,
    122, 92, 158, 214, 140, 32, 245, 94, 213, 79, 194, 54, 211, 186, 251, 162,
)


def level_of(g: int) -> int:
    return ((255 - int(g)) * 16 + 127) // 255


def clamp_p(tex: str, p: float | None) -> int:
    if p is None:
        return DEFAULT_P.get(tex, 6)
    lo = 1 if tex == "stipple" else 2
    return max(lo, min(64, int(p)))


def _bayer(level: int, xl: int, yl: int) -> bool:
    return level > BAYER4[yl & 3][xl & 3]


def _motif(tex: str, p: int, xl: int, yl: int) -> bool:
    if tex == "checker":
        return ((xl // p) + (yl // p)) & 1 == 0
    if tex == "brick":
        row = yl // p
        return yl % p == 0 or (xl + (p if row & 1 else 0)) % (2 * p) == 0
    if tex == "weave":
        q = max(2, p // 3)
        return (yl % q == 0) if ((xl // p) + (yl // p)) & 1 == 0 else (xl % q == 0)
    if tex == "wavy":
        amp = max(1, p // 3)
        off = (amp * SIN[((xl * 90) // p) % 360] + 512) >> 10
        return (yl + off) % p == 0
    return False


def inked(tex: str, p: int, level: int, seed: int, xl: int, yl: int) -> bool:
    """Is the layer-local pixel ink? (gradients are handled by the painter: they need a box)"""
    if level <= 0:
        return False
    if tex in OLD:
        return ink.inked(tex, p, level, seed, xl, yl)
    if tex in ("checker", "brick", "weave", "wavy"):
        if _motif(tex, p, xl, yl):
            return level >= 8 or _bayer(level * 2, xl, yl)
        return level > 8 and _bayer((level - 8) * 2, xl, yl)
    if tex == "stipple":
        k = max(1, p)
        sx, sy = xl // k + ((seed * 7) & 15), yl // k + ((seed * 11) & 15)
        return level * 16 > BLUE16[(sy & 15) * 16 + (sx & 15)]
    if tex == "halftone":
        u, v = xl + yl, xl - yl
        dx, dy = 2 * (u % p) + 1 - p, 2 * (v % p) + 1 - p
        return (dx * dx + dy * dy) * 16 < level * 2 * p * p
    return _bayer(level, xl, yl)


def _value_row(tex: str, p: int, level: int, seed: int, yl: int, ox: int, w: int) -> bytes:
    """A row of `cloud` or `gated` exactly as ink.inked draws it, with the value-noise lattice
    hashed once per cell instead of four times per pixel (the hot path of a drifting texture)."""
    s = p
    gy = yl // s
    fy = yl - gy * s
    lat: dict[int, tuple[int, int]] = {}

    def cell(gx: int) -> tuple[int, int]:
        got = lat.get(gx)
        if got is None:
            got = lat[gx] = (ink.h4(seed, gx, gy, 0x51ED) & 255, ink.h4(seed, gx, gy + 1, 0x51ED) & 255)
        return got
    b = BAYER4[yl & 3]
    ss = s * s
    out = bytearray(w)
    for x in range(w):
        xl = x - ox
        gx = xl // s
        fx = xl - gx * s
        v00, v01 = cell(gx)
        v10, v11 = cell(gx + 1)
        vn = (v00 * (s - fx) * (s - fy) + v10 * fx * (s - fy) + v01 * (s - fx) * fy + v11 * fx * fy) // ss
        if tex == "cloud":
            hit = ((vn * level) >> 8) > b[xl & 3]
        else:
            hit = vn >= 128 and (ink.h4(seed ^ 0x5BD1E995, xl, yl, 0) & 15) < level
        out[x] = 0 if hit else 255
    return bytes(out)


class Painter:
    """What a textured fill paints: `row(y)` -> 240 bytes, 0 (ink) or 255 (paper) per pixel."""

    __slots__ = ("tex", "p", "level", "level2", "seed", "ox", "oy", "box", "cache")

    def __init__(self, tex, p, level, level2, seed, ox, oy, cache, box=None) -> None:
        self.tex, self.p, self.level, self.level2 = tex, p, level, level2
        self.seed, self.ox, self.oy, self.box, self.cache = seed & 0xFFFF, ox, oy, box, cache

    def row(self, y: int, w: int = 240) -> bytes:
        tex = self.tex
        grad = tex in GRADIENTS
        key = (tex, self.p, self.level, self.level2 if grad else 0,
               self.seed if tex in ("noise", "gated", "cloud", "stipple") else 0, y - self.oy, self.ox,
               self.box if grad else None, y if grad else 0)
        got = self.cache.get(key)
        if got is not None:
            return got
        yl = y - self.oy
        ox = self.ox
        if grad:
            out = self._gradient_row(y, w)
        elif self.level <= 0:
            out = bytes([255]) * w
        elif self.level >= 16 and tex in ("flat", "noise", "checker", "brick", "weave", "wavy", "stipple"):
            out = bytes(w)
        elif tex in ("cloud", "gated"):
            out = _value_row(tex, self.p, self.level, self.seed, yl, ox, w)
        else:
            fn, tex_, p, lv, sd = inked, tex, self.p, self.level, self.seed
            out = bytes(0 if fn(tex_, p, lv, sd, x - ox, yl) else 255 for x in range(w))
        if len(self.cache) > 4096:
            self.cache.clear()
        self.cache[key] = out
        return out

    def _gradient_row(self, y: int, w: int) -> bytes:
        x0, y0, x1, y1 = self.box if self.box else (0, 0, w, 128)
        l1, l2 = self.level, self.level2
        yl = y - self.oy
        out = bytearray(w)
        bw, bh = max(1, x1 - x0 - 1), max(1, y1 - y0 - 1)
        if self.tex == "vgradient":
            t = min(1.0, max(0.0, (y - y0) / bh))
            lv = int(l1 + (l2 - l1) * t + 0.5)
            b = BAYER4[yl & 3]
            for x in range(w):
                out[x] = 0 if lv > b[(x - self.ox) & 3] else 255
            return bytes(out)
        cx, cy = (x0 + x1 - 1) / 2, (y0 + y1 - 1) / 2
        r = max(1.0, max(x1 - x0, y1 - y0) / 2)
        b = BAYER4[yl & 3]
        for x in range(w):
            if self.tex == "hgradient":
                t = min(1.0, max(0.0, (x - x0) / bw))
            else:
                t = min(1.0, ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 / r)
            lv = int(l1 + (l2 - l1) * t + 0.5)
            out[x] = 0 if lv > b[(x - self.ox) & 3] else 255
        return bytes(out)
