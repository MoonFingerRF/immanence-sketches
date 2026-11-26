"""The grey canvas and the one way anything gets onto it: filled, anti-aliased contours.

Every shape, stroke and glyph of a sketch arrives here as contours in DEVICE space (the sketch's
matrix already applied). They are filled with the non-zero winding rule -- what Java2D's
GeneralPath does -- with anti-aliasing close to Java2D's: 4 sub-rows per pixel row, each with the
exact horizontal coverage of its spans. The canvas is one byte per pixel, 0 = black, 255 = white;
a fill paints grey `g` with opacity `a` (0..255) times coverage.

Strokes become contours in the sketch's own (user) space first -- `stroke_polyline` -- and are then
transformed like any shape, which is how Java2D strokes (a scaled sketch has scaled strokes).
Pure Python, standard library only; the costs are charged to the caller's step budget.
"""
from __future__ import annotations

import math

W, H = 240, 128
SUB = 4                     # sub-rows per pixel row
_SUBY = tuple((k + 0.5) / SUB for k in range(SUB))
_Q = 1.0 / SUB


BLEND = 1


def blend_grey(mode: int, v: int, g: int, a: int) -> int:
    """blendMode() on one grey channel: PImage.blendColor(dst v, src g with alpha a), the formulas
    Processing's JAVA2D BlendingContext uses per channel (ADD, SUBTRACT, LIGHTEST, DARKEST,
    DIFFERENCE, EXCLUSION, MULTIPLY, SCREEN; REPLACE writes the source; anything else is BLEND)."""
    if mode == 0:
        return g
    sa = a + (1 if a >= 0x7F else 0)
    da = 0x100 - sa
    if mode == 2:
        return min(v + ((g * sa) >> 8), 255)
    if mode == 4:
        return max(v - ((g * sa) >> 8), 0)
    if mode == 8:
        return (v * da + max(g, v) * sa) >> 8
    if mode == 16:
        return (v * da + min(g, v) * sa) >> 8
    if mode == 32:
        return (v * da + abs(v - g) * sa) >> 8
    if mode == 64:
        sub = (g * (v + (1 if v >= 0x7F else 0))) >> 7
        return max(0, min(255, (v * da + (v + g - sub) * sa) >> 8))
    if mode == 128:
        return (v * da + ((g * (v + 1)) >> 8) * sa) >> 8
    if mode == 256:
        return (v * da + (v + g - ((g * (v + 1)) >> 8)) * sa) >> 8
    return int(v + (g - v) * (a / 255.0) + 0.5)


