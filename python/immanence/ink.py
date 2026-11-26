"""The ink primitives: coverage rasterisers, textures and paint modes on a 1-bit canvas.

The textures of the sketch dialect (pde/textures.py) are drawn with these primitives; the C++ VM
(src/pde_textures.cpp) and NanoGFX `src/PackInk.h` produce the same pixels bit for bit, so
everything here is integer arithmetic with floor division and every constant table is shared
(`tables.py`).

Representation: a canvas and a coverage are lists of Python ints, one per row, bit x = pixel x.
That is what makes a pure-standard-library renderer fast enough:
a span is one shift, a paint is four bitwise operations per row.
"""
from __future__ import annotations

from math import isqrt

from .tables import BAYER4, GLYPHS, SIN

M32 = 0xFFFFFFFF
TEXTURES = ("flat", "noise", "gated", "cloud", "hatch", "lines", "vlines", "cross", "dots")
MODES = ("cover", "glaze", "erase", "invert")


def mix(x: int) -> int:
    """lowbias32: a 32-bit integer finaliser."""
    x &= M32
    x ^= x >> 16
    x = (x * 0x7FEB352D) & M32
    x ^= x >> 15
    x = (x * 0x846CA68B) & M32
    x ^= x >> 16
    return x


def h4(a: int, b: int, c: int, d: int) -> int:
    h = mix((a + 0x9E3779B9) & M32)
    h = mix(h ^ (b & M32))
    h = mix(h ^ (c & M32))
    return mix(h ^ (d & M32))


def sin1024(deg: int) -> int:
    return SIN[deg % 360]


def cos1024(deg: int) -> int:
    return SIN[(deg + 90) % 360]


def polar(cx: int, cy: int, r: int, deg: int) -> tuple[int, int]:
    return cx + (r * cos1024(deg) + 512) // 1024, cy + (r * sin1024(deg) + 512) // 1024


# --------------------------------------------------------------------------- coverage
class Cov:
    """The set of pixels one shape covers, clipped to the canvas, plus how many pen stamps it took."""

    __slots__ = ("w", "h", "rows", "stamps")

    def __init__(self, w: int, h: int) -> None:
        self.w, self.h = w, h
        self.rows = [0] * h
        self.stamps = 0

    def span(self, y: int, x0: int, x1: int) -> None:
        """Pixels x0..x1 inclusive on row y."""
        if y < 0 or y >= self.h:
            return
        if x0 < 0:
            x0 = 0
        if x1 >= self.w:
            x1 = self.w - 1
        if x0 > x1:
            return
        self.rows[y] |= ((1 << (x1 - x0 + 1)) - 1) << x0

    def count(self) -> int:
        return sum(r.bit_count() for r in self.rows)


