"""Processing's drawing API on the 240x128 grey canvas: style, colour, matrix, shapes, text, pixels.

SPDX-License-Identifier: MIT AND LGPL-2.1-only
MIT, except the P3D methods of Api (camera, perspective, ortho, frustum, the lights and materials,
box, sphere, normal, and the 3D paths of the shape calls), which follow Processing's
PGraphicsOpenGL (Processing 4.5.2, GNU LGPL 2.1). See THIRD-PARTY.md.

Everything a sketch can reach is a method here with plain Python arguments; the compiler
(compiler.py) decides which method a call means from the argument count and static types.
Semantics follow PGraphics / PGraphicsJava2D (Processing 4.5.2) where they change pixels:
colour arithmetic (PGraphics.colorCalc, with its float truncations), rect/ellipse modes, arc's
pie fill with an open outline, Java2D's non-zero fill, round caps and miter joins, the default font.
"""
from __future__ import annotations

import math
import time
from array import array

from . import font_data, raster, textures
from . import three as T3
from .errors import PdeError
from .jrandom import Randomness, f32

W, H = raster.W, raster.H

# PConstants (the values sketches may compute with)
CONST = {
    "PI": ("float", f32(math.pi)), "HALF_PI": ("float", f32(math.pi / 2)), "QUARTER_PI": ("float", f32(math.pi / 4)),
    "TWO_PI": ("float", f32(2 * math.pi)), "TAU": ("float", f32(2 * math.pi)),
    "DEG_TO_RAD": ("float", f32(f32(math.pi) / 180)), "RAD_TO_DEG": ("float", f32(180 / f32(math.pi))),
    "CORNER": ("int", 0), "CORNERS": ("int", 1), "RADIUS": ("int", 2), "CENTER": ("int", 3), "DIAMETER": ("int", 3),
    "BASELINE": ("int", 0), "TOP": ("int", 101), "BOTTOM": ("int", 102),
    "LEFT": ("int", 37), "UP": ("int", 38), "RIGHT": ("int", 39), "DOWN": ("int", 40),
    "OPEN": ("int", 1), "CLOSE": ("int", 2), "CHORD": ("int", 2), "PIE": ("int", 3),
    "SQUARE": ("int", 1), "ROUND": ("int", 2), "PROJECT": ("int", 4), "MITER": ("int", 8), "BEVEL": ("int", 32),
    "RGB": ("int", 1), "ARGB": ("int", 2), "HSB": ("int", 3), "ALPHA": ("int", 4),
    "POINTS": ("int", 3), "LINES": ("int", 5), "TRIANGLES": ("int", 9), "TRIANGLE_STRIP": ("int", 10),
    "TRIANGLE_FAN": ("int", 11), "QUADS": ("int", 17), "QUAD_STRIP": ("int", 18), "POLYGON": ("int", 20),
    "REPLACE": ("int", 0), "BLEND": ("int", 1), "ADD": ("int", 2), "SUBTRACT": ("int", 4), "LIGHTEST": ("int", 8),
    "DARKEST": ("int", 16), "DIFFERENCE": ("int", 32), "EXCLUSION": ("int", 64), "MULTIPLY": ("int", 128),
    "SCREEN": ("int", 256),
    "JAVA2D": ("String", "processing.awt.PGraphicsJava2D"), "P2D": ("String", "processing.opengl.PGraphics2D"),
    "P3D": ("String", "processing.opengl.PGraphics3D"),
    "THRESHOLD": ("int", 16), "GRAY": ("int", 12), "INVERT": ("int", 13), "POSTERIZE": ("int", 15),
    "BLUR": ("int", 11), "ERODE": ("int", 17), "DILATE": ("int", 18), "OPAQUE": ("int", 14),
    # hint() (PConstants): the depth test and mask are honoured in P3D, the rest are accepted
    "DISABLE_DEPTH_TEST": ("int", 2), "ENABLE_DEPTH_TEST": ("int", -2), "ENABLE_DEPTH_SORT": ("int", 3),
    "DISABLE_DEPTH_SORT": ("int", -3), "DISABLE_DEPTH_MASK": ("int", 5), "ENABLE_DEPTH_MASK": ("int", -5),
    "DISABLE_OPTIMIZED_STROKE": ("int", 6), "ENABLE_OPTIMIZED_STROKE": ("int", -6),
    "ENABLE_STROKE_PERSPECTIVE": ("int", 7), "DISABLE_STROKE_PERSPECTIVE": ("int", -7),
    "LINE_STRIP": ("int", 50), "LINE_LOOP": ("int", 51),
}
P3D_NAME = "processing.opengl.PGraphics3D"


THIN_SEG_MAX = 4 * (W + H)     # steps per quarter of a noSmooth ellipse outline, at most
ARC_FOLD = 64 * 2 * math.pi    # arc(): a start below -ARC_FOLD is folded, not stepped up by TWO_PI


class Budget:
    """Steps and wall clock for one frame (or setup). Loops and calls tick it; drawing charges it."""

    __slots__ = ("steps", "next", "limit", "deadline", "clock", "alloc", "alloc_limit", "depth")

    def __init__(self, limit: int, seconds: float, alloc_limit: int, clock=time.monotonic) -> None:
        self.clock = clock
        self.limit = limit
        self.alloc_limit = alloc_limit
        self.reset(seconds)

    def reset(self, seconds: float) -> None:
        self.steps = 0
        self.next = 4096
        self.deadline = self.clock() + seconds
        self.alloc = 0
        self.depth = 0

    def check(self) -> None:
        if self.steps > self.limit:
            raise PdeError("budget", f"the frame took more than {self.limit:,} steps")
        if self.clock() > self.deadline:
            raise PdeError("deadline", "the frame ran out of time")
        self.next = self.steps + 4096

    def charge(self, n: int) -> None:
        self.steps += n
        if self.steps >= self.next:
            self.check()

    def allocate(self, nbytes: int) -> None:
        self.alloc += nbytes
        if self.alloc > self.alloc_limit:
            raise PdeError("memory", f"the frame allocated more than {self.alloc_limit >> 20} MB")
        self.steps += nbytes >> 6
        if self.steps >= self.next:
            self.check()


class PImg:
    """A PImage: createImage(), get(), or loadPixels()/pixels[] of one. ARGB ints, width x height."""
    __slots__ = ("w", "h", "fmt", "pixels")

    def __init__(self, w: int, h: int, fmt: int = 2, pixels=None) -> None:
        self.w, self.h, self.fmt = w, h, fmt
        self.pixels = pixels if pixels is not None else array("i", bytes(4 * w * h))


class PGr:
    """A PGraphics from createGraphics(w, h): its own drawing state (an Api) on a transparent
    240x128 surface of which the first w x h pixels are the image."""
    __slots__ = ("w", "h", "api")

    def __init__(self, w: int, h: int, api) -> None:
        self.w, self.h, self.api = w, h, api


class PFont:
    """A font object: only its size matters (every font is Processing's default font here)."""
    __slots__ = ("size",)

    def __init__(self, size: float) -> None:
        self.size = size


class Style:
    __slots__ = ("fill", "fill_g", "fill_a", "fill_c", "stroke", "stroke_g", "stroke_a", "stroke_c", "sw", "cap",
                 "join", "rect_mode", "ellipse_mode", "text_size", "align_x", "align_y", "leading", "cmode",
                 "cmax", "tint", "tex", "tex_p", "tex_g2", "stex", "stex_p", "stex_g2",
                 "amb_c", "spec_c", "emis_c", "shine", "blend", "image_mode")

    def __init__(self) -> None:
        self.fill, self.fill_g, self.fill_a, self.fill_c = True, 255, 255, -1
        self.stroke, self.stroke_g, self.stroke_a, self.stroke_c = True, 0, 255, -16777216
        self.sw, self.cap, self.join = 1.0, raster.ROUND, raster.MITER
        self.rect_mode, self.ellipse_mode = 0, 3
        self.text_size, self.align_x, self.align_y, self.leading = 12.0, 37, 0, None
        self.cmode, self.cmax = 1, (255.0, 255.0, 255.0, 255.0)
        self.tex, self.tex_p, self.tex_g2 = None, 6, 255     # the texture extension
        self.stex, self.stex_p, self.stex_g2 = None, 6, 255
        # P3D materials (PGraphicsOpenGL.defaultSettings): ambient follows the fill until ambient()
        self.amb_c, self.spec_c, self.emis_c, self.shine = None, -8553091, -16777216, 1.0
        self.blend, self.image_mode = 1, 0

    def copy(self) -> "Style":
        s = Style.__new__(Style)
        for k in Style.__slots__:
            if k != "tint":
                setattr(s, k, getattr(self, k))
        return s