class Canvas:
    __slots__ = ("buf", "aa", "tables", "cost", "blend")

    def __init__(self, grey: int = 204) -> None:
        self.buf = bytearray([grey]) * (W * H)
        self.aa = True
        self.tables: dict[tuple[int, int], bytes] = {}
        self.cost = 0          # work done, in "pixel touches", read and reset by the runtime
        self.blend = BLEND     # blendMode(): BLEND is the ordinary painting below

    # ------------------------------------------------------------------ painting
    def table(self, g: int, a: int) -> bytes:
        """The blend of every grey 0..255 with `g` at opacity a/255, as a translate() table
        (under blendMode(), that mode's result instead)."""
        key = (g, a) if self.blend == BLEND else (g, a, self.blend)
        t = self.tables.get(key)
        if t is None:
            if len(self.tables) > 512:
                self.tables.clear()
            if self.blend == BLEND:
                f = a / 255.0
                t = bytes(int(v + (g - v) * f + 0.5) for v in range(256))
            else:
                m = self.blend
                t = bytes(blend_grey(m, v, g, a) for v in range(256))
            self.tables[key] = t
        return t

    def clear(self, g: int, a: int = 255) -> None:
        if a >= 255:
            self.buf[:] = bytes([g]) * (W * H)
        else:
            m, self.blend = self.blend, BLEND        # background() ignores blendMode()
            try:
                self.buf[:] = self.buf.translate(self.table(g, a))
            finally:
                self.blend = m
        self.cost += W * H // 8

    def span(self, y: int, x0: int, x1: int, g: int, a: int) -> None:
        """Pixels x0..x1-1 of row y fully covered."""
        if x1 <= x0:
            return
        i = y * W
        if self.blend != BLEND:
            if a > 0 or self.blend == 0:
                self.buf[i + x0:i + x1] = self.buf[i + x0:i + x1].translate(self.table(g, a))
        elif a >= 255:
            self.buf[i + x0:i + x1] = bytes([g]) * (x1 - x0)
        elif a > 0:
            self.buf[i + x0:i + x1] = self.buf[i + x0:i + x1].translate(self.table(g, a))
        self.cost += (x1 - x0) >> 3

    def pixel(self, i: int, g: int, cov: float) -> None:
        """One pixel (index i) at coverage `cov` * opacity already folded in: 0..1."""
        if cov <= 0.0:
            return
        v = self.buf[i]
        if self.blend != BLEND:
            self.buf[i] = self.table(g, int(min(cov, 1.0) * 255 + 0.5))[v]
            return
        if cov >= 1.0:
            self.buf[i] = g
        else:
            self.buf[i] = int(v + (g - v) * cov + 0.5)

    def thin_line(self, x0: float, y0: float, x1: float, y1: float, g: int, a: int, painter=None) -> None:
        """Java2D's aliased 1-pixel line (noSmooth, strokeWeight <= 1): Bresenham between the
        end points rounded half up, both ends included."""
        ax, ay = int(math.floor(x0 + 0.5)), int(math.floor(y0 + 0.5))
        bx, by = int(math.floor(x1 + 0.5)), int(math.floor(y1 + 0.5))
        dx, dy = abs(bx - ax), -abs(by - ay)
        sx, sy = (1 if bx > ax else -1), (1 if by > ay else -1)
        err = dx + dy
        buf = self.buf
        steps = max(dx, -dy) + 1
        self.cost += steps
        if steps > 4 * (W + H):       # a line far off the canvas: clip it to a sane length first
            return
        f = a / 255.0
        for _ in range(steps):
            if 0 <= ax < W and 0 <= ay < H:
                i = ay * W + ax
                if painter is not None:
                    buf[i] = painter.row(ay)[ax]
                elif self.blend != BLEND:
                    buf[i] = self.table(g, a)[buf[i]]
                elif a >= 255:
                    buf[i] = g
                else:
                    v = buf[i]
                    buf[i] = int(v + (g - v) * f + 0.5)
            if ax == bx and ay == by:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                ax += sx
            if e2 <= dx:
                err += dx
                ay += sy

    # ------------------------------------------------------------------ rectangles
    def fill_rect(self, x0: float, y0: float, x1: float, y1: float, g: int, a: int) -> None:
        """An axis-aligned rectangle in device space, exact area coverage."""
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        x0, x1 = max(x0, 0.0), min(x1, float(W))
        y0, y1 = max(y0, 0.0), min(y1, float(H))
        if x1 <= x0 or y1 <= y0 or a <= 0:
            return
        if not self.aa:
            # crisp: corners snap to floor(v + 0.25) + 0.25 (Java2D/Marlin), then the pixels whose
            # centres fall in [start, end)
            def first(v: float) -> int:
                q = math.floor(v + 0.25) + 0.25
                return int(math.ceil(q - 0.5))
            xa, xb = max(0, first(x0)), min(W, first(x1))
            for y in range(max(0, first(y0)), min(H, first(y1))):
                self.span(y, xa, xb, g, a)
            return
        af = a / 255.0
        ix0, ix1 = int(x0), int(math.ceil(x1))
        full0, full1 = int(math.ceil(x0)), int(x1)       # fully covered columns [full0, full1)
        for y in range(int(y0), int(math.ceil(y1))):
            vy = min(y1, y + 1.0) - max(y0, float(y))
            if vy <= 0:
                continue
            base = y * W
            f = vy * af
            if ix1 - ix0 == 1:
                self.pixel(base + ix0, g, (x1 - x0) * f)
                continue
            if full0 > ix0:
                self.pixel(base + ix0, g, (full0 - x0) * f)
            if vy >= 0.999999:
                self.span(y, full0, full1, g, a)
            else:
                for x in range(full0, full1):
                    self.pixel(base + x, g, f)
                self.cost += (full1 - full0) >> 2
            if ix1 > full1:
                self.pixel(base + full1, g, (x1 - full1) * f)

    # ------------------------------------------------------------------ contours
    def fill(self, contours: list, g: int, a: int, painter=None) -> None:
        """Contours (lists of (x, y) in device space, closed implicitly), non-zero winding.
        With a `painter` (textures.Painter) every pixel at least half covered takes the texture's
        pure ink or paper instead of grey g -- crisp 1-bit, whatever the anti-aliasing."""
        if a <= 0:
            return
        edges = []
        ymin, ymax = float(H), 0.0
        for pts in contours:
            n = len(pts)
            if n < 2:
                continue
            px, py = pts[-1]
            for qx, qy in pts:
                if qy != py and qy == qy and py == py:
                    if py < qy:
                        edges.append((py, qy, px, (qx - px) / (qy - py), 1))
                    else:
                        edges.append((qy, py, qx, (px - qx) / (py - qy), -1))
                    lo, hi = (py, qy) if py < qy else (qy, py)
                    if lo < ymin:
                        ymin = lo
                    if hi > ymax:
                        ymax = hi
                px, py = qx, qy
        if not edges:
            return
        ystart = max(0, int(math.floor(ymin)))
        yend = min(H, int(math.ceil(ymax)))
        if ystart >= yend:
            return
        edges.sort()
        self.cost += len(edges) + (yend - ystart) * SUB
        af = a / 255.0
        aa = self.aa
        buf = self.buf
        active: list = []
        k, ne = 0, len(edges)
        for y in range(ystart, yend):
            ytop, ybot = float(y), y + 1.0
            # edges that start before this row ends join; ones that ended leave
            while k < ne and edges[k][0] < ybot:
                active.append(edges[k])
                k += 1
            if active:
                active = [e for e in active if e[1] > ytop]
            if not active:
                if k >= ne:
                    break
                continue
            if not aa or painter is not None:
                spans = _spans(active, y + 0.5)
                trow = painter.row(y) if painter is not None else None
                for xa, xb in spans:
                    # the pixels whose centres lie in [xa, xb) (a centre on the left edge is in)
                    ia, ib = max(0, int(math.ceil(xa - 0.5))), min(W, int(math.ceil(xb - 0.5)))
                    if ib <= ia:
                        continue
                    if trow is not None:
                        buf[y * W + ia:y * W + ib] = trow[ia:ib]
                        self.cost += (ib - ia) >> 3
                    else:
                        self.span(y, ia, ib, g, a)
                continue
            # coverage of this row: `full` counts sub-rows that cover a pixel completely (as a
            # difference array), `part` the fractional ends
            sub_spans = [_spans(active, ytop + sy) for sy in _SUBY]
            lo, hi = W, 0
            for sp in sub_spans:
                if sp:
                    if sp[0][0] < lo:
                        lo = sp[0][0]
                    if sp[-1][1] > hi:
                        hi = sp[-1][1]
            if hi <= lo:
                continue
            c0 = max(0, int(lo))
            c1 = min(W, int(math.ceil(hi)))
            if c1 <= c0:
                continue
            width = c1 - c0
            diff = [0] * (width + 1)
            part = [0.0] * width
            for sp in sub_spans:
                for xa, xb in sp:
                    if xa < 0.0:
                        xa = 0.0
                    if xb > W:
                        xb = float(W)
                    if xb <= xa:
                        continue
                    fa, fb = int(xa), int(xb)
                    if fa == fb:
                        part[fa - c0] += (xb - xa) * _Q
                        continue
                    ca = int(math.ceil(xa))
                    if ca > fa:
                        part[fa - c0] += (ca - xa) * _Q
                    if fb > ca:
                        diff[ca - c0] += 1
                        diff[fb - c0] -= 1
                    if xb > fb and fb < W:
                        part[fb - c0] += (xb - fb) * _Q
            base = y * W
            run = 0
            x = 0
            full_start = -1
            while x < width:
                run += diff[x]
                if run == SUB and part[x] == 0.0:
                    if full_start < 0:
                        full_start = x
                    x += 1
                    continue
                if full_start >= 0:
                    self.span(y, c0 + full_start, c0 + x, g, a)
                    full_start = -1
                cov = run * _Q + part[x]
                if cov > 0.0:
                    if cov > 1.0:
                        cov = 1.0
                    i = base + c0 + x
                    f = cov * af
                    if self.blend != BLEND:
                        buf[i] = self.table(g, int(f * 255 + 0.5))[buf[i]]
                    elif f >= 0.999:
                        buf[i] = g
                    else:
                        v = buf[i]
                        buf[i] = int(v + (g - v) * f + 0.5)
                x += 1
            if full_start >= 0:
                self.span(y, c0 + full_start, c0 + width, g, a)
            self.cost += width >> 1