def ellipse_half(rx: int, ry: int, dy: int) -> int:
    if ry == 0:
        return rx
    return isqrt((rx * rx * (ry * ry - dy * dy)) // (ry * ry))


def fill_rect(c: Cov, x: int, y: int, w: int, h: int) -> None:
    for yy in range(max(y, 0), min(y + h, c.h)):
        c.span(yy, x, x + w - 1)


def outline_rect(c: Cov, x: int, y: int, w: int, h: int, s: int) -> None:
    if 2 * s >= w or 2 * s >= h:
        fill_rect(c, x, y, w, h)
        return
    for yy in range(max(y, 0), min(y + h, c.h)):
        if yy < y + s or yy >= y + h - s:
            c.span(yy, x, x + w - 1)
        else:
            c.span(yy, x, x + s - 1)
            c.span(yy, x + w - s, x + w - 1)


def fill_ellipse(c: Cov, cx: int, cy: int, rx: int, ry: int) -> None:
    for dy in range(-ry, ry + 1):
        y = cy + dy
        if 0 <= y < c.h:
            hw = ellipse_half(rx, ry, dy)
            c.span(y, cx - hw, cx + hw)


def outline_ellipse(c: Cov, cx: int, cy: int, rx: int, ry: int, s: int) -> None:
    irx, iry = rx - s, ry - s
    if irx < 0 or iry < 0:
        fill_ellipse(c, cx, cy, rx, ry)
        return
    for dy in range(-ry, ry + 1):
        y = cy + dy
        if not 0 <= y < c.h:
            continue
        hw = ellipse_half(rx, ry, dy)
        if -iry <= dy <= iry:
            ih = ellipse_half(irx, iry, dy)
            c.span(y, cx - hw, cx - ih - 1)
            c.span(y, cx + ih + 1, cx + hw)
        else:
            c.span(y, cx - hw, cx + hw)


def fill_poly(c: Cov, pts: list[tuple[int, int]]) -> None:
    n = len(pts)
    if n < 3:
        return
    ymin = max(min(p[1] for p in pts), 0)
    ymax = min(max(p[1] for p in pts), c.h)
    for y in range(ymin, ymax):
        xs = []
        for i in range(n):
            x0, y0 = pts[i]
            x1, y1 = pts[(i + 1) % n]
            if y0 == y1:
                continue
            if min(y0, y1) <= y < max(y0, y1):
                xs.append(x0 + ((y - y0) * (x1 - x0)) // (y1 - y0))
        xs.sort()
        for i in range(0, len(xs) - 1, 2):
            if xs[i + 1] > xs[i]:
                c.span(y, xs[i], xs[i + 1] - 1)


def stamp(c: Cov, x: int, y: int, s: int) -> None:
    c.stamps += 1
    if s <= 1:
        c.span(y, x, x)
    elif s == 2:
        c.span(y, x, x + 1)
        c.span(y + 1, x, x + 1)
    else:
        r = s // 2
        for dy in range(-r, r + 1):
            hw = ellipse_half(r, r, dy)
            c.span(y + dy, x - hw, x + hw)


def line(c: Cov, x0: int, y0: int, x1: int, y1: int, s: int) -> None:
    """Bresenham, every point stamped (both ends included)."""
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        stamp(c, x0, y0, s)
        if x0 == x1 and y0 == y1:
            return
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def polyline(c: Cov, pts: list[tuple[int, int]], s: int, closed: bool = False) -> None:
    for i in range(len(pts) - 1):
        line(c, pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], s)
    if closed and len(pts) > 2:
        line(c, pts[-1][0], pts[-1][1], pts[0][0], pts[0][1], s)
    if len(pts) == 1:
        stamp(c, pts[0][0], pts[0][1], s)


def _segments(length: int) -> int:
    return max(4, min(32, length // 6))


def quad_points(x1: int, y1: int, cx: int, cy: int, x2: int, y2: int) -> list[tuple[int, int]]:
    n = _segments(abs(cx - x1) + abs(cy - y1) + abs(x2 - cx) + abs(y2 - cy))
    nn = n * n
    out = []
    for i in range(n + 1):
        a = n - i
        out.append(((a * a * x1 + 2 * a * i * cx + i * i * x2 + nn // 2) // nn,
                    (a * a * y1 + 2 * a * i * cy + i * i * y2 + nn // 2) // nn))
    return out


def cubic_points(x1: int, y1: int, ax: int, ay: int, bx: int, by: int, x2: int, y2: int) -> list[tuple[int, int]]:
    n = _segments(abs(ax - x1) + abs(ay - y1) + abs(bx - ax) + abs(by - ay) + abs(x2 - bx) + abs(y2 - by))
    n3 = n * n * n
    out = []
    for i in range(n + 1):
        a = n - i
        out.append(((a * a * a * x1 + 3 * a * a * i * ax + 3 * a * i * i * bx + i * i * i * x2 + n3 // 2) // n3,
                    (a * a * a * y1 + 3 * a * a * i * ay + 3 * a * i * i * by + i * i * i * y2 + n3 // 2) // n3))
    return out


def arc_points(cx: int, cy: int, r: int, a0: int, a1: int) -> list[tuple[int, int]]:
    while a1 < a0:
        a1 += 360
    if a1 - a0 > 360:
        a1 = a0 + 360
    step = 10 if r < 24 else 5 if r < 96 else 3
    out = []
    a = a0
    while a < a1:
        out.append(polar(cx, cy, r, a))
        a += step
    out.append(polar(cx, cy, r, a1))
    return out


def wave_points(x: int, y: int, w: int, amp: int, period: int, phase: int) -> list[tuple[int, int]]:
    period = max(4, period)
    w = max(0, w)
    out = []
    px = x
    while True:
        deg = phase + ((px - x) * 360) // period
        out.append((px, y + (amp * sin1024(deg) + 512) // 1024))
        if px >= x + w:
            return out
        px = min(px + 2, x + w)


def spiral_points(cx: int, cy: int, r: int, turns: int) -> list[tuple[int, int]]:
    total = max(1, turns) * 360
    return [polar(cx, cy, (r * d) // total, d) for d in range(0, total + 1, 10)]


def text(c: Cov, x: int, y: int, size: int, s: str, turn: int, align: str) -> None:
    """The 5x7 font, `size` pixels per font pixel, rotated clockwise by `turn` about (x, y)."""
    n = len(s)
    width = n * 6 * size - size if n else 0
    ax = 0 if align == "left" else -(width // 2) if align == "center" else -width
    for i, ch in enumerate(s):
        code = ord(ch)
        if code < 32 or code > 126:
            code = 63
        base = (code - 32) * 5
        for col in range(5):
            bits = GLYPHS[base + col]
            for row in range(7):
                if not (bits >> row) & 1:
                    continue
                u0 = (i * 6 + col) * size + ax
                v0 = row * size
                if turn == 0:
                    fill_rect(c, x + u0, y + v0, size, size)
                elif turn == 90:
                    fill_rect(c, x - v0 - size, y + u0, size, size)
                elif turn == 180:
                    fill_rect(c, x - u0 - size, y - v0 - size, size, size)
                else:
                    fill_rect(c, x + v0, y - u0 - size, size, size)


# --------------------------------------------------------------------------- textures
def value_noise(xl: int, yl: int, s: int, seed: int) -> int:
    gx, gy = xl // s, yl // s
    fx, fy = xl - gx * s, yl - gy * s
    v00 = h4(seed, gx, gy, 0x51ED) & 255
    v10 = h4(seed, gx + 1, gy, 0x51ED) & 255
    v01 = h4(seed, gx, gy + 1, 0x51ED) & 255
    v11 = h4(seed, gx + 1, gy + 1, 0x51ED) & 255
    return (v00 * (s - fx) * (s - fy) + v10 * fx * (s - fy) + v01 * (s - fx) * fy + v11 * fx * fy) // (s * s)


def inked(tex: str, p: int, level: int, seed: int, xl: int, yl: int) -> bool:
    """Is the layer-local pixel (xl, yl) ink under this pen? The normative per-pixel rule."""
    if level <= 0:
        return False
    if tex == "flat":
        return level > BAYER4[yl & 3][xl & 3]
    if tex == "noise":
        return (h4(seed, xl, yl, 0) & 15) < level
    if tex == "gated":
        return value_noise(xl, yl, p, seed) >= 128 and (h4(seed ^ 0x5BD1E995, xl, yl, 0) & 15) < level
    if tex == "cloud":
        return ((value_noise(xl, yl, p, seed) * level) >> 8) > BAYER4[yl & 3][xl & 3]
    w = (level * p + 15) >> 4
    if tex == "hatch":
        return (xl + yl) % p < w
    if tex == "lines":
        return yl % p < w
    if tex == "vlines":
        return xl % p < w
    if tex == "cross":
        return (xl + yl) % p < w or (xl - yl) % p < w
    if tex == "dots":
        dx, dy = 2 * (xl % p) + 1 - p, 2 * (yl % p) + 1 - p
        return (dx * dx + dy * dy) * 16 < level * 2 * p * p
    return False


class Textures:
    """Row masks of a texture, cached: the texture of a row depends only on the pen and the shift."""

    def __init__(self, w: int) -> None:
        self.w = w
        self.cache: dict[tuple, int] = {}

    def row(self, tex: str, p: int, level: int, seed: int, y: int, mx: int, my: int) -> int:
        if level <= 0:
            return 0
        key = (tex, p, level, seed if tex in ("noise", "gated", "cloud") else 0, y - my, mx)
        got = self.cache.get(key)
        if got is not None:
            return got
        yl = y - my
        mask = 0
        for x in range(self.w):
            if inked(tex, p, level, seed, x - mx, yl):
                mask |= 1 << x
        if len(self.cache) > 20000:
            self.cache.clear()
        self.cache[key] = mask
        return mask


def paint(rows: list[int], cov: list[int], ink_row, mode: str, w: int) -> None:
    """Apply one shape: `cov` already clipped and masked, `ink_row(y)` the texture mask of row y."""
    full = (1 << w) - 1
    for y, m in enumerate(cov):
        if not m:
            continue
        if mode == "erase":
            rows[y] &= ~m & full
            continue
        ink = ink_row(y) & m
        if mode == "cover":
            rows[y] = (rows[y] & ~m & full) | ink
        elif mode == "glaze":
            rows[y] |= ink
        else:  # invert
            rows[y] ^= ink