def _jint(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v >= 1 << 31 else v


def luma(argb: int) -> int:
    r, g, b = (argb >> 16) & 255, (argb >> 8) & 255, argb & 255
    return (r * 299 + g * 587 + b * 114) // 1000


class Api:
    def __init__(self, budget: Budget, now=time.time) -> None:
        self.budget = budget
        self.now = now
        self.canvas = raster.Canvas(204)   # what a primary surface starts as (the oracle does the same)
        # In this dialect edges are CRISP by default (anti-aliased edges dither badly on 1-bit
        # displays); smooth() opts in to anti-aliasing, noSmooth() is then a no-op.
        # Text stays anti-aliased either way, as in Processing's JAVA2D.
        self.canvas.aa = False
        self.rand = Randomness()
        self.frame_count = 0
        self.looping = True
        self.redraw = False
        self.exited = False
        self.m = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
        self.mstack: list = []
        self.st = Style()
        self.sstack: list = []
        self.shape: list | None = None
        self.shape_kind = 20
        self.contours: list = []
        self.pixels: array | None = None
        self.curve_tightness = 0.0
        self.tex_seed: int | None = None      # textureSeed(); else the last randomSeed()
        self.last_rseed = 0
        self.tex_cache: dict = {}
        # P3D (three.py): None for JAVA2D. The matrix is `m` (2D affine) plus `mz` (z scale) while
        # every transform is 2D -- the "flat" state, where 2D shapes keep the 2D renderer exactly --
        # and becomes `m3` (4x4) at the first 3D transform.
        self.p3d = False
        self.t3: T3.Three | None = None
        self.mz = 1.0
        self.m3: list | None = None

    # ================================================================== frame bookkeeping
    def _cost(self) -> None:
        c = self.canvas.cost
        if c:
            self.canvas.cost = 0
            self.budget.charge(c)

    def size(self, w, h, renderer=None, *_):
        if int(w) != W or int(h) != H:
            raise PdeError("size", f"size({int(w)}, {int(h)}) is not size(240, 128)")
        if renderer == P3D_NAME and not self.p3d:
            self.p3d = True
            self.t3 = T3.Three(self)

    def no_loop(self):
        self.looping = False

    def loop(self):
        self.looping = True

    def do_redraw(self):
        self.redraw = True

    def exit(self):
        self.exited = True
        self.looping = False

    def smooth(self, *_):
        self.canvas.aa = True

    def no_smooth(self):
        self.canvas.aa = False

    def millis(self) -> int:
        return int(self.frame_count * 1000 / 60)   # deterministic: frames at 60 fps

    def clock(self, what: str) -> int:
        t = time.localtime(self.now())
        return {"second": t.tm_sec, "minute": t.tm_min, "hour": t.tm_hour, "day": t.tm_mday, "month": t.tm_mon,
                "year": t.tm_year}[what]

    # ================================================================== colour
    def color_mode(self, mode, *maxes):
        mode = int(mode)
        if mode not in (1, 3):
            mode = 1
        m = list(self.st.cmax)
        if len(maxes) == 1:
            m = [f32(maxes[0])] * 4
        elif len(maxes) == 3:
            m[:3] = [f32(v) for v in maxes]
        elif len(maxes) >= 4:
            m = [f32(v) for v in maxes[:4]]
        self.st.cmode, self.st.cmax = mode, tuple(m)

    def _calc(self, args: tuple, ints: tuple) -> int:
        """PGraphics.colorCalc: the ARGB colour of fill/stroke/background/color arguments."""
        mx, my, mz, ma = self.st.cmax
        n = len(args)
        if n in (1, 2) and ints[0]:
            rgb = int(args[0])
            if (rgb & 0xFF000000) == 0 and rgb <= mx:
                return self._grey(float(rgb), float(args[1]) if n == 2 else ma)
            if n == 1 or float(args[1]) == ma:
                return _jint(rgb)
            a = int(((rgb >> 24) & 255) * max(0.0, min(1.0, f32(float(args[1]) / ma))))
            return _jint((a << 24) | (rgb & 0xFFFFFF))
        if n in (1, 2):
            return self._grey(float(args[0]), float(args[1]) if n == 2 else ma)
        if n in (3, 4):
            return self._three(float(args[0]), float(args[1]), float(args[2]), float(args[3]) if n == 4 else ma)
        raise PdeError("runtime", "a colour takes 1 to 4 numbers")

    def _grey(self, g: float, a: float) -> int:
        mx, _, _, ma = self.st.cmax
        g = min(max(g, 0.0), mx)
        a = min(max(a, 0.0), ma)
        gi = int(f32(f32(g / mx) * 255)) if mx else 0
        ai = int(f32(f32(a / ma) * 255)) if ma else 0
        return _jint((ai << 24) | (gi << 16) | (gi << 8) | gi)

    def _three(self, x: float, y: float, z: float, a: float) -> int:
        mx, my, mz, ma = self.st.cmax
        x, y, z, a = min(max(x, 0.0), mx), min(max(y, 0.0), my), min(max(z, 0.0), mz), min(max(a, 0.0), ma)
        if self.st.cmode == 3:
            hue, sat, bri = f32(x / mx), f32(y / my), f32(z / mz)
            if sat == 0:
                r = g = b = bri
            else:
                which = f32((hue - int(hue)) * 6.0)
                f = f32(which - int(which))
                p = f32(bri * f32(1.0 - sat))
                q = f32(bri * f32(1.0 - f32(sat * f)))
                t = f32(bri * f32(1.0 - f32(sat * f32(1.0 - f))))
                r, g, b = ((bri, t, p), (q, bri, p), (p, bri, t), (p, q, bri), (t, p, bri), (bri, p, q))[int(which) % 6]
        else:
            r, g, b = f32(x / mx), f32(y / my), f32(z / mz)
        ri, gi, bi = int(f32(r * 255)), int(f32(g * 255)), int(f32(b * 255))
        ai = int(f32(f32(a / ma) * 255))
        return _jint((ai << 24) | (ri << 16) | (gi << 8) | bi)

    def color(self, args, ints) -> int:
        return self._calc(args, ints)

    def fill(self, args, ints):
        c = self._calc(args, ints)
        s = self.st
        s.fill, s.fill_c, s.fill_g, s.fill_a = True, c, luma(c), (c >> 24) & 255

    def no_fill(self):
        self.st.fill = False

    def stroke(self, args, ints):
        c = self._calc(args, ints)
        s = self.st
        s.stroke, s.stroke_c, s.stroke_g, s.stroke_a = True, c, luma(c), (c >> 24) & 255

    def no_stroke(self):
        self.st.stroke = False

    def background(self, args, ints):
        c = self._calc(args, ints)
        a = (c >> 24) & 255
        g = luma(c)
        if a < 255:
            # an offscreen surface keeps the transparency; the oracle reads it over paper
            g = (g * a + 255 * (255 - a)) // 255
        self.canvas.clear(g)
        self._cost()
        if self.t3 is not None:
            self.t3.clear_depth()

    def clear(self):
        self.canvas.clear(255)
        if self.t3 is not None:
            self.t3.clear_depth()

    def stroke_weight(self, w):
        self.st.sw = max(0.0, float(w))

    def stroke_cap(self, c):
        self.st.cap = int(c) if int(c) in (1, 2, 4) else 2

    def stroke_join(self, j):
        self.st.join = int(j) if int(j) in (2, 8, 32) else 8

    def rect_mode(self, m):
        self.st.rect_mode = int(m)

    def ellipse_mode(self, m):
        self.st.ellipse_mode = int(m)

    @staticmethod
    def red(c) -> float:
        return float((int(c) >> 16) & 255)

    @staticmethod
    def green(c) -> float:
        return float((int(c) >> 8) & 255)

    @staticmethod
    def blue(c) -> float:
        return float(int(c) & 255)

    @staticmethod
    def alpha(c) -> float:
        return float((int(c) >> 24) & 255)

    def brightness(self, c) -> float:
        c = int(c)
        return f32(max((c >> 16) & 255, (c >> 8) & 255, c & 255) / 255.0 * self.st.cmax[2])

    def lerp_color(self, c1, c2, t) -> int:
        t = min(max(float(t), 0.0), 1.0)
        c1, c2 = int(c1), int(c2)
        out = 0
        for sh in (24, 16, 8, 0):
            a, b = (c1 >> sh) & 255, (c2 >> sh) & 255
            out |= (int(a + (b - a) * t + 0.5) & 255) << sh    # PGraphics.lerpColor rounds
        return _jint(out)

    # ================================================================== matrix
    def push_matrix(self):
        if len(self.mstack) >= 32:
            raise PdeError("runtime", "too many calls to pushMatrix()")
        if self.p3d:
            self.mstack.append((list(self.m), self.mz, list(self.m3) if self.m3 is not None else None))
        else:
            self.mstack.append(list(self.m))

    def pop_matrix(self):
        if not self.mstack:
            raise PdeError("runtime", "too many calls to popMatrix(), and not enough to pushMatrix()")
        top = self.mstack.pop()
        if type(top) is tuple:
            self.m, self.mz, self.m3 = top
        else:
            self.m = top

    def push_style(self):
        if len(self.sstack) >= 64:
            raise PdeError("runtime", "too many calls to pushStyle()")
        self.sstack.append(self.st.copy())

    def pop_style(self):
        if not self.sstack:
            raise PdeError("runtime", "too many calls to popStyle(), and not enough to pushStyle()")
        self.st = self.sstack.pop()
        self.canvas.blend = self.st.blend

    def push(self):
        self.push_matrix()
        self.push_style()

    def pop(self):
        self.pop_matrix()
        self.pop_style()

    def reset_matrix(self):
        self.m = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
        self.mz = 1.0
        self.m3 = None

    def translate(self, x, y, z=0.0):
        if self.m3 is not None or (self.p3d and z != 0.0):
            self._apply3((1.0, 0.0, 0.0, x, 0.0, 1.0, 0.0, y, 0.0, 0.0, 1.0, z, 0.0, 0.0, 0.0, 1.0))
            return
        a, b, c, d, e, f = self.m
        self.m[4] = e + a * x + c * y
        self.m[5] = f + b * x + d * y

    def rotate(self, t):
        if self.m3 is not None:
            co, si = math.cos(t), math.sin(t)
            self._apply3((co, -si, 0.0, 0.0, si, co, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0))
            return
        a, b, c, d, e, f = self.m
        co, si = math.cos(t), math.sin(t)
        self.m[0], self.m[1] = a * co + c * si, b * co + d * si
        self.m[2], self.m[3] = c * co - a * si, d * co - b * si

    def scale(self, sx, sy=None, sz=None):
        if self.p3d:
            z = sx if sy is None else (1.0 if sz is None else sz)
            if self.m3 is not None:
                y = sx if sy is None else sy
                self._apply3((sx, 0.0, 0.0, 0.0, 0.0, y, 0.0, 0.0, 0.0, 0.0, z, 0.0, 0.0, 0.0, 0.0, 1.0))
                return
            self.mz *= z
        sy = sx if sy is None else sy
        self.m[0] *= sx
        self.m[1] *= sx
        self.m[2] *= sy
        self.m[3] *= sy

    def shear_x(self, t):
        k = math.tan(t)
        if self.m3 is not None:
            self._apply3((1.0, k, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0))
            return
        self.m[2] += self.m[0] * k
        self.m[3] += self.m[1] * k

    def shear_y(self, t):
        k = math.tan(t)
        if self.m3 is not None:
            self._apply3((1.0, 0.0, 0.0, 0.0, k, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0))
            return
        self.m[0] += self.m[2] * k
        self.m[1] += self.m[3] * k

    # ---------------------------------------------------------------- 3D matrix (P3D)
    def _need3d(self, what: str):
        if not self.p3d:
            raise PdeError("runtime", f"{what}() needs a 3D sketch: size(240, 128, P3D)")

    def _embed(self) -> list:
        a, b, c, d, e, f = self.m
        return [a, c, 0.0, e, b, d, 0.0, f, 0.0, 0.0, self.mz, 0.0, 0.0, 0.0, 0.0, 1.0]

    def _apply3(self, t) -> None:
        """M = M * t (PMatrix3D.apply), leaving the flat state first."""
        if self.m3 is None:
            self.m3 = self._embed()
        self.m3 = T3.mmul(self.m3, t)

    def _mv(self) -> list:
        return T3.mmul(self.t3.cam, self.m3 if self.m3 is not None else self._embed())

    def _use3d(self) -> bool:
        """Whether a 2D shape goes through the 3D pipeline: any 3D transform, a moved camera or
        projection, lights on (they shade 2D shapes too), or 3D drawing already in the depth buffer
        this frame (then it is depth-tested like Processing's). Otherwise it is drawn exactly as a
        JAVA2D sketch draws it."""
        t = self.t3
        return t is not None and (self.m3 is not None or not t.cam_default or not t.proj_default
                                  or t.lighting or (t.dirty and t.depth_test))

    def rotate_x(self, t):
        self._need3d("rotateX")
        co, si = math.cos(t), math.sin(t)
        self._apply3((1.0, 0.0, 0.0, 0.0, 0.0, co, -si, 0.0, 0.0, si, co, 0.0, 0.0, 0.0, 0.0, 1.0))

    def rotate_y(self, t):
        self._need3d("rotateY")
        co, si = math.cos(t), math.sin(t)
        self._apply3((co, 0.0, si, 0.0, 0.0, 1.0, 0.0, 0.0, -si, 0.0, co, 0.0, 0.0, 0.0, 0.0, 1.0))

    def rotate_axis(self, t, x, y, z):
        """rotate(angle, x, y, z) (PMatrix3D.rotate)."""
        self._need3d("rotate")
        n2 = x * x + y * y + z * z
        if n2 < 1e-4:
            return
        if abs(n2 - 1.0) > 1e-4:
            n = math.sqrt(n2)
            x, y, z = x / n, y / n, z / n
        c, s = math.cos(t), math.sin(t)
        tt = 1.0 - c
        self._apply3((tt * x * x + c, tt * x * y - s * z, tt * x * z + s * y, 0.0,
                      tt * x * y + s * z, tt * y * y + c, tt * y * z - s * x, 0.0,
                      tt * x * z - s * y, tt * y * z + s * x, tt * z * z + c, 0.0,
                      0.0, 0.0, 0.0, 1.0))

    def apply_matrix(self, *v):
        if len(v) == 6:
            n00, n01, n02, n10, n11, n12 = v
            if self.m3 is not None:
                self._apply3((n00, n01, 0.0, n02, n10, n11, 0.0, n12, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0))
                return
            a, b, c, d, e, f = self.m
            self.m = [a * n00 + c * n10, b * n00 + d * n10, a * n01 + c * n11, b * n01 + d * n11,
                      a * n02 + c * n12 + e, b * n02 + d * n12 + f]
            return
        self._need3d("applyMatrix")
        self._apply3(tuple(float(x) for x in v))

    def camera(self, *v):
        """camera() or camera(eyeX, eyeY, eyeZ, centerX, centerY, centerZ, upX, upY, upZ): also resets
        the transforms (modelview = camera)."""
        self._need3d("camera")
        if not v:
            v = (W / 2.0, H / 2.0, T3.CAM_Z, W / 2.0, H / 2.0, 0.0, 0.0, 1.0, 0.0)
        v = [float(x) for x in v]
        self.t3.set_camera(T3.look_at(*v))
        dx, dy, dz = v[0] - v[3], v[1] - v[4], v[2] - v[5]
        self.t3.eye_dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        self.reset_matrix()

    def perspective(self, *v):
        self._need3d("perspective")
        if not v:
            self.t3.set_projection(T3.default_projection(), False)
        else:
            self.t3.set_projection(T3.perspective_m(*[float(x) for x in v]), False)

    def ortho(self, *v):
        self._need3d("ortho")
        if len(v) == 0:
            v = (-W / 2.0, W / 2.0, -H / 2.0, H / 2.0)
        if len(v) == 4:
            v = tuple(v) + (0.0, self._eye_dist() * 10.0)
        self.t3.set_projection(T3.ortho_m(*[float(x) for x in v]), True)

    def _eye_dist(self) -> float:
        """|eye - center| of the last camera() (PGraphicsOpenGL.eyeDist)."""
        return self.t3.eye_dist

    def frustum(self, l, r, b, t, n, f):
        self._need3d("frustum")
        self.t3.set_projection(T3.frustum_m(float(l), float(r), float(b), float(t), float(n), float(f)), False)

    def screen_z(self, x, y, z=0.0):
        self._need3d("screenZ")
        return f32(self.t3.screen(self._mv(), x, y, z)[2])

    # ---------------------------------------------------------------- lights and materials (P3D)
    def _rgbf(self, v1, v2, v3) -> tuple[float, float, float]:
        """PGraphics.colorCalc(v1, v2, v3) as floats 0..1 (calcR, calcG, calcB)."""
        mx, my, mz, _ = self.st.cmax
        x = min(max(float(v1), 0.0), mx) / mx if mx else 0.0
        y = min(max(float(v2), 0.0), my) / my if my else 0.0
        z = min(max(float(v3), 0.0), mz) / mz if mz else 0.0
        if self.st.cmode == 3:
            if y == 0:
                return (z, z, z)
            which = (x - int(x)) * 6.0
            f = which - int(which)
            p = z * (1.0 - y)
            q = z * (1.0 - y * f)
            t = z * (1.0 - (y * (1.0 - f)))
            return ((z, t, p), (q, z, p), (p, z, t), (p, q, z), (t, p, z), (z, p, q))[int(which) % 6]
        return (x, y, z)

    def _light_pos(self, x, y, z) -> tuple[float, float, float]:
        m = self._mv()
        return (m[0] * x + m[1] * y + m[2] * z + m[3], m[4] * x + m[5] * y + m[6] * z + m[7],
                m[8] * x + m[9] * y + m[10] * z + m[11])

    def _light_dir(self, dx, dy, dz) -> tuple[float, float, float]:
        ni = T3.inv3(self._mv())
        if ni is None:
            return (0.0, 0.0, 0.0)
        nx = dx * ni[0] + dy * ni[3] + dz * ni[6]
        ny = dx * ni[1] + dy * ni[4] + dz * ni[7]
        nz = dx * ni[2] + dy * ni[5] + dz * ni[8]
        d = math.sqrt(nx * nx + ny * ny + nz * nz)
        if d > 0.0:
            return (nx / d, ny / d, nz / d)
        return (0.0, 0.0, 0.0)

    def lights(self):
        if not self.p3d:
            return                                  # JAVA2D: Processing only warns
        t = self.t3
        t.lights = []
        t.fall = (1.0, 0.0, 0.0)
        t.lspec = (0.0, 0.0, 0.0)
        t.lighting = True
        # colorMode forced to RGB with the current maxima: half of each
        z3 = (0.0, 0.0, 0.0)
        t.add_light("ambient", self._light_pos(0.0, 0.0, 0.0), 1.0, z3, (0.5, 0.5, 0.5), z3, z3, t.fall, (0.0, 0.0))
        t.add_light("directional", (0.0, 0.0, 0.0), 0.0, self._light_dir(0.0, 0.0, -1.0), z3, (0.5, 0.5, 0.5),
                    t.lspec, (1.0, 0.0, 0.0), (0.0, 0.0))

    def no_lights(self):
        if self.t3 is not None:
            self.t3.lighting = False
            self.t3.lights = []

    def ambient_light(self, r, g, b, x=0.0, y=0.0, z=0.0):
        self._need3d("ambientLight")
        t = self.t3
        z3 = (0.0, 0.0, 0.0)
        t.add_light("ambient", self._light_pos(x, y, z), 1.0, z3, self._rgbf(r, g, b), z3, z3, t.fall, (0.0, 0.0))

    def directional_light(self, r, g, b, dx, dy, dz):
        self._need3d("directionalLight")
        t = self.t3
        z3 = (0.0, 0.0, 0.0)
        t.add_light("directional", (0.0, 0.0, 0.0), 0.0, self._light_dir(dx, dy, dz), z3, self._rgbf(r, g, b),
                    t.lspec, (1.0, 0.0, 0.0), (0.0, 0.0))

    def point_light(self, r, g, b, x, y, z):
        self._need3d("pointLight")
        t = self.t3
        z3 = (0.0, 0.0, 0.0)
        t.add_light("point", self._light_pos(x, y, z), 1.0, z3, z3, self._rgbf(r, g, b), t.lspec, t.fall, (0.0, 0.0))

    def spot_light(self, r, g, b, x, y, z, dx, dy, dz, angle, conc):
        self._need3d("spotLight")
        t = self.t3
        z3 = (0.0, 0.0, 0.0)
        t.add_light("spot", self._light_pos(x, y, z), 1.0, self._light_dir(dx, dy, dz), z3, self._rgbf(r, g, b),
                    t.lspec, t.fall, (max(0.0, math.cos(angle)), float(conc)))

    def light_falloff(self, c, l, q):
        self._need3d("lightFalloff")
        self.t3.fall = (float(c), float(l), float(q))

    def light_specular(self, r, g, b):
        self._need3d("lightSpecular")
        self.t3.lspec = self._rgbf(r, g, b)

    def ambient(self, args, ints):
        self._need3d("ambient")
        self.st.amb_c = self._calc(args, ints)

    def specular(self, args, ints):
        self._need3d("specular")
        self.st.spec_c = self._calc(args, ints)

    def emissive(self, args, ints):
        self._need3d("emissive")
        self.st.emis_c = self._calc(args, ints)

    def shininess(self, s):
        self._need3d("shininess")
        self.st.shine = float(s)

    def normal(self, x, y, z):
        self._need3d("normal")
        self.t3.normal = (float(x), float(y), float(z))
        if self.shape is not None:
            self.shape_normals = True

    def hint(self, which):
        t = self.t3
        if t is None:
            return
        w = int(which)
        if w in (2, -2):
            t.depth_test = w < 0
        elif w in (5, -5):
            t.depth_mask = w < 0

    # ---------------------------------------------------------------- 3D shapes (P3D)
    def box(self, w, h=None, d=None):
        self._need3d("box")
        if h is None:
            h = d = w
        verts, norms, faces, edges = T3.box_mesh(float(w), float(h), float(d))
        self.t3.mesh(self._mv(), verts, norms, [(f,) for f in faces], edges)

    def sphere_detail(self, u, v=None):
        self._need3d("sphereDetail")
        u = int(u)
        v = u if v is None else int(v)
        u = max(3, min(u, 200))
        v = max(2, min(v, 200))
        t = self.t3
        if (u, v) != (t.su, t.sv):
            t.su, t.sv = u, v
            t.stab = T3.sphere_tables(u, v)

    def sphere(self, r):
        self._need3d("sphere")
        t = self.t3
        if t.su < 3 or t.sv < 2:
            self.sphere_detail(30)
        verts, norms, tris, edges = T3.sphere_mesh(float(r), t.su, t.sv, t.stab)
        t.mesh(self._mv(), verts, norms, [(f,) for f in tris], edges)

    def _xf(self, pts: list) -> list:
        a, b, c, d, e, f = self.m
        return [(a * x + c * y + e, b * x + d * y + f) for x, y in pts]

    def _inverse(self):
        a, b, c, d, e, f = self.m
        det = a * d - b * c
        if abs(det) < 1e-12:
            return None
        ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
        return lambda x, y: (ia * (x - e) + ic * (y - f), ib * (x - e) + id_ * (y - f))

    def _normalize(self, pts: list, anchors: list | None = None) -> list:
        """Java2D's stroke normalisation (Processing's default): the path's points move to the
        nearest pixel centre in DEVICE space, floor(v) + 0.5 -- so a 1-pixel line on whole
        coordinates is one crisp pixel wide, not two half-grey ones. Fills are not normalised.
        `anchors` (flags) marks the points that snap; the others (flattened curve points) move by
        the offsets of the anchors around them, as Java moves a curve's control points."""
        inv = self._inverse()
        if inv is None or not pts:
            return pts
        a, b, c, d, e, f = self.m
        dev = [(a * x + c * y + e, b * x + d * y + f) for x, y in pts]
        off = [None] * len(dev)
        if self.canvas.aa:        # anti-aliased: the nearest pixel centre
            snap = lambda v: math.floor(v) + 0.5            # noqa: E731
        else:                     # crisp (noSmooth): Marlin's quarter rule, measured on 4.5.2
            snap = lambda v: math.floor(v + 0.25) + 0.25    # noqa: E731
        for i, (x, y) in enumerate(dev):
            if anchors is None or anchors[i]:
                off[i] = (snap(x) - x, snap(y) - y)
        if anchors is not None:
            known = [i for i, o in enumerate(off) if o is not None]
            if not known:
                ox, oy = snap(dev[0][0]) - dev[0][0], snap(dev[0][1]) - dev[0][1]
                off = [(ox, oy)] * len(dev)
            else:
                k = 0
                for i in range(len(off)):
                    if off[i] is not None:
                        continue
                    while k + 1 < len(known) and known[k + 1] < i:
                        k += 1
                    lo = known[k] if known[k] < i else None
                    hi = next((j for j in known[k:] if j > i), None)
                    if lo is None:
                        off[i] = off[hi]
                    elif hi is None:
                        off[i] = off[lo]
                    else:
                        t = (i - lo) / (hi - lo)
                        off[i] = (off[lo][0] + (off[hi][0] - off[lo][0]) * t, off[lo][1] + (off[hi][1] - off[lo][1]) * t)
        return [inv(x + o[0], y + o[1]) for (x, y), o in zip(dev, off)]

    def _scale(self) -> float:
        if self.m3 is not None:
            m = self.m3
            return math.sqrt(abs(m[0] * m[5] - m[1] * m[4])) or 1e-9
        a, b, c, d = self.m[:4]
        return math.sqrt(abs(a * d - b * c)) or 1e-9

    def _axis(self) -> bool:
        return self.m[1] == 0.0 and self.m[2] == 0.0

    def _flat3(self) -> bool:
        t = self.t3
        return self.m3 is None and t.cam_default and t.proj_default

    def screen_x(self, x, y, *_):
        if self.p3d and (not self._flat3() or (_ and _[0] != 0.0)):
            return f32(self.t3.screen(self._mv(), x, y, _[0] if _ else 0.0)[0])
        return f32(self.m[0] * x + self.m[2] * y + self.m[4])

    def screen_y(self, x, y, *_):
        if self.p3d and (not self._flat3() or (_ and _[0] != 0.0)):
            return f32(self.t3.screen(self._mv(), x, y, _[0] if _ else 0.0)[1])
        return f32(self.m[1] * x + self.m[3] * y + self.m[5])

    # ================================================================== painting helpers
    def _painter(self, tex, p, g, g2, dev=None):
        box = None
        if tex in textures.GRADIENTS and dev is not None:
            xs = [x for c in dev for x, _ in c]
            ys = [y for c in dev for _, y in c]
            box = (int(math.floor(min(xs))), int(math.floor(min(ys))), int(math.ceil(max(xs))), int(math.ceil(max(ys))))
        seed = self.tex_seed if self.tex_seed is not None else self.last_rseed
        ox, oy = (self.m3[3], self.m3[7]) if self.m3 is not None else (self.m[4], self.m[5])
        return textures.Painter(tex, p, textures.level_of(g), textures.level_of(g2), seed,
                                int(math.floor(ox + 0.5)), int(math.floor(oy + 0.5)), self.tex_cache, box)

    def _crisp(self, dev: list, rect: bool = False) -> list:
        """noSmooth fills, as Java2D does them (measured on Processing 4.5.2): a general path is
        sampled at whole-pixel coordinates (the same as moving it half a pixel and sampling pixel
        centres); a rectangle's corners go to floor(v + 0.25) + 0.25 first."""
        if self.canvas.aa:
            return dev
        if rect:
            f = math.floor
            return [[(f(x + 0.25) + 0.25, f(y + 0.25) + 0.25) for x, y in c] for c in dev]
        return [[(x + 0.5, y + 0.5) for x, y in c] for c in dev]

    def _fill_contours(self, contours: list, rect: bool = False) -> None:
        s = self.st
        if s.fill and s.fill_a > 0 and self._use3d():
            self._fill3(contours)
            return
        if s.fill and s.fill_a > 0:
            exact = [self._xf(c) for c in contours]
            painter = self._painter(s.tex, s.tex_p, s.fill_g, s.tex_g2, exact) if s.tex else None
            dev = self._crisp(exact, rect)
            self.canvas.fill(dev, s.fill_g, s.fill_a, painter)
            self._cost()

    def _stroke_contours(self, contours: list) -> None:
        s = self.st
        if contours and s.stroke_a > 0:
            dev = [self._xf(c) for c in contours]      # (the path was normalised before stroking)
            painter = self._painter(s.stex, s.stex_p, s.stroke_g, s.stex_g2, dev) if s.stex else None
            self.canvas.fill(dev, s.stroke_g, s.stroke_a, painter)
            self._cost()

    # ================================================================== textures (an extension)
    def _tex_name(self, name) -> str:
        name = str(name or "").strip().lower()
        if name not in textures.ALL:
            raise PdeError("runtime", f"there is no texture called {name!r}")
        return name

    def texture(self, name, p=None):
        name = self._tex_name(name)
        s = self.st
        s.tex = name
        if name in textures.GRADIENTS:
            s.tex_g2 = luma(self._grey(float(p), self.st.cmax[3])) if p is not None else 255
        else:
            s.tex_p = textures.clamp_p(name, p)

    def no_texture(self):
        self.st.tex = None

    def stroke_texture(self, name, p=None):
        name = self._tex_name(name)
        s = self.st
        s.stex = name
        if name in textures.GRADIENTS:
            s.stex_g2 = luma(self._grey(float(p), self.st.cmax[3])) if p is not None else 255
        else:
            s.stex_p = textures.clamp_p(name, p)

    def no_stroke_texture(self):
        self.st.stex = None

    def texture_seed(self, n):
        self.tex_seed = int(n)

    def background_texture(self, nums, ints, name, extra=None):
        """background(gray, "dots", 8) / background(g1, g2, "vgradient"): the whole canvas."""
        name = self._tex_name(name)
        if name in textures.GRADIENTS:
            g1 = luma(self._grey(float(nums[0]), self.st.cmax[3]))
            g2 = luma(self._grey(float(nums[1]), self.st.cmax[3])) if len(nums) > 1 else (
                luma(self._grey(float(extra), self.st.cmax[3])) if extra is not None else 255)
            painter = self._painter(name, 6, g1, g2, [[(0, 0), (W, H)]])
        else:
            c = self._calc(tuple(nums), tuple(ints))
            painter = self._painter(name, textures.clamp_p(name, extra), luma(c), 255)
        buf = self.canvas.buf
        for y in range(H):
            buf[y * W:(y + 1) * W] = painter.row(y)
        self.budget.charge(W * H // 8)
        if self.t3 is not None:
            self.t3.clear_depth()

    def _thin(self) -> bool:
        """noSmooth() with a stroke no wider than a pixel: Java2D's thin-line path (Bresenham)."""
        return not self.canvas.aa and self.st.sw * self._scale() <= 1.0

    def _thin_path(self, pts: list, closed: bool) -> None:
        s = self.st
        if s.stroke_a <= 0 or not pts:
            return
        dev = self._xf(pts)
        painter = self._painter(s.stex, s.stex_p, s.stroke_g, s.stex_g2, [dev]) if s.stex else None
        if len(dev) == 1:
            dev = dev * 2
        segs = list(zip(dev, dev[1:])) + ([(dev[-1], dev[0])] if closed and len(dev) > 2 else [])
        for (x0, y0), (x1, y1) in segs:
            self.canvas.thin_line(x0, y0, x1, y1, s.stroke_g, s.stroke_a, painter)
        self._cost()

    def _stroke_path(self, pts: list, closed: bool, anchors: list | None = None) -> None:
        s = self.st
        if not s.stroke or s.sw <= 0 or not pts:
            return
        if self._use3d():
            self._stroke3([(x, y, 0.0) for x, y in pts], closed)
            return
        if self._thin():
            self._thin_path(pts, closed)
            return
        pts = self._normalize(pts, anchors)
        self._stroke_contours(raster.stroke_polyline(pts, s.sw, closed, s.cap, s.join, self._scale()))

    # ================================================================== 2D shapes through the 3D pipeline
    def _fill3(self, contours: list, lit: bool = True) -> None:
        """Contours at z = 0 (model space) filled in 3D: one planar face, holes included, its
        normal +z or -z as its winding says (PGraphicsOpenGL's automatic normals)."""
        verts: list = []
        face: list = []
        for c in contours:
            if len(c) < 2:
                continue
            idx = []
            for x, y in c:
                idx.append(len(verts))
                verts.append((x, y, 0.0))
            face.append(tuple(idx))
        if not face:
            return
        nz = T3.newell([verts[i] for i in face[0]])[2]
        fn = (0.0, 0.0, 1.0 if nz >= 0.0 else -1.0)
        self.t3.mesh(self._mv(), verts, None, [tuple(face)], None, lit=lit, face_norms=[fn], stroke=False,
                     text=not lit)

    def _stroke3(self, pts: list, closed: bool) -> None:
        """A polyline in 3D (model space points): its segments as screen-space lines."""
        if len(pts) == 1:
            self.t3.points(self._mv(), pts)
            return
        n = len(pts)
        edges = [(i, i + 1) for i in range(n - 1)]
        if closed and n > 2:
            edges.append((n - 1, 0))
        self.t3.mesh(self._mv(), pts, None, None, edges, fill=False)

    # ================================================================== shapes
    def point(self, x, y, *_):
        s = self.st
        if not s.stroke or s.sw <= 0:
            return
        if self.p3d and (self._use3d() or (_ and _[0] != 0.0)):
            self.t3.points(self._mv(), [(x, y, float(_[0]) if _ else 0.0)])
            return
        hw = s.sw / 2
        if self._thin():
            self._thin_path([(x, y)], False)
            return
        x, y = self._normalize([(x, y)])[0]
        if s.cap == raster.ROUND:
            n = raster.segments_for(hw * self._scale())
            self._stroke_contours([raster.ellipse_points(x, y, hw, hw, n)])
        elif s.cap == raster.PROJECT:
            self._stroke_contours([[(x - hw, y - hw), (x + hw, y - hw), (x + hw, y + hw), (x - hw, y + hw)]])

    def line(self, x1, y1, x2, y2, *_):
        if len(_) == 2:         # line(x1, y1, z1, x2, y2, z2)
            if self.p3d and (self._use3d() or x2 != 0.0 or _[1] != 0.0):
                s = self.st
                if s.stroke and s.sw > 0:
                    self._stroke3([(x1, y1, x2), (y2, _[0], _[1])], False)
                return
            x1, y1, x2, y2 = x1, y1, y2, _[0]
        self._stroke_path([(x1, y1), (x2, y2)], False)

    def _rect_box(self, a, b, c, d, mode):
        if mode == 1:          # CORNERS
            x0, y0, x1, y1 = a, b, c, d
        elif mode == 3:        # CENTER
            x0, y0, x1, y1 = a - c / 2, b - d / 2, a + c / 2, b + d / 2
        elif mode == 2:        # RADIUS
            x0, y0, x1, y1 = a - c, b - d, a + c, b + d
        else:
            x0, y0, x1, y1 = a, b, a + c, b + d
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        return x0, y0, x1, y1

    def rect(self, a, b, c, d, *radii):
        x0, y0, x1, y1 = self._rect_box(a, b, c, d, self.st.rect_mode)
        if radii and any(r > 0 for r in radii):
            self._round_rect(x0, y0, x1, y1, radii)
            return
        s = self.st
        if self._use3d():
            box = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            self._fill_contours([box])
            self._stroke_path(box, True)
            return
        if s.fill and s.fill_a > 0:
            if self._axis() and not s.tex:
                m = self.m
                self.canvas.fill_rect(m[0] * x0 + m[4], m[3] * y0 + m[5], m[0] * x1 + m[4], m[3] * y1 + m[5],
                                      s.fill_g, s.fill_a)
                self.budget.charge(4 + self.canvas.cost)
                self.canvas.cost = 0
            else:
                self._fill_contours([[(x0, y0), (x1, y0), (x1, y1), (x0, y1)]], rect=True)
        if s.stroke and s.sw > 0 and self._thin():
            # Java2D's crisp rectangle outline rounds its corners down from a quarter pixel
            # (floor(v + 0.25)), unlike its lines (floor(v + 0.5)) -- measured on Processing 4.5.2
            dev = self._xf([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
            q = [(math.floor(x + 0.25), math.floor(y + 0.25)) for x, y in dev]
            inv = self._inverse()
            self._thin_path([inv(x, y) for x, y in q] if inv else [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], True)
        elif s.stroke and s.sw > 0:
            box = self._normalize([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
            hw = s.sw / 2
            if s.join == raster.MITER and self._axis():
                (x0, y0), (x1, y1) = box[0], box[2]
                outer = [(x0 - hw, y0 - hw), (x1 + hw, y0 - hw), (x1 + hw, y1 + hw), (x0 - hw, y1 + hw)]
                if abs(x1 - x0) > s.sw and abs(y1 - y0) > s.sw:
                    inner = [(x0 + hw, y0 + hw), (x1 - hw, y0 + hw), (x1 - hw, y1 - hw), (x0 + hw, y1 - hw)]
                    self._stroke_contours(raster.ring(outer, inner))
                else:
                    self._stroke_contours([outer])
            else:
                self._stroke_contours(raster.stroke_polyline(box, s.sw, True, s.cap, s.join, self._scale()))

    def square(self, x, y, e):
        self.rect(x, y, e, e)

    def _round_rect(self, x0, y0, x1, y1, radii):
        tl = tr = br = bl = radii[0]
        if len(radii) >= 4:
            tl, tr, br, bl = radii[:4]
        mr = min(x1 - x0, y1 - y0) / 2
        tl, tr, br, bl = (max(0.0, min(r, mr)) for r in (tl, tr, br, bl))
        pts: list = []

        def corner(cx, cy, r, a0):
            if r <= 0:
                pts.append((cx, cy))
                return
            n = max(2, raster.segments_for(r * self._scale(), math.pi / 2))
            for i in range(n + 1):
                a = a0 + (math.pi / 2) * i / n
                pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        corner(x1 - tr if tr else x1, y0 + tr if tr else y0, tr, -math.pi / 2)
        corner(x1 - br if br else x1, y1 - br if br else y1, br, 0.0)
        corner(x0 + bl if bl else x0, y1 - bl if bl else y1, bl, math.pi / 2)
        corner(x0 + tl if tl else x0, y0 + tl if tl else y0, tl, math.pi)
        self._fill_contours([pts])
        self._stroke_path(pts, True)

    def _ellipse_box(self, a, b, c, d, mode):
        if mode == 1:          # CORNERS
            return (a + c) / 2, (b + d) / 2, abs(c - a) / 2, abs(d - b) / 2
        if mode == 0:          # CORNER
            return a + c / 2, b + d / 2, abs(c) / 2, abs(d) / 2
        if mode == 2:          # RADIUS
            return a, b, abs(c), abs(d)
        return a, b, abs(c) / 2, abs(d) / 2

    def ellipse(self, a, b, c, d):
        cx, cy, rx, ry = self._ellipse_box(a, b, c, d, self.st.ellipse_mode)
        s = self.st
        sc = self._scale()
        if self._use3d():
            n = raster.segments_for(max(rx, ry) * sc)
            pts = raster.ellipse_points(cx, cy, rx, ry, n)
            if rx > 0 and ry > 0:
                self._fill_contours([pts])
            self._stroke_path(pts, True)
            return
        if s.fill and s.fill_a > 0 and rx > 0 and ry > 0:
            n = raster.segments_for(max(rx, ry) * sc)
            self._fill_contours([raster.ellipse_points(cx, cy, rx, ry, n)])
        if s.stroke and s.sw > 0 and self._thin():
            # Java2D steps along the ellipse's four Bezier curves pixel by pixel; points about
            # half a pixel apart, each rounded, trace the same pixels
            k = 0.5522847498
            pts: list = []
            # ~half a pixel a step; an ellipse far bigger than the canvas (mostly off it) is capped at
            # THIN_SEG_MAX steps a quarter, charged to the frame before any point exists
            q = (abs(rx) + abs(ry)) * sc * 1.6
            seg = THIN_SEG_MAX if q >= THIN_SEG_MAX else max(4, int(q))
            self.budget.charge(4 * seg)
            for (ax, ay), (bx, by) in (((rx, 0), (0, ry)), ((0, ry), (-rx, 0)), ((-rx, 0), (0, -ry)), ((0, -ry), (rx, 0))):
                c1 = (ax + k * bx if ax == 0 else ax, ay + k * by if ay == 0 else ay)
                c2 = (bx + k * ax if bx == 0 else bx, by + k * ay if by == 0 else by)
                for i in range(seg):
                    t = i / seg
                    u = 1 - t
                    pts.append((cx + u * u * u * ax + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t * t * t * bx,
                                cy + u * u * u * ay + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t * t * t * by))
            self._thin_path(pts, True)
        elif s.stroke and s.sw > 0:
            hw = s.sw / 2
            if self._axis():
                # Java normalises the four ends of the ellipse's curves: left/right, top/bottom
                (lx, _), (rx_, _), (_, ty), (_, by) = self._normalize(
                    [(cx - rx, cy), (cx + rx, cy), (cx, cy - ry), (cx, cy + ry)])
                cx, rx, cy, ry = (lx + rx_) / 2, abs(rx_ - lx) / 2, (ty + by) / 2, abs(by - ty) / 2
            else:
                ncx, ncy = self._normalize([(cx, cy)])[0]
                cx, cy = ncx, ncy
            n = raster.segments_for((max(rx, ry) + hw) * sc)
            outer = raster.ellipse_points(cx, cy, rx + hw, ry + hw, n)
            if rx - hw > 0 and ry - hw > 0:
                self._stroke_contours(raster.ring(outer, raster.ellipse_points(cx, cy, rx - hw, ry - hw, n)))
            else:
                self._stroke_contours([outer])

    def circle(self, x, y, d):
        self.ellipse(x, y, d, d)

    def arc(self, a, b, c, d, start, stop, mode=0):
        cx, cy, rx, ry = self._ellipse_box(a, b, c, d, self.st.ellipse_mode)
        start, stop = float(start), float(stop)
        if math.isinf(start) or math.isinf(stop) or start != start or stop != stop or stop < start:
            return
        if start < -ARC_FOLD:
            # far below zero, adding TWO_PI would take forever (or never move start at all):
            # fold it into (-2pi, 0] first, the sweep kept (Processing itself loops here)
            r = math.fmod(start, 2 * math.pi)
            stop, start = stop + (r - start), r
        while start < 0:
            start += 2 * math.pi
            stop += 2 * math.pi
        if stop - start > 2 * math.pi:
            start, stop = 0.0, 2 * math.pi
        sweep = stop - start
        mode = int(mode)
        sc = self._scale()
        n = raster.segments_for(max(rx, ry) * sc, sweep)
        pts = [(cx + rx * math.cos(start + sweep * i / n), cy + ry * math.sin(start + sweep * i / n))
               for i in range(n + 1)]
        s = self.st
        if s.fill and s.fill_a > 0 and rx > 0 and ry > 0:
            self._fill_contours([pts if mode in (1, 2) else pts + [(cx, cy)]])   # OPEN/CHORD: chord; else pie
        if s.stroke and s.sw > 0:
            ends = [i in (0, n) for i in range(n + 1)]
            if mode == 3:
                self._stroke_path(pts + [(cx, cy)], True, ends + [True])
            elif mode == 2:
                self._stroke_path(pts, True, ends)
            else:
                self._stroke_path(pts, False, ends)

    def triangle(self, x1, y1, x2, y2, x3, y3):
        pts = [(x1, y1), (x2, y2), (x3, y3)]
        self._fill_contours([pts])
        self._stroke_path(pts, True)

    def quad(self, x1, y1, x2, y2, x3, y3, x4, y4):
        pts = [(x1, y1), (x2, y2), (x3, y3), (x4, y4)]
        self._fill_contours([pts])
        self._stroke_path(pts, True)

    # ---------------------------------------------------------------- curves
    def _bez_pts(self, x0, y0, x1, y1, x2, y2, x3, y3) -> list:
        ln = math.hypot(x1 - x0, y1 - y0) + math.hypot(x2 - x1, y2 - y1) + math.hypot(x3 - x2, y3 - y2)
        n = max(4, min(64, int(ln * self._scale() / 3) + 1))
        out = []
        for i in range(1, n + 1):
            t = i / n
            u = 1 - t
            a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
            out.append((a * x0 + b * x1 + c * x2 + d * x3, a * y0 + b * y1 + c * y2 + d * y3))
        return out

    def bezier(self, x1, y1, x2, y2, x3, y3, x4, y4):
        pts = [(x1, y1)] + self._bez_pts(x1, y1, x2, y2, x3, y3, x4, y4)
        self._fill_contours([pts])
        self._stroke_path(pts, False, [i in (0, len(pts) - 1) for i in range(len(pts))])

    def _curve_seg(self, p0, p1, p2, p3) -> list:
        """Catmull-Rom with Processing's curveTightness (0 = Catmull-Rom)."""
        s = (1 - self.curve_tightness) / 2
        c1 = (p1[0] + s * (p2[0] - p0[0]) / 3, p1[1] + s * (p2[1] - p0[1]) / 3)
        c2 = (p2[0] - s * (p3[0] - p1[0]) / 3, p2[1] - s * (p3[1] - p1[1]) / 3)
        return self._bez_pts(p1[0], p1[1], c1[0], c1[1], c2[0], c2[1], p2[0], p2[1])

    def curve(self, x1, y1, x2, y2, x3, y3, x4, y4):
        pts = [(x2, y2)] + self._curve_seg((x1, y1), (x2, y2), (x3, y3), (x4, y4))
        self._fill_contours([pts])
        self._stroke_path(pts, False, [i in (0, len(pts) - 1) for i in range(len(pts))])

    def curve_tightness_set(self, t):
        self.curve_tightness = float(t)

    @staticmethod
    def bezier_point(a, b, c, d, t):
        u = 1 - t
        return f32(a * u * u * u + 3 * b * t * u * u + 3 * c * t * t * u + d * t * t * t)

    @staticmethod
    def bezier_tangent(a, b, c, d, t):
        return f32(3 * t * t * (-a + 3 * b - 3 * c + d) + 6 * t * (a - 2 * b + c) + 3 * (-a + b))

    @staticmethod
    def curve_point(a, b, c, d, t):
        t2, t3 = t * t, t * t * t
        return f32(0.5 * ((2 * b) + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t2 + (-a + 3 * b - 3 * c + d) * t3))

    # ---------------------------------------------------------------- beginShape
    def begin_shape(self, kind=20):
        self.shape = []
        self.contours = []
        self.shape_kind = int(kind)
        self.shape_normals = False

    def _need_shape(self):
        if self.shape is None:
            raise PdeError("runtime", "vertex() must be between beginShape() and endShape()")
        if len(self.shape) > 20000:
            raise PdeError("memory", "a shape has more than 20000 vertices")
        self.budget.charge(2)

    def vertex(self, x, y, *_):
        self._need_shape()
        if self.p3d:
            # (x, y, z, normal): z only for vertex(x, y, z); vertex(x, y, u, v) is 2D
            self.shape.append(("v", x, y, float(_[0]) if len(_) in (1, 3) else 0.0, self.t3.normal))
            return
        self.shape.append(("v", x, y))

    def bezier_vertex(self, *a):
        self._need_shape()
        if self.p3d and len(a) == 9:
            self.shape.append(("b", a[0], a[1], a[3], a[4], a[6], a[7], a[2], a[5], a[8]))
            return
        self.shape.append(("b",) + tuple(a[-6:]) if len(a) == 6 else ("b", a[0], a[1], a[3], a[4], a[6], a[7]))

    def quadratic_vertex(self, *a):
        self._need_shape()
        if self.p3d and len(a) == 6:
            self.shape.append(("q", a[0], a[1], a[3], a[4], a[2], a[5]))
            return
        self.shape.append(("q",) + tuple(a[:4]) if len(a) == 4 else ("q", a[0], a[1], a[3], a[4]))

    def curve_vertex(self, x, y, *_):
        self._need_shape()
        if self.p3d:
            self.shape.append(("c", x, y, float(_[0]) if _ else 0.0, self.t3.normal))
            return
        self.shape.append(("c", x, y))

    def begin_contour(self):
        self._need_shape()
        self.contours.append(("main",) + self._flatten(self.shape) + (self.shape,))
        self.shape = []

    def end_contour(self):
        self._need_shape()
        self.contours.append(("hole",) + self._flatten(self.shape) + (self.shape,))
        self.shape = []

    def _flatten(self, items: list) -> tuple[list, list]:
        """The shape's outline as points, and which of them are vertices the sketch gave (they
        snap to pixel centres when stroked; flattened curve points follow them)."""
        pts: list = []
        anchors: list = []
        curve: list = []

        def add(new, anchor_last):
            pts.extend(new)
            anchors.extend([False] * (len(new) - 1) + [anchor_last] if new else [])

        def flush_curve():
            if len(curve) >= 4:
                pts.append(curve[1])
                anchors.append(True)
                for i in range(len(curve) - 3):
                    add(self._curve_seg(curve[i], curve[i + 1], curve[i + 2], curve[i + 3]), True)
            curve.clear()
        for it in items:
            k = it[0]
            if k == "c":
                curve.append((it[1], it[2]))
                continue
            flush_curve()
            if k == "v":
                pts.append((it[1], it[2]))
                anchors.append(True)
            elif k == "b" and pts:
                x0, y0 = pts[-1]
                add(self._bez_pts(x0, y0, *it[1:7]), True)
            elif k == "q" and pts:
                x0, y0 = pts[-1]
                cx, cy, x3, y3 = it[1:5]
                add(self._bez_pts(x0, y0, x0 + 2 / 3 * (cx - x0), y0 + 2 / 3 * (cy - y0),
                                  x3 + 2 / 3 * (cx - x3), y3 + 2 / 3 * (cy - y3), x3, y3), True)
        flush_curve()
        return pts, anchors

    def end_shape(self, mode=1):
        if self.shape is None:
            raise PdeError("runtime", "endShape() without beginShape()")
        if self.p3d and (self._use3d() or self._shape_has_z()):
            self._end_shape3d(int(mode) == 2)
            return
        items, kind = self.shape, self.shape_kind
        holes = [(c[1], c[2]) for c in self.contours if c[0] == "hole"]
        before = [(c[1], c[2]) for c in self.contours if c[0] == "main"]
        self.shape, self.contours = None, []
        close = int(mode) == 2
        if kind == 20 or kind not in (3, 5, 9, 10, 11, 17, 18):
            main: list = []
            anchors: list = []
            for bp, ba in before:
                main.extend(bp)
                anchors.extend(ba)
            fp, fa = self._flatten(items)
            main.extend(fp)
            anchors.extend(fa)
            if len(main) >= 2:
                if len(main) >= 3:
                    self._fill_contours([main] + [h[0] for h in holes])
                self._stroke_path(main, close, anchors)
            for hp, ha in holes:
                self._stroke_path(hp, True, ha)
            return
        pts = [(it[1], it[2]) for it in items if it[0] in ("v", "c")]
        if kind == 3:
            for x, y in pts:
                self.point(x, y)
        elif kind == 5:
            for i in range(0, len(pts) - 1, 2):
                self.line(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
        elif kind == 9:
            for i in range(0, len(pts) - 2, 3):
                self._poly(pts[i:i + 3])
        elif kind == 10:
            for i in range(len(pts) - 2):
                self._poly(pts[i:i + 3])
        elif kind == 11:
            for i in range(1, len(pts) - 1):
                self._poly([pts[0], pts[i], pts[i + 1]])
        elif kind == 17:
            for i in range(0, len(pts) - 3, 4):
                self._poly(pts[i:i + 4])
        elif kind == 18:
            for i in range(0, len(pts) - 3, 2):
                self._poly([pts[i], pts[i + 1], pts[i + 3], pts[i + 2]])

    def _poly(self, pts):
        self._fill_contours([pts])
        self._stroke_path(pts, True)

    # ---------------------------------------------------------------- beginShape in 3D (P3D)
    def _shape_has_z(self) -> bool:
        raws = [self.shape] + [c[3] for c in self.contours]
        for items in raws:
            for it in items:
                k = it[0]
                if k in ("v", "c") and len(it) > 3 and it[3] != 0.0:
                    return True
                if k == "b" and len(it) == 10 and (it[7] != 0.0 or it[8] != 0.0 or it[9] != 0.0):
                    return True
                if k == "q" and len(it) == 7 and (it[5] != 0.0 or it[6] != 0.0):
                    return True
        return False

    def _bez3(self, p0, p1, p2, p3) -> list:
        """A cubic Bezier from p0 (excluded) to p3 in 3D, as many points as _bez_pts takes in 2D."""
        def dist(a, b):
            dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
            return math.sqrt(dx * dx + dy * dy + dz * dz)
        ln = dist(p0, p1) + dist(p1, p2) + dist(p2, p3)
        n = max(4, min(64, int(ln * self._scale() / 3) + 1))
        out = []
        for i in range(1, n + 1):
            t = i / n
            u = 1 - t
            a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
            out.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1],
                        a * p0[2] + b * p1[2] + c * p2[2] + d * p3[2]))
        return out

    def _flatten3(self, items: list) -> list:
        """The shape's points in 3D with their normals: [(x, y, z, normal)]."""
        pts: list = []
        curve: list = []
        cur_n = self.t3.normal

        def flush():
            if len(curve) >= 4:
                s_ = (1 - self.curve_tightness) / 2
                pts.append(curve[1])
                for i in range(len(curve) - 3):
                    q0, q1, q2, q3 = curve[i], curve[i + 1], curve[i + 2], curve[i + 3]
                    c1 = tuple(q1[k] + s_ * (q2[k] - q0[k]) / 3 for k in range(3))
                    c2 = tuple(q2[k] - s_ * (q3[k] - q1[k]) / 3 for k in range(3))
                    for p in self._bez3(q1, c1, c2, q2):
                        pts.append((p[0], p[1], p[2], q2[3]))
            curve.clear()
        for it in items:
            k = it[0]
            if k == "c":
                curve.append((it[1], it[2], it[3], it[4]))
                continue
            flush()
            if k == "v":
                pts.append((it[1], it[2], it[3], it[4]))
            elif k == "b" and pts:
                x0, y0, z0, n0 = pts[-1]
                z1, z2, z3 = (it[7], it[8], it[9]) if len(it) == 10 else (0.0, 0.0, 0.0)
                for p in self._bez3((x0, y0, z0), (it[1], it[2], z1), (it[3], it[4], z2), (it[5], it[6], z3)):
                    pts.append((p[0], p[1], p[2], n0))
            elif k == "q" and pts:
                x0, y0, z0, n0 = pts[-1]
                cz, z3 = (it[5], it[6]) if len(it) == 7 else (0.0, 0.0)
                cx, cy, x3, y3 = it[1:5]
                c1 = (x0 + 2 / 3 * (cx - x0), y0 + 2 / 3 * (cy - y0), z0 + 2 / 3 * (cz - z0))
                c2 = (x3 + 2 / 3 * (cx - x3), y3 + 2 / 3 * (cy - y3), z3 + 2 / 3 * (cz - z3))
                for p in self._bez3((x0, y0, z0), c1, c2, (x3, y3, z3)):
                    pts.append((p[0], p[1], p[2], n0))
        flush()
        del cur_n
        return pts

    def _end_shape3d(self, close: bool) -> None:
        items, kind = self.shape, self.shape_kind
        raws = self.contours
        auto = not self.shape_normals
        self.shape, self.contours = None, []
        mv = self._mv()
        t3 = self.t3
        if kind in (3, 5, 9, 10, 11, 17, 18):
            pts = [(it[1], it[2], it[3], it[4]) for it in items if it[0] in ("v", "c")]
            verts = [p[:3] for p in pts]
            n = len(verts)
            if kind == 3:
                t3.points(mv, verts)
                return
            if kind == 5:
                t3.mesh(mv, verts, None, None, [(i, i + 1) for i in range(0, n - 1, 2)], fill=False)
                return
            faces: list = []
            edges: list = []
            tris: list = []            # triangles for the automatic normals, in Processing's order
            if kind == 9:
                for i in range(n // 3):
                    a, b, c = 3 * i, 3 * i + 1, 3 * i + 2
                    faces.append(((a, b, c),))
                    edges.extend(((a, b), (b, c), (c, a)))
                    tris.append((a, b, c))
            elif kind == 11:
                for i in range(1, n - 1):
                    faces.append(((0, i, i + 1),))
                    edges.extend(((0, i), (i, i + 1), (i + 1, 0)))
                    tris.append((0, i, i + 1))
            elif kind == 10:
                for i in range(1, n - 1):
                    f = (i, i - 1, i + 1) if i % 2 == 0 else (i, i + 1, i - 1)
                    faces.append((f,))
                    e1, e2 = (i - 1, i + 1) if i % 2 == 0 else (i + 1, i - 1)
                    edges.extend(((i, e1), (e1, e2), (e2, i)))
                    tris.append((i - 1, i, i + 1) if i % 2 == 1 else (i + 1, i, i - 1))
            elif kind == 17:
                for q in range(n // 4):
                    a, b, c, d = 4 * q, 4 * q + 1, 4 * q + 2, 4 * q + 3
                    faces.append(((a, b, c, d),))
                    edges.extend(((a, b), (b, c), (c, d), (d, a)))
                    tris.extend(((a, b, c), (c, d, a)))
            else:
                for q in range(1, n // 2):
                    a, b, c, d = 2 * (q - 1), 2 * (q - 1) + 1, 2 * q + 1, 2 * q
                    faces.append(((b, c, d, a),))
                    edges.extend(((a, b), (b, c), (c, d), (d, a)))
                    tris.extend(((a, b, d), (d, b, c)))
            if auto:
                norms = [(0.0, 0.0, 1.0)] * n
                for a, b, c in tris:
                    nn = T3.tri_normal(verts[a], verts[b], verts[c])
                    norms[a] = norms[b] = norms[c] = nn
            else:
                norms = [p[3] for p in pts]
            t3.mesh(mv, verts, norms, faces, edges)
            return
        # POLYGON (and anything else): one outline, holes from beginContour()/endContour()
        main: list = []
        holes: list = []
        for c in raws:
            if c[0] == "main":
                main.extend(self._flatten3(c[3]))
            else:
                holes.append(self._flatten3(c[3]))
        main.extend(self._flatten3(items))
        verts = [p[:3] for p in main]
        norms = [p[3] for p in main]
        faces = []
        holes_in = False            # the holes' points are in verts (a planar outline's face has them)
        n = len(verts)
        if n >= 3:
            nx, ny, nz = T3.newell(verts)
            nl = math.sqrt(nx * nx + ny * ny + nz * nz)
            if nl > 0.0:
                ux, uy, uz = nx / nl, ny / nl, nz / nl
                x0, y0, z0 = verts[0]
                ext = max(max(abs(v[0] - x0), abs(v[1] - y0), abs(v[2] - z0)) for v in verts)
                planar = all(abs((v[0] - x0) * ux + (v[1] - y0) * uy + (v[2] - z0) * uz) <= 1e-6 * ext + 1e-9
                             for v in verts)
                if planar:
                    face = [tuple(range(n))]
                    for h in holes:
                        k = len(verts)
                        verts.extend(p[:3] for p in h)
                        norms.extend(p[3] for p in h)
                        face.append(tuple(range(k, k + len(h))))
                    holes_in = True
                    faces.append(tuple(face))
                    if auto:
                        norms = [(ux, uy, uz)] * len(verts)
                else:
                    tris = [(0, k, k + 1) for k in range(1, n - 1)]
                    faces = [(t,) for t in tris]
                    if auto:
                        norms = [(0.0, 0.0, 1.0)] * n
                        for a, b, c in tris:
                            nn = T3.tri_normal(verts[a], verts[b], verts[c])
                            norms[a] = norms[b] = norms[c] = nn
        edges = [(i, i + 1) for i in range(n - 1)]
        if close and n > 2:
            edges.append((n - 1, 0))
        k = n
        hole_edges = []
        for h in holes:
            m = len(h)
            if not holes_in:
                verts.extend(p[:3] for p in h)
                norms.extend(p[3] for p in h)
            hole_edges.extend((k + i, k + (i + 1) % m) for i in range(m) if m > 1)
            k += m
        t3.mesh(mv, verts, norms, faces, edges + hole_edges)

    # ================================================================== text
    def text_size(self, s):
        self.st.text_size = max(0.0, float(s))
        self.st.leading = None

    def text_align(self, ax, ay=0):
        self.st.align_x, self.st.align_y = int(ax), int(ay)

    def text_leading(self, v):
        self.st.leading = float(v)

    def create_font(self, name, size, *_):
        return PFont(float(size))

    def text_font(self, font, size=None):
        if font is None:
            raise PdeError("runtime", "textFont() got a null font")
        self.text_size(size if size is not None else font.size)

    def text_ascent(self) -> float:
        return f32(font_data.ASCENT * self.st.text_size / 1000)

    def text_descent(self) -> float:
        return f32(font_data.DESCENT * self.st.text_size / 1000)

    def _leading(self) -> float:
        s = self.st
        return s.leading if s.leading is not None else (font_data.ASCENT + font_data.DESCENT) * s.text_size / 1000 * 1.275

    def text_width(self, s) -> float:
        s = str(s)
        w = 0.0
        best = 0.0
        for ch in s:
            if ch == "\n":
                best, w = max(best, w), 0.0
                continue
            g = font_data.GLYPHS.get(ord(ch)) or font_data.GLYPHS[63]
            w += g[0]
        return f32(max(best, w) * self.st.text_size / 1000)

    def _glyphs(self, line: str, x: float, y: float) -> list:
        """Contours of one line of text whose pen starts at (x, y) on the baseline (user space)."""
        k = self.st.text_size / 1000
        out = []
        for ch in line:
            g = font_data.GLYPHS.get(ord(ch))
            if g is None:
                g = font_data.GLYPHS[63] if ord(ch) > 32 else font_data.GLYPHS[32]
            for c in g[1]:
                out.append([(x + c[i] * k, y + c[i + 1] * k) for i in range(0, len(c), 2)])
            x += g[0] * k
            self.budget.charge(8)
        return out

    def text(self, s, x, y, x2=None, y2=None):
        st = self.st
        s = str(s)
        if len(s) > 2000:
            s = s[:2000]
        if not s or st.text_size <= 0:
            return
        if x2 is not None:
            self._text_box(s, x, y, x2, y2)
            return
        lines = s.split("\n")
        lead = self._leading()
        asc, desc = self.text_ascent(), self.text_descent()
        if st.align_y == 3:          # CENTER (PGraphics.text: y += textAscent() / 2)
            y += asc / 2 - (len(lines) - 1) * lead / 2
        elif st.align_y == 101:      # TOP
            y += asc
        elif st.align_y == 102:      # BOTTOM
            y -= desc + (len(lines) - 1) * lead
        contours = []
        for i, ln in enumerate(lines):
            w = self.text_width(ln)
            lx = x - w / 2 if st.align_x == 3 else x - w if st.align_x == 39 else x
            contours.extend(self._glyphs(ln, lx, y + i * lead))
        self._text_paint(contours)

    def text_z(self, s, x, y, z):
        """text(s, x, y, z): the words at depth z (P3D); z is ignored in 2D, as before."""
        if self.p3d and z != 0.0:
            self.push_matrix()
            self.translate(0.0, 0.0, z)
            try:
                self.text(s, x, y)
            finally:
                self.pop_matrix()
            return
        self.text(s, x, y)

    def _text_paint(self, contours):
        st = self.st
        if contours and st.fill_a > 0 and self._use3d():
            self._fill3(contours, lit=False)
            return
        if contours and st.fill_a > 0:
            dev = [self._xf(c) for c in contours]
            painter = self._painter(st.tex, st.tex_p, st.fill_g, st.tex_g2, dev) if st.tex else None
            # Processing's JAVA2D keeps text anti-aliased under noSmooth() (measured, 4.5.2)
            aa, self.canvas.aa = self.canvas.aa, True
            try:
                self.canvas.fill(dev, st.fill_g, st.fill_a, painter)
            finally:
                self.canvas.aa = aa
            self._cost()

    def _text_box(self, s, x, y, w, h):
        st = self.st
        if st.rect_mode == 3:
            x, y = x - w / 2, y - h / 2
        elif st.rect_mode == 1:
            w, h = w - x, h - y
        lines: list = []
        for para in s.split("\n"):
            cur = ""
            for word in para.split(" "):
                trial = word if not cur else cur + " " + word
                if cur and self.text_width(trial) > w:
                    lines.append(cur)
                    cur = word
                else:
                    cur = trial
            lines.append(cur)
        lead = self._leading()
        asc = self.text_ascent()
        yy = y + asc
        contours = []
        for ln in lines:
            if yy - asc + lead > y + h + 0.01 and contours:
                break
            lw = self.text_width(ln)
            lx = x + (w - lw) / 2 if st.align_x == 3 else x + w - lw if st.align_x == 39 else x
            contours.extend(self._glyphs(ln, lx, yy))
            yy += lead
        self._text_paint(contours)

    # ================================================================== pixels
    def load_pixels(self) -> None:
        buf = self.canvas.buf
        if self.pixels is None:
            self.budget.allocate(W * H * 4)
        if type(self.canvas) is raster.TwinCanvas:
            img = self.get_image()
            self.pixels = img.pixels
        else:
            self.pixels = array("i", ((-16777216 | (g << 16) | (g << 8) | g) for g in buf))
        self.budget.charge(W * H // 16)

    def update_pixels(self, *_) -> None:
        if self.pixels is None:
            return
        buf = self.canvas.buf
        if type(self.canvas) is raster.TwinCanvas:
            cov = self.canvas.cov
            for i, c in enumerate(self.pixels):
                a = (c >> 24) & 255
                buf[i] = 255 - ((255 - luma(c)) * a + 127) // 255
                cov[i] = 255 - a
            self.budget.charge(W * H // 8)
            return
        for i, c in enumerate(self.pixels):
            a = (c >> 24) & 255
            g = luma(c)
            buf[i] = g if a == 255 else (g * a + 255 * (255 - a)) // 255
        self.budget.charge(W * H // 8)

    def get(self, x, y) -> int:
        x, y = int(x), int(y)
        if 0 <= x < W and 0 <= y < H:
            g = self.canvas.buf[y * W + x]
            return _jint(0xFF000000 | (g << 16) | (g << 8) | g)
        return 0

    def set(self, x, y, c) -> None:
        x, y = int(x), int(y)
        if 0 <= x < W and 0 <= y < H:
            self.canvas.buf[y * W + x] = luma(int(c))
            if type(self.canvas) is raster.TwinCanvas:
                self.canvas.opaque(y * W + x)

    def set_image(self, x, y, img) -> None:
        """set(x, y, image): the image's pixels copied in, no blending, no transform."""
        self.image_at(img, int(x), int(y), None, None, raw=True)

    # ================================================================== images and offscreen surfaces
    def blend_mode(self, m):
        m = int(m)
        if m not in (0, 1, 2, 4, 8, 16, 32, 64, 128, 256):
            raise PdeError("runtime", "blendMode() takes BLEND, ADD, SUBTRACT, LIGHTEST, DARKEST, DIFFERENCE,"
                                      " EXCLUSION, MULTIPLY, SCREEN or REPLACE")
        self.st.blend = m
        self.canvas.blend = m

    def image_mode(self, m):
        self.st.image_mode = int(m)

    def create_graphics(self, w, h, renderer=None):
        w, h = int(w), int(h)
        if not (0 < w <= W and 0 < h <= H):
            raise PdeError("runtime", f"createGraphics({w}, {h}): at most 240 x 128 here")
        if renderer is not None and renderer == P3D_NAME:
            raise PdeError("runtime", "createGraphics() with P3D is not available here (draw 3D on the sketch itself)")
        self.budget.allocate(2 * W * H + 256)
        api = Api(self.budget, self.now)
        api.canvas = raster.TwinCanvas()
        api.canvas.aa = self.canvas.aa
        api.rand = self.rand
        return PGr(w, h, api)

    def create_image(self, w, h, fmt=1):
        w, h = int(w), int(h)
        if not (0 < w <= 1024 and 0 < h <= 1024 and w * h <= 262144):
            raise PdeError("memory", f"createImage({w}, {h}) is too big")
        self.budget.allocate(4 * w * h)
        img = PImg(w, h, int(fmt))
        if int(fmt) == 1:
            for i in range(w * h):
                img.pixels[i] = -16777216
        return img

    def begin_draw(self):
        self.reset_matrix()

    def end_draw(self):
        pass

    def get_image(self, x=0, y=0, w=W, h=H) -> PImg:
        """get() / get(x, y, w, h): a copy of the canvas (or this surface) as a PImage."""
        x, y, w, h = int(x), int(y), int(w), int(h)
        if w <= 0 or h <= 0 or w * h > 262144:
            raise PdeError("runtime", "get() of an empty or huge region")
        self.budget.allocate(4 * w * h)
        self.budget.charge((w * h) >> 3)
        buf = self.canvas.buf
        cov = self.canvas.cov if type(self.canvas) is raster.TwinCanvas else None
        px = array("i", bytes(4 * w * h))
        for j in range(h):
            yy = y + j
            if not 0 <= yy < H:
                continue
            for i in range(w):
                xx = x + i
                if 0 <= xx < W:
                    k = yy * W + xx
                    if cov is None:
                        g = buf[k]
                        px[j * w + i] = _jint(0xFF000000 | (g << 16) | (g << 8) | g)
                    else:
                        a = 255 - cov[k]
                        if a > 0:
                            # the surface's premultiplied ink back to a colour at alpha a
                            g = 255 - min(255, ((255 - buf[k]) * 255 + a // 2) // a)
                            px[j * w + i] = _jint((a << 24) | (g << 16) | (g << 8) | g)
        return PImg(w, h, 2, px)

    def _src(self, img):
        """(width, height, sample(i, j) -> (grey, alpha)) of a PImage or PGraphics."""
        if isinstance(img, PGr):
            cv = img.api.canvas
            buf, cov = cv.buf, cv.cov

            def sample(i, j):
                k = j * W + i
                return buf[k], 255 - cov[k]
            return img.w, img.h, sample, True
        px, iw = img.pixels, img.w
        opaque = img.fmt == 1

        def sample_i(i, j):
            c = px[j * iw + i]
            return luma(c), 255 if opaque else (c >> 24) & 255
        return img.w, img.h, sample_i, False

    def image(self, img, x, y, w=None, h=None):
        if img is None:
            raise PdeError("runtime", "image() of a null image")
        if self.m3 is not None or (self.t3 is not None and (not self.t3.cam_default or not self.t3.proj_default)):
            raise PdeError("runtime", "image() under a 3D transform is not available here")
        self.image_at(img, x, y, w, h)

    def image_at(self, img, x, y, w, h, raw=False):
        sw, sh, sample, premul = self._src(img)
        mode = self.st.image_mode
        if w is None:
            w, h = float(sw), float(sh)
        if mode == 1:            # CORNERS
            x0, y0, x1, y1 = x, y, w, h
        elif mode == 3:          # CENTER
            x0, y0, x1, y1 = x - w / 2, y - h / 2, x + w / 2, y + h / 2
        else:
            x0, y0, x1, y1 = x, y, x + w, y + h
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        dw, dh = x1 - x0, y1 - y0
        if dw <= 0 or dh <= 0:
            return
        if raw:
            inv = lambda px_, py_: (px_, py_)     # noqa: E731
            corners = [(x0, y0), (x1, y1)]
        else:
            inv = self._inverse()
            if inv is None:
                return
            corners = self._xf([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
        bx0 = max(0, int(math.floor(min(c[0] for c in corners))))
        bx1 = min(W, int(math.ceil(max(c[0] for c in corners))))
        by0 = max(0, int(math.floor(min(c[1] for c in corners))))
        by1 = min(H, int(math.ceil(max(c[1] for c in corners))))
        if bx1 <= bx0 or by1 <= by0:
            return
        self.budget.charge(((bx1 - bx0) * (by1 - by0)) >> 2)
        buf = self.canvas.buf
        twin = self.canvas.cov if type(self.canvas) is raster.TwinCanvas else None
        kx, ky = sw / dw, sh / dh
        for py in range(by0, by1):
            base = py * W
            for px_ in range(bx0, bx1):
                u, v = inv(px_ + 0.5, py + 0.5) if not raw else (px_ + 0.5, py + 0.5)
                if not (x0 <= u < x1 and y0 <= v < y1):
                    continue
                i = int((u - x0) * kx)
                j = int((v - y0) * ky)
                if i >= sw or j >= sh:
                    continue
                g, a = sample(i, j)
                k = base + px_
                if raw:
                    buf[k] = g
                    if twin is not None:
                        twin[k] = 255 - a
                    continue
                if a <= 0:
                    continue
                m = buf[k]
                if premul:
                    ink = (255 - m) * (255 - a) / 255.0 + (255 - g)
                    out = 255 - int(ink + 0.5)
                    buf[k] = 0 if out < 0 else out
                elif a >= 255:
                    buf[k] = g
                else:
                    buf[k] = int(m + (g - m) * (a / 255.0) + 0.5)
                if twin is not None:
                    twin[k] = (twin[k] * (255 - a) + 127) // 255

    def copy_region(self, *a):
        """copy(sx, sy, sw, sh, dx, dy, dw, dh) within the canvas, or copy(img, sx, sy, sw, sh, dx, dy, dw, dh)."""
        if len(a) == 9:
            img, a = a[0], a[1:]
            if img is None:
                raise PdeError("runtime", "copy() from a null image")
        else:
            img = self.get_image()
        sx, sy, sw, sh, dx, dy, dw, dh = (int(v) for v in a)
        if sw <= 0 or sh <= 0 or dw <= 0 or dh <= 0:
            return
        src_w, src_h, sample, _ = self._src(img)
        buf = self.canvas.buf
        self.budget.charge((dw * dh) >> 2)
        for j in range(dh):
            yy = dy + j
            if not 0 <= yy < H:
                continue
            v = sy + (j * sh) // dh
            for i in range(dw):
                xx = dx + i
                if not 0 <= xx < W:
                    continue
                u = sx + (i * sw) // dw
                if 0 <= u < src_w and 0 <= v < src_h:
                    g, al = sample(u, v)
                    if al > 0:
                        buf[yy * W + xx] = g if al >= 255 else int(buf[yy * W + xx] + (g - buf[yy * W + xx]) * al / 255.0 + 0.5)

    def filter(self, kind, param=None):
        """filter(): PImage.filter on the grey canvas (THRESHOLD, GRAY, INVERT, POSTERIZE, BLUR,
        ERODE, DILATE, OPAQUE)."""
        kind = int(kind)
        buf = self.canvas.buf
        n = W * H
        self.budget.charge(n >> 2)
        if kind == 16:
            t = int(f32(0.5 if param is None else float(param)) * 255)
            buf[:] = buf.translate(bytes(0 if v < t else 255 for v in range(256)))
        elif kind == 12 or kind == 14:
            if param is not None:
                raise PdeError("runtime", "filter(GRAY) and filter(OPAQUE) take no number")
        elif kind == 13:
            if param is not None:
                raise PdeError("runtime", "Use filter(INVERT) instead of filter(INVERT, param)")
            buf[:] = buf.translate(bytes(255 - v for v in range(256)))
        elif kind == 15:
            if param is None:
                raise PdeError("runtime", "Use filter(POSTERIZE, int levels) instead of filter(POSTERIZE)")
            lv = int(param)
            if lv < 2 or lv > 255:
                raise PdeError("runtime", "Levels must be between 2 and 255 for filter(POSTERIZE, levels)")
            buf[:] = buf.translate(bytes((((v * lv) >> 8) * 255) // (lv - 1) for v in range(256)))
        elif kind in (17, 18):
            if param is not None:
                raise PdeError("runtime", "ERODE and DILATE take no number")
            pick = min if kind == 17 else max
            src = bytes(buf)
            for y in range(H):
                b = y * W
                up = b - W if y > 0 else b
                dn = b + W if y < H - 1 else b
                for x in range(W):
                    k = b + x
                    l = k - 1 if x > 0 else k
                    r = k + 1 if x < W - 1 else k
                    buf[k] = pick(src[k], src[l], src[r], src[up + x], src[dn + x])
            self.budget.charge(n)
        elif kind == 11:
            self._blur(1.0 if param is None else float(param))
        else:
            raise PdeError("runtime", "filter() takes THRESHOLD, GRAY, INVERT, POSTERIZE, BLUR, ERODE, DILATE or OPAQUE")

    def _blur(self, r: float) -> None:
        """PImage.blurRGB on one channel, its kernel from buildBlurKernel (integer arithmetic)."""
        r = min(r, f32(f32(min(W, H) / 2.0) / 3.5))
        radius = int(f32(r * 3.5))
        radius = max(1, min(248, radius))
        size = (1 + radius) << 1                   # Java: 1 + blurRadius<<1
        kern = [0] * size
        ri = radius - 1
        for i in range(1, radius):
            kern[radius + i] = kern[ri] = ri * ri
            ri -= 1
        kern[radius] = radius * radius
        buf = self.canvas.buf
        src = bytes(buf)
        tmp = [0] * (W * H)
        for y in range(H):
            yi = y * W
            for x in range(W):
                c = s_ = 0
                read = x - radius
                if read < 0:
                    bk0 = -read
                    read = 0
                else:
                    bk0 = 0
                for i in range(bk0, size):
                    if read >= W:
                        break
                    kv = kern[i]
                    c += kv * src[read + yi]
                    s_ += kv
                    read += 1
                tmp[yi + x] = c // s_ if s_ else c
        ym = -radius
        for y in range(H):
            yi = y * W
            for x in range(W):
                c = s_ = 0
                if ym < 0:
                    bk0 = ri_ = -ym
                    read = x
                else:
                    bk0, ri_, read = 0, ym, x + ym * W
                for i in range(bk0, size):
                    if ri_ >= H:
                        break
                    kv = kern[i]
                    c += kv * tmp[read]
                    s_ += kv
                    ri_ += 1
                    read += W
                buf[yi + x] = c // s_ if s_ else c
            ym += 1
        self.budget.charge(W * H * size >> 2)

    # ================================================================== randomness
    def random(self, *a):
        return self.rand.random(float(a[0])) if len(a) == 1 else self.rand.random2(float(a[0]), float(a[1]))

    def random_seed(self, s):
        self.last_rseed = int(s)
        self.rand.seed(int(s))

    def random_gaussian(self):
        return self.rand.gaussian()

    def noise(self, *a):
        self.budget.charge(4 * self.rand.octaves)
        return self.rand.noise(*[float(v) for v in a])

    def noise_seed(self, s):
        self.rand.noise_seed(int(s))

    def noise_detail(self, lod, falloff=None):
        self.rand.noise_detail(int(lod), falloff)

    # ================================================================== output
    def grey(self) -> bytes:
        return bytes(self.canvas.buf)


BAYER4 = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))
_LEVEL = [((255 - g) * 16 + 127) // 255 for g in range(256)]
# per (y & 3, x & 3): grey -> b"1" (ink) or b"0", so a row dithers with four C-speed translates
_DITHER = [[bytes(0x31 if _LEVEL[g] > BAYER4[yy][xx] else 0x30 for g in range(256)) for xx in range(4)]
           for yy in range(4)]


def to_rows(grey: bytes) -> list[int]:
    """Grey bytes (0 = black) -> 1-bit rows, bit x = pixel x, 1 = ink: arm A's `texture flat` dither,
    the ordered dither: level = ((255 - g) * 16 + 127) // 255, ink where
    level > BAYER4[y & 3][x & 3] (so the oracle's frames compare 1:1)."""
    rows = []
    out = bytearray(W)
    for y in range(H):
        line = grey[y * W:(y + 1) * W]
        tt = _DITHER[y & 3]
        for px in range(4):
            out[px::4] = line[px::4].translate(tt[px])
        rows.append(int(out[::-1], 2))
    return rows