def _spans(active: list, sy: float) -> list:
    """Non-zero spans [xa, xb) where the horizontal line y = sy is inside the edges."""
    xs = []
    for y0, y1, x0, dx, d in active:
        if y0 <= sy < y1:
            xs.append((x0 + (sy - y0) * dx, d))
    if not xs:
        return []
    xs.sort()
    out = []
    wind = 0
    start = 0.0
    for x, d in xs:
        before = wind
        wind += d
        if before == 0 and wind != 0:
            start = x
        elif before != 0 and wind == 0:
            if x > start:
                if out and out[-1][1] >= start:
                    out[-1] = (out[-1][0], x)
                else:
                    out.append((start, x))
    return out


# ---------------------------------------------------------------------- geometry helpers
def ellipse_points(cx: float, cy: float, rx: float, ry: float, n: int, a0: float = 0.0,
                   a1: float = 2 * math.pi, closed_full: bool = True) -> list:
    """Points on an ellipse from angle a0 to a1 (y down, as Processing draws)."""
    if closed_full:
        return [(cx + rx * math.cos(a0 + (a1 - a0) * i / n), cy + ry * math.sin(a0 + (a1 - a0) * i / n))
                for i in range(n)]
    return [(cx + rx * math.cos(a0 + (a1 - a0) * i / n), cy + ry * math.sin(a0 + (a1 - a0) * i / n))
            for i in range(n + 1)]


def segments_for(r_device: float, sweep: float = 2 * math.pi) -> int:
    """How many straight pieces make a round thing of this device radius look round."""
    n = int(math.ceil(abs(sweep) * max(r_device, 0.5) / 1.2))
    full = max(8, min(160, n))
    return max(2, int(math.ceil(full * abs(sweep) / (2 * math.pi)))) if sweep < 2 * math.pi - 1e-9 else full


def ccw(pts: list) -> list:
    """The same polygon, counter-clockwise in y-down space (positive shoelace area), so pieces of
    one stroke never cancel each other under the non-zero rule."""
    s = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return pts if s >= 0 else pts[::-1]


ROUND, SQUARE, PROJECT, MITER, BEVEL = 2, 1, 4, 8, 32   # PConstants values
MITER_LIMIT = 10.0                                         # java.awt.BasicStroke's default


def _cap_pts(x: float, y: float, ux: float, uy: float, hw: float, n: int) -> list:
    """A half disc at (x, y) bulging in direction (ux, uy)."""
    base = math.atan2(uy, ux)
    return [(x + hw * math.cos(base - math.pi / 2 + math.pi * i / n),
             y + hw * math.sin(base - math.pi / 2 + math.pi * i / n)) for i in range(n + 1)]


def stroke_polyline(pts: list, w: float, closed: bool, cap: int, join: int, scale: float = 1.0) -> list:
    """Contours (user space) covering a stroke of width w along pts. `scale` is the matrix's size
    factor, only used to choose how finely round caps and joins are cut."""
    hw = w / 2.0
    if hw <= 0:
        return []
    # drop repeated points
    clean = []
    for p in pts:
        if not clean or abs(p[0] - clean[-1][0]) > 1e-9 or abs(p[1] - clean[-1][1]) > 1e-9:
            clean.append(p)
    if closed and len(clean) > 2 and abs(clean[0][0] - clean[-1][0]) < 1e-9 and abs(clean[0][1] - clean[-1][1]) < 1e-9:
        clean.pop()
    nround = max(4, min(32, int(math.ceil(hw * scale * 2.5))))
    if len(clean) == 1 or (len(clean) == 2 and closed and False):
        x, y = clean[0]
        if cap == ROUND:
            return [ellipse_points(x, y, hw, hw, 2 * nround)]
        if cap == PROJECT:
            return [[(x - hw, y - hw), (x + hw, y - hw), (x + hw, y + hw), (x - hw, y + hw)]]
        return []
    out = []
    n = len(clean)
    segs = n if closed else n - 1
    dirs = []
    for i in range(segs):
        x0, y0 = clean[i]
        x1, y1 = clean[(i + 1) % n]
        dx, dy = x1 - x0, y1 - y0
        ln = math.hypot(dx, dy)
        dirs.append((dx / ln, dy / ln) if ln > 0 else (1.0, 0.0))
    for i in range(segs):
        x0, y0 = clean[i]
        x1, y1 = clean[(i + 1) % n]
        ux, uy = dirs[i]
        nx, ny = -uy * hw, ux * hw
        ex0, ey0, ex1, ey1 = x0, y0, x1, y1
        if not closed and cap == PROJECT:
            if i == 0:
                ex0, ey0 = x0 - ux * hw, y0 - uy * hw
            if i == segs - 1:
                ex1, ey1 = x1 + ux * hw, y1 + uy * hw
        out.append(ccw([(ex0 + nx, ey0 + ny), (ex1 + nx, ey1 + ny), (ex1 - nx, ey1 - ny), (ex0 - nx, ey0 - ny)]))
    # joins
    for j in range(n if closed else n - 2):
        vi = (j + 1) % n if not closed else j
        i_in = (vi - 1) % segs if closed else j
        i_out = vi % segs if closed else j + 1
        ax, ay = dirs[i_in]
        bx, by = dirs[i_out]
        cross = ax * by - ay * bx
        if abs(cross) < 1e-9 and ax * bx + ay * by > 0:
            continue          # straight on
        vx, vy = clean[vi]
        if join == ROUND:
            out.append(ellipse_points(vx, vy, hw, hw, 2 * nround))
            continue
        # the outer side of the turn
        s = 1.0 if cross > 0 else -1.0
        p1 = (vx + ay * hw * s, vy - ax * hw * s)
        p2 = (vx + by * hw * s, vy - bx * hw * s)
        wedge = [(vx, vy), p1]
        if join == MITER:
            cos_t = ax * bx + ay * by
            half = math.sqrt(max(0.0, (1.0 + cos_t) / 2.0))     # cos of half the turn's inner angle
            if half > 1e-6 and 1.0 / half <= MITER_LIMIT:
                mx, my = (ay * s + by * s), (-ax * s - bx * s)
                ml = math.hypot(mx, my)
                if ml > 1e-9:
                    d = hw / half
                    wedge.append((vx + mx / ml * d, vy + my / ml * d))
        wedge.append(p2)
        out.append(ccw(wedge))
    if not closed and cap == ROUND:
        x0, y0 = clean[0]
        ux, uy = dirs[0]
        out.append(ccw(_cap_pts(x0, y0, -ux, -uy, hw, nround)))
        x1, y1 = clean[-1]
        ux, uy = dirs[-1]
        out.append(ccw(_cap_pts(x1, y1, ux, uy, hw, nround)))
    return out


def ring(outer: list, inner: list) -> list:
    """A band between two closed outlines (the stroke of a convex shape): outer CCW, inner CW."""
    o = ccw(outer)
    i = ccw(inner)[::-1]
    return [o, i]


class TwinCanvas(Canvas):
    """An offscreen surface (createGraphics): starts transparent. `buf` is the grey drawn over
    paper (which is premultiplied ink: the same arithmetic as ink over white), `cov` the coverage
    painted in black at the same opacities -- 255 - cov is the alpha. image() composites
    ink = ink_under * (1 - alpha) + ink_surface (source-over in ink)."""
    __slots__ = ("cov", "inner")

    def __init__(self) -> None:
        super().__init__(255)
        self.cov = bytearray([255]) * (W * H)
        self.inner = False

    def _twice(self, fn, args_colour, args_cover) -> None:
        if self.inner:
            fn(self, *args_colour)
            return
        self.inner = True
        try:
            fn(self, *args_colour)
            self.buf, self.cov = self.cov, self.buf
            try:
                fn(self, *args_cover)
            finally:
                self.buf, self.cov = self.cov, self.buf
        finally:
            self.inner = False

    def clear(self, g: int, a: int = 255) -> None:
        self._twice(Canvas.clear, (g, a), (0, a))

    def clear_transparent(self) -> None:
        self.buf[:] = bytes([255]) * (W * H)
        self.cov[:] = bytes([255]) * (W * H)

    def span(self, y, x0, x1, g, a) -> None:
        self._twice(Canvas.span, (y, x0, x1, g, a), (y, x0, x1, 0, a))

    def pixel(self, i, g, cov) -> None:
        self._twice(Canvas.pixel, (i, g, cov), (i, 0, cov))

    def thin_line(self, x0, y0, x1, y1, g, a, painter=None) -> None:
        self._twice(Canvas.thin_line, (x0, y0, x1, y1, g, a, painter), (x0, y0, x1, y1, 0, 255 if painter else a, None))

    def fill_rect(self, x0, y0, x1, y1, g, a) -> None:
        self._twice(Canvas.fill_rect, (x0, y0, x1, y1, g, a), (x0, y0, x1, y1, 0, a))

    def fill(self, contours, g, a, painter=None) -> None:
        self._twice(Canvas.fill, (contours, g, a, painter), (contours, 0, 255 if painter else a, None))

    def opaque(self, i: int) -> None:
        """Pixel i was written directly (set(), pixels[]): it is opaque now."""
        self.cov[i] = 0
