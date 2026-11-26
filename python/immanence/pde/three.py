"""P3D: a software 3D pipeline for the 240x128 grey canvas (Processing's OpenGL renderer, in pure Python).

SPDX-License-Identifier: LGPL-2.1-only
This file follows Processing's own source (PGraphicsOpenGL, InGeometry and its shaders, Processing
4.5.2, GNU LGPL 2.1) to draw 3D exactly as Processing does. See THIRD-PARTY.md.

`size(240, 128, P3D)` turns it on. What it follows (PGraphicsOpenGL, Processing 4.5.2 sources):

* matrices: modelview = camera * the sketch's transforms (4x4, row-major like PMatrix3D); the
  default camera looks from (120, 64, 64 / tan(30 deg)) at (120, 64, 0), so z = 0 is the ordinary
  2D canvas; perspective(60 deg, 240 / 128, z / 10, z * 10) by default; ortho() and frustum().
  camera() and the projection persist across frames; the transforms start again every frame.
* clipping against the near plane (clip space z >= -w), then pixel centres: a pixel is covered
  when its centre (x + 0.5, y + 0.5) is inside (non-zero winding), GL's rule, not Java2D's.
* a depth buffer (NDC z, float32, cleared to 1.0 every frame and by background()), test z <= depth
  (GL_LEQUAL); strokes are pulled toward the eye (eye space * 0.999, PGL.STROKE_DISPLACEMENT) so
  edges win over their own faces; lines are screen-space quads strokeWeight wide, points small
  polygons (LineVert.glsl / PointVert.glsl).
* lighting per vertex exactly as LightVert.glsl: ambient, directional, point and spot lights with
  falloff, Lambert diffuse, Blinn-Phong specular, emissive; two-sided (a back face lights with the
  inverted normal); normals from normal(), box/sphere's own, or computed per triangle like
  InGeometry.calc*Normals. A face whose vertices share one colour is painted flat with the colour
  quantised as the GPU does (round(c * 255) per channel, then the harness's luma); otherwise
  Gouraud, the grey interpolated across the triangle.
* box() and sphere() are InGeometry.addBox / addSphere (sphereDetail's tables included), so a
  wireframe shows the same edges as Processing.

The texture extension works on 3D fills too (the pattern in screen space, its darkness from
the lit grey). Everything a sketch draws in 3D is charged to the frame budget by the pixels and
triangles it touches. The C++ port (src/pde_three.cpp) follows this
file operation for operation (same double arithmetic, same order), so frames match bit for bit.
"""
from __future__ import annotations

import math
from array import array

from . import raster
from .jrandom import f32

W, H = raster.W, raster.H
NEAR_EPS = 1e-9
DISPLACE = 0.999                  # PGL.STROKE_DISPLACEMENT
MAX_LIGHTS = 8
MAX_VERTS = 65536                 # vertices in one 3D shape (a sphere at detail 200 is 40k)

IDENT = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)


# ---------------------------------------------------------------------------- matrices (row-major)
def mmul(a, b) -> list:
    """a * b for 4x4 row-major matrices."""
    out = [0.0] * 16
    for r in range(4):
        a0, a1, a2, a3 = a[r * 4], a[r * 4 + 1], a[r * 4 + 2], a[r * 4 + 3]
        for c in range(4):
            out[r * 4 + c] = a0 * b[c] + a1 * b[4 + c] + a2 * b[8 + c] + a3 * b[12 + c]
    return out


def look_at(ex, ey, ez, cx, cy, cz, ux, uy, uz) -> list:
    """PGraphicsOpenGL.camera(eye, center, up) -- the camera (= modelview) matrix."""
    z0, z1, z2 = ex - cx, ey - cy, ez - cz
    d = math.sqrt(z0 * z0 + z1 * z1 + z2 * z2)
    if d != 0.0:
        z0, z1, z2 = z0 / d, z1 / d, z2 / d
    y0, y1, y2 = ux, uy, uz
    x0 = y1 * z2 - y2 * z1
    x1 = -y0 * z2 + y2 * z0
    x2 = y0 * z1 - y1 * z0
    y0 = z1 * x2 - z2 * x1
    y1 = -z0 * x2 + z2 * x0
    y2 = z0 * x1 - z1 * x0
    xm = math.sqrt(x0 * x0 + x1 * x1 + x2 * x2)
    if xm != 0.0:
        x0, x1, x2 = x0 / xm, x1 / xm, x2 / xm
    ym = math.sqrt(y0 * y0 + y1 * y1 + y2 * y2)
    if ym != 0.0:
        y0, y1, y2 = y0 / ym, y1 / ym, y2 / ym
    m = [x0, x1, x2, 0.0, y0, y1, y2, 0.0, z0, z1, z2, 0.0, 0.0, 0.0, 0.0, 1.0]
    # modelview.translate(-eye)
    tx, ty, tz = -ex, -ey, -ez
    for r in range(3):
        m[r * 4 + 3] = m[r * 4] * tx + m[r * 4 + 1] * ty + m[r * 4 + 2] * tz + m[r * 4 + 3]
    return m


def frustum_m(left, right, bottom, top, near, far) -> list:
    n2 = 2.0 * near
    w = right - left
    h = top - bottom
    d = far - near
    return [n2 / w, 0.0, (right + left) / w, 0.0,
            0.0, -n2 / h, (top + bottom) / h, 0.0,
            0.0, 0.0, -(far + near) / d, -(n2 * far) / d,
            0.0, 0.0, -1.0, 0.0]


def ortho_m(left, right, bottom, top, near, far) -> list:
    w = right - left
    h = top - bottom
    d = far - near
    return [2.0 / w, 0.0, 0.0, -(right + left) / w,
            0.0, -(2.0 / h), 0.0, -(top + bottom) / h,
            0.0, 0.0, -2.0 / d, -(far + near) / d,
            0.0, 0.0, 0.0, 1.0]


FOV = 60.0 * math.pi / 180.0
CAM_Z = (H / 2.0) / math.tan(FOV / 2.0)


def default_camera() -> list:
    return look_at(W / 2.0, H / 2.0, CAM_Z, W / 2.0, H / 2.0, 0.0, 0.0, 1.0, 0.0)


def perspective_m(fov, aspect, near, far) -> list:
    ymax = near * math.tan(fov / 2.0)
    ymin = -ymax
    return frustum_m(ymin * aspect, ymax * aspect, ymin, ymax, near, far)


def default_projection() -> list:
    return perspective_m(FOV, W / H, CAM_Z / 10.0, CAM_Z * 10.0)


def inv3(m) -> list | None:
    """The inverse of the upper-left 3x3 of a 4x4 (row-major 3x3 result), None if singular."""
    a, b, c = m[0], m[1], m[2]
    d, e, f = m[4], m[5], m[6]
    g, h, i = m[8], m[9], m[10]
    A = e * i - f * h
    B = f * g - d * i
    C = d * h - e * g
    det = a * A + b * B + c * C
    if det == 0.0 or det != det:
        return None
    k = 1.0 / det
    return [A * k, (c * h - b * i) * k, (b * f - c * e) * k,
            B * k, (a * i - c * g) * k, (c * d - a * f) * k,
            C * k, (b * g - a * h) * k, (a * e - b * d) * k]


def invert4(m) -> list | None:
    """General 4x4 inverse (for screen/model queries), None if singular."""
    a = list(m)
    inv = [0.0] * 16
    inv[0] = a[5] * a[10] * a[15] - a[5] * a[11] * a[14] - a[9] * a[6] * a[15] + a[9] * a[7] * a[14] + a[13] * a[6] * a[11] - a[13] * a[7] * a[10]
    inv[4] = -a[4] * a[10] * a[15] + a[4] * a[11] * a[14] + a[8] * a[6] * a[15] - a[8] * a[7] * a[14] - a[12] * a[6] * a[11] + a[12] * a[7] * a[10]
    inv[8] = a[4] * a[9] * a[15] - a[4] * a[11] * a[13] - a[8] * a[5] * a[15] + a[8] * a[7] * a[13] + a[12] * a[5] * a[11] - a[12] * a[7] * a[9]
    inv[12] = -a[4] * a[9] * a[14] + a[4] * a[10] * a[13] + a[8] * a[5] * a[14] - a[8] * a[6] * a[13] - a[12] * a[5] * a[10] + a[12] * a[6] * a[9]
    inv[1] = -a[1] * a[10] * a[15] + a[1] * a[11] * a[14] + a[9] * a[2] * a[15] - a[9] * a[3] * a[14] - a[13] * a[2] * a[11] + a[13] * a[3] * a[10]
    inv[5] = a[0] * a[10] * a[15] - a[0] * a[11] * a[14] - a[8] * a[2] * a[15] + a[8] * a[3] * a[14] + a[12] * a[2] * a[11] - a[12] * a[3] * a[10]
    inv[9] = -a[0] * a[9] * a[15] + a[0] * a[11] * a[13] + a[8] * a[1] * a[15] - a[8] * a[3] * a[13] - a[12] * a[1] * a[11] + a[12] * a[3] * a[9]
    inv[13] = a[0] * a[9] * a[14] - a[0] * a[10] * a[13] - a[8] * a[1] * a[14] + a[8] * a[2] * a[13] + a[12] * a[1] * a[10] - a[12] * a[2] * a[9]
    inv[2] = a[1] * a[6] * a[15] - a[1] * a[7] * a[14] - a[5] * a[2] * a[15] + a[5] * a[3] * a[14] + a[13] * a[2] * a[7] - a[13] * a[3] * a[6]
    inv[6] = -a[0] * a[6] * a[15] + a[0] * a[7] * a[14] + a[4] * a[2] * a[15] - a[4] * a[3] * a[14] - a[12] * a[2] * a[7] + a[12] * a[3] * a[6]
    inv[10] = a[0] * a[5] * a[15] - a[0] * a[7] * a[13] - a[4] * a[1] * a[15] + a[4] * a[3] * a[13] + a[12] * a[1] * a[7] - a[12] * a[3] * a[5]
    inv[14] = -a[0] * a[5] * a[14] + a[0] * a[6] * a[13] + a[4] * a[1] * a[14] - a[4] * a[2] * a[13] - a[12] * a[1] * a[6] + a[12] * a[2] * a[5]
    inv[3] = -a[1] * a[6] * a[11] + a[1] * a[7] * a[10] + a[5] * a[2] * a[11] - a[5] * a[3] * a[10] - a[9] * a[2] * a[7] + a[9] * a[3] * a[6]
    inv[7] = a[0] * a[6] * a[11] - a[0] * a[7] * a[10] - a[4] * a[2] * a[11] + a[4] * a[3] * a[10] + a[8] * a[2] * a[7] - a[8] * a[3] * a[6]
    inv[11] = -a[0] * a[5] * a[11] + a[0] * a[7] * a[9] + a[4] * a[1] * a[11] - a[4] * a[3] * a[9] - a[8] * a[1] * a[7] + a[8] * a[3] * a[5]
    inv[15] = a[0] * a[5] * a[10] - a[0] * a[6] * a[9] - a[4] * a[1] * a[10] + a[4] * a[2] * a[9] + a[8] * a[1] * a[6] - a[8] * a[2] * a[5]
    det = a[0] * inv[0] + a[1] * inv[4] + a[2] * inv[8] + a[3] * inv[12]
    if det == 0.0 or det != det:
        return None
    k = 1.0 / det
    return [v * k for v in inv]


# ---------------------------------------------------------------------------- sphereDetail's tables
SINCOS_LENGTH = 720
_DTR = f32(f32(math.pi) / 180.0)
SIN_LUT = [f32(math.sin(f32(f32(i * _DTR) * 0.5))) for i in range(SINCOS_LENGTH)]
COS_LUT = [f32(math.cos(f32(f32(i * _DTR) * 0.5))) for i in range(SINCOS_LENGTH)]


def sphere_tables(ures: int, vres: int) -> tuple[list, list, list]:
    """PGraphics.sphereDetail(ures, vres): the unit sphere's ring vertices (float32, as Java)."""
    delta = f32(SINCOS_LENGTH / ures)
    cx = [COS_LUT[int(f32(i * delta)) % SINCOS_LENGTH] for i in range(ures)]
    cz = [SIN_LUT[int(f32(i * delta)) % SINCOS_LENGTH] for i in range(ures)]
    xs, ys, zs = [], [], []
    step = f32(SINCOS_LENGTH * 0.5 / vres)
    angle = step
    for _ in range(1, vres):
        rad = SIN_LUT[int(angle) % SINCOS_LENGTH]
        cy = COS_LUT[int(angle) % SINCOS_LENGTH]
        for j in range(ures):
            xs.append(f32(cx[j] * rad))
            ys.append(cy)
            zs.append(f32(cz[j] * rad))
        angle = f32(angle + step)
    return xs, ys, zs


def box_mesh(w: float, h: float, d: float) -> tuple[list, list, list, list]:
    """InGeometry.addBox: 24 vertices, their normals, 6 quads, 24 edges (4 per face)."""
    nx = -1.0 if (h > 0) != (d > 0) else 1.0
    ny = -1.0 if (w > 0) != (d > 0) else 1.0
    nz = -1.0 if (w > 0) != (h > 0) else 1.0
    x1, x2 = -w / 2.0, w / 2.0
    y1, y2 = -h / 2.0, h / 2.0
    z1, z2 = -d / 2.0, d / 2.0
    faces_v = (
        ((0.0, 0.0, -nz), ((x1, y1, z1), (x1, y2, z1), (x2, y2, z1), (x2, y1, z1))),     # back
        ((0.0, 0.0, nz), ((x1, y2, z2), (x1, y1, z2), (x2, y1, z2), (x2, y2, z2))),      # front
        ((nx, 0.0, 0.0), ((x2, y1, z1), (x2, y2, z1), (x2, y2, z2), (x2, y1, z2))),      # right
        ((-nx, 0.0, 0.0), ((x1, y2, z1), (x1, y1, z1), (x1, y1, z2), (x1, y2, z2))),     # left
        ((0.0, -ny, 0.0), ((x2, y1, z1), (x2, y1, z2), (x1, y1, z2), (x1, y1, z1))),     # top
        ((0.0, ny, 0.0), ((x1, y2, z1), (x1, y2, z2), (x2, y2, z2), (x2, y2, z1))),      # bottom
    )
    verts, norms, faces, edges = [], [], [], []
    for n, quad in faces_v:
        k = len(verts)
        for v in quad:
            verts.append(v)
            norms.append(n)
        faces.append((k, k + 1, k + 2, k + 3))
        edges.extend(((k, k + 1), (k + 1, k + 2), (k + 2, k + 3), (k + 3, k)))
    return verts, norms, faces, edges


def sphere_mesh(r: float, du: int, dv: int, tables) -> tuple[list, list, list, list]:
    """InGeometry.addSphere: vertices (poles repeated per column), normals, triangles, edges."""
    sx, sy, sz = tables
    verts, norms = [], []
    for _ in range(du):
        verts.append((0.0, r, 0.0))
        norms.append((0.0, 1.0, 0.0))
    for i in range(du):
        verts.append((r * sx[i], r * sy[i], r * sz[i]))
        norms.append((sx[i], sy[i], sz[i]))
    verts.append((r * sx[0], r * sy[0], r * sz[0]))
    norms.append((sx[0], sy[0], sz[0]))
    tris, edges = [], []
    vert0 = du
    for i in range(du):
        i1 = vert0 + i
        i0 = vert0 + i - du
        tris.append((i1, i0, i1 + 1))
        edges.append((i0, i1))
        edges.append((i1, i1 + 1))
    offset = 0
    for _j in range(2, dv):
        offset += du
        vert0 = len(verts)
        for i in range(du):
            k = offset + i
            verts.append((r * sx[k], r * sy[k], r * sz[k]))
            norms.append((sx[k], sy[k], sz[k]))
        vert1 = len(verts)
        verts.append((r * sx[offset], r * sy[offset], r * sz[offset]))
        norms.append((sx[offset], sy[offset], sz[offset]))
        for i in range(du):
            i1 = vert0 + i
            i0 = vert0 + i - du - 1
            tris.append((i1, i0, i0 + 1))
            tris.append((i1, i0 + 1, i1 + 1))
            edges.append((i0, i1))
            edges.append((i1, i1 + 1))
            edges.append((i0 + 1, i1))
        tris.append((vert1, vert1 - du, vert1 - 1))
    for _ in range(du):
        verts.append((0.0, -r, 0.0))
        norms.append((0.0, -1.0, 0.0))
    for i in range(du):
        i0 = vert0 + i
        i1 = vert0 + i + du + 1
        tris.append((i1, i0, i0 + 1))
        edges.append((i0, i1))
    return verts, norms, tris, edges


def tri_normal(p0, p1, p2) -> tuple[float, float, float]:
    """InGeometry.calcTriangleNormal: n = (v2 - v1) x (v0 - v1), normalised."""
    ax, ay, az = p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2]
    bx, by, bz = p0[0] - p1[0], p0[1] - p1[1], p0[2] - p1[2]
    nx = ay * bz - by * az
    ny = az * bx - bz * ax
    nz = ax * by - bx * ay
    d = math.sqrt(nx * nx + ny * ny + nz * nz)
    if d == 0.0:
        return (0.0, 0.0, 0.0)
    return (nx / d, ny / d, nz / d)


def newell(pts) -> tuple[float, float, float]:
    """The (unnormalised) Newell normal of a closed 3D polygon."""
    nx = ny = nz = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0, z0 = pts[i]
        x1, y1, z1 = pts[(i + 1) % n]
        nx += (y0 - y1) * (z0 + z1)
        ny += (z0 - z1) * (x0 + x1)
        nz += (x0 - x1) * (y0 + y1)
    return nx, ny, nz


# ---------------------------------------------------------------------------- the rasteriser
def _plane(contours, k) -> tuple[float, float, float] | None:
    """The plane v = a*x + b*y + c through screen points (x, y, ..., v at index k) of all contours
    (Newell's method), None when the polygon is edge-on (no area on the screen)."""
    nx = ny = nz = 0.0
    sx = sy = sv = 0.0
    cnt = 0
    for c in contours:
        n = len(c)
        for i in range(n):
            p = c[i]
            q = c[(i + 1) % n]
            nx += (p[1] - q[1]) * (p[k] + q[k])
            ny += (p[k] - q[k]) * (p[0] + q[0])
            nz += (p[0] - q[0]) * (p[1] + q[1])
            sx += p[0]
            sy += p[1]
            sv += p[k]
            cnt += 1
    if cnt < 3 or not (abs(nz) > 1e-9):
        return None
    a = -nx / nz
    b = -ny / nz
    c = sv / cnt - a * (sx / cnt) - b * (sy / cnt)
    return a, b, c


def _pix(v: float) -> int:
    """The first pixel whose centre is at or after v: ceil(v - 0.5); far or NaN values clamp
    (1e9 for far right/below, -1 for far left/above and NaN) without an exception."""
    if -1e9 < v < 1e9:
        return math.ceil(v - 0.5)
    return 1000000000 if v >= 1e9 else -1


def _signed2(contours) -> float:
    """Twice the signed screen area (y down) of all contours: > 0 is a front face (GL frontFace CW
    in window coordinates, with Processing's flipped y)."""
    s = 0.0
    for c in contours:
        n = len(c)
        for i in range(n):
            p = c[i]
            q = c[(i + 1) % n]
            s += (p[0] - q[0]) * (p[1] + q[1])
    return s


class Three:
    """The 3D state of one surface (camera, projection, lights, depth, sphere detail) and the
    drawing of 3D geometry onto its canvas. Owned by runtime.Api when the sketch is P3D."""

    def __init__(self, api) -> None:
        self.api = api
        self.cam = default_camera()
        self.cam_default = True
        self.proj = default_projection()
        self.proj_default = True
        self.ortho = False
        self.depth = array("f", [1.0]) * (W * H)
        self.dirty = False              # anything drawn through the 3D path since the depth was cleared
        self.depth_test = True
        self.depth_mask = True
        self.lighting = False
        self.lights: list = []
        self.fall = (1.0, 0.0, 0.0)
        self.lspec = (0.0, 0.0, 0.0)
        self.normal = (0.0, 0.0, 1.0)
        self.su = self.sv = 0
        self.stab = None
        self.eye_dist = CAM_Z
        self.stats = [0, 0]             # (polygons, pixels) drawn, for measurements

    # ------------------------------------------------------------------ frame
    def begin_frame(self) -> None:
        """PGraphicsOpenGL.setGLSettings: depth cleared, lights off, the normal back to +z."""
        self.clear_depth()
        self.lighting = False
        self.lights = []
        self.fall = (1.0, 0.0, 0.0)
        self.lspec = (0.0, 0.0, 0.0)
        self.normal = (0.0, 0.0, 1.0)

    def clear_depth(self) -> None:
        self.depth[:] = array("f", [1.0]) * (W * H)
        self.dirty = False

    # ------------------------------------------------------------------ camera and projection
    def set_camera(self, m) -> None:
        self.cam = m
        self.cam_default = m == default_camera()

    def set_projection(self, m, ortho: bool) -> None:
        self.proj = m
        self.ortho = ortho
        self.proj_default = (not ortho) and m == default_projection()

    # ------------------------------------------------------------------ lights
    def add_light(self, kind, pos, w, nrm, amb, dif, spec, fall, spot) -> None:
        from .errors import PdeError
        if len(self.lights) >= MAX_LIGHTS:
            raise PdeError("runtime", f"can only create {MAX_LIGHTS} lights")
        self.lighting = True
        self.lights.append((kind, pos, w, nrm, amb, dif, spec, fall, spot))

    # ------------------------------------------------------------------ shading
    def shade(self, ex, ey, ez, nx, ny, nz, mat) -> tuple[float, float, float]:
        """LightVert.glsl for one vertex and one facing: (R, G, B), unclamped."""
        tar = tag = tab = 0.0
        tdr = tdg = tdb = 0.0
        tsr = tsg = tsb = 0.0
        for kind, pos, w, ln, amb, dif, spec, fall, spot in self.lights:
            if w < 1.0:
                falloff = 1.0
                ldx, ldy, ldz = -ln[0], -ln[1], -ln[2]
            else:
                lx, ly, lz = pos[0] - ex, pos[1] - ey, pos[2] - ez
                dd = lx * lx + ly * ly + lz * lz
                d = math.sqrt(dd)
                den = fall[0] + d * fall[1] + dd * fall[2]
                falloff = 1.0 / den if den != 0.0 else 0.0
                if d > 0.0:
                    ldx, ldy, ldz = lx / d, ly / d, lz / d
                else:
                    ldx = ldy = ldz = 0.0
            if spot[1] > 0.0:
                lx, ly, lz = pos[0] - ex, pos[1] - ey, pos[2] - ez
                d = math.sqrt(lx * lx + ly * ly + lz * lz)
                if d > 0.0:
                    lx, ly, lz = lx / d, ly / d, lz / d
                sc = -ln[0] * lx - ln[1] * ly - ln[2] * lz
                if sc > 1.0:
                    sc = 1.0
                spotf = 0.0 if sc <= spot[0] else math.pow(sc, spot[1])
            else:
                spotf = 1.0
            if amb[0] > 0.0 or amb[1] > 0.0 or amb[2] > 0.0:
                tar += amb[0] * falloff
                tag += amb[1] * falloff
                tab += amb[2] * falloff
            if dif[0] > 0.0 or dif[1] > 0.0 or dif[2] > 0.0:
                lam = ldx * nx + ldy * ny + ldz * nz
                if lam < 0.0:
                    lam = 0.0
                f = falloff * spotf * lam
                tdr += dif[0] * f
                tdg += dif[1] * f
                tdb += dif[2] * f
            if spec[0] > 0.0 or spec[1] > 0.0 or spec[2] > 0.0:
                pd = math.sqrt(ex * ex + ey * ey + ez * ez)
                px, py, pz = (ex / pd, ey / pd, ez / pd) if pd > 0.0 else (0.0, 0.0, 0.0)
                hx, hy, hz = ldx - px, ldy - py, ldz - pz
                hd = math.sqrt(hx * hx + hy * hy + hz * hz)
                if hd > 0.0:
                    hx, hy, hz = hx / hd, hy / hd, hz / hd
                bp = hx * nx + hy * ny + hz * nz
                if bp > 1.0:
                    bp = 1.0
                if bp > 0.0:
                    pw = math.pow(bp, mat[4])
                else:
                    pw = 0.0 if mat[4] > 0.0 else 1.0
                f = falloff * spotf * pw
                tsr += spec[0] * f
                tsg += spec[1] * f
                tsb += spec[2] * f
        fill, ambm, specm, emis = mat[0], mat[1], mat[2], mat[3]
        return (tar * ambm[0] + tdr * fill[0] + tsr * specm[0] + emis[0],
                tag * ambm[1] + tdg * fill[1] + tsg * specm[1] + emis[1],
                tab * ambm[2] + tdb * fill[2] + tsb * specm[2] + emis[2])

    @staticmethod
    def grey_f(rgb) -> float:
        r, g, b = rgb
        r = 0.0 if not r > 0.0 else 1.0 if r > 1.0 else r        # NaN reads as 0
        g = 0.0 if not g > 0.0 else 1.0 if g > 1.0 else g
        b = 0.0 if not b > 0.0 else 1.0 if b > 1.0 else b
        return (r * 299.0 + g * 587.0 + b * 114.0) * 0.255

    @staticmethod
    def grey_i(rgb) -> int:
        """The GPU's round(c * 255) per channel, then the harness's integer luma."""
        out = []
        for c in rgb:
            c = 0.0 if not c > 0.0 else 1.0 if c > 1.0 else c
            out.append(int(c * 255.0 + 0.5))
        return (out[0] * 299 + out[1] * 587 + out[2] * 114) // 1000

    # ------------------------------------------------------------------ raster
    def raster(self, contours, zp, g: int, gp, a: int, painter=None, depth: bool = True) -> None:
        """Fill screen contours (points (x, y, ...)) with depth plane zp, grey g (or the grey plane
        gp), opacity a, non-zero winding, sampled at pixel centres; depth-tested (<=)."""
        api = self.api
        cv = api.canvas
        buf = cv.buf
        D = self.depth
        edges = []
        ymin, ymax = math.inf, -math.inf
        for c in contours:
            for p in c:
                if not (-1e30 < p[0] < 1e30 and -1e30 < p[1] < 1e30):
                    return                      # NaN or runaway coordinates: nothing is drawn
        for c in contours:
            n = len(c)
            if n < 2:
                continue
            px, py = c[n - 1][0], c[n - 1][1]
            for p in c:
                qx, qy = p[0], p[1]
                if qy != py:
                    if py < qy:
                        edges.append((py, qy, px, (qx - px) / (qy - py), 1))
                    else:
                        edges.append((qy, py, qx, (px - qx) / (py - qy), -1))
                    if qy < ymin:
                        ymin = qy
                    if qy > ymax:
                        ymax = qy
                    if py < ymin:
                        ymin = py
                    if py > ymax:
                        ymax = py
                px, py = qx, qy
        if not edges or not (ymin <= ymax):
            return
        r0 = max(0, _pix(ymin))
        r1 = min(H, _pix(ymax))
        if r1 <= r0:
            return
        edges.sort()
        za, zb, zc = zp
        test = depth and self.depth_test
        write = test and self.depth_mask
        tbl = cv.table(g, a) if (a < 255 and gp is None and painter is None) else None
        af = a / 255.0
        pix = 0
        ne = len(edges)
        k = 0
        active: list = []
        for r in range(r0, r1):
            yc = r + 0.5
            while k < ne and edges[k][0] <= yc:
                active.append(edges[k])
                k += 1
            if active:
                active = [e for e in active if e[1] > yc]
            if not active:
                if k >= ne:
                    break
                continue
            xs = []
            for y0, y1, x0, dx, d in active:
                if y0 <= yc:
                    xs.append((x0 + (yc - y0) * dx, d))
            if len(xs) < 2:
                continue
            xs.sort()
            spans = []
            wind = 0
            start = 0.0
            for x, d in xs:
                before = wind
                wind += d
                if before == 0 and wind != 0:
                    start = x
                elif before != 0 and wind == 0 and x > start:
                    if spans and spans[-1][1] >= start:
                        spans[-1] = (spans[-1][0], x)
                    else:
                        spans.append((start, x))
            if not spans:
                continue
            base = r * W
            zr = zb * yc + zc
            trow = painter.row(r) if painter is not None else None
            gr = gp[1] * yc + gp[2] if gp is not None else 0.0
            ga = gp[0] if gp is not None else 0.0
            for xa, xb in spans:
                ia = _pix(xa)
                ib = _pix(xb)
                if ia < 0:
                    ia = 0
                if ib > W:
                    ib = W
                if ib <= ia:
                    continue
                pix += ib - ia
                for x in range(ia, ib):
                    j = base + x
                    if test:
                        z = za * (x + 0.5) + zr
                        if not z <= D[j]:
                            continue
                        if write:
                            D[j] = z
                    if trow is not None:
                        buf[j] = trow[x]
                    elif gp is not None:
                        gf = ga * (x + 0.5) + gr + 0.5
                        gv = 0 if not gf >= 1.0 else 255 if gf >= 255.0 else int(gf)
                        if a >= 255:
                            buf[j] = gv
                        else:
                            v = buf[j]
                            buf[j] = int(v + (gv - v) * af + 0.5)
                    elif tbl is not None:
                        buf[j] = tbl[buf[j]]
                    else:
                        buf[j] = g
        self.stats[0] += 1
        self.stats[1] += pix
        api.budget.charge(8 + ne + (r1 - r0) + (pix >> 2))
        self.dirty = True

    # ------------------------------------------------------------------ geometry
    def transform(self, mv) -> list:
        """projection * modelview."""
        return mmul(self.proj, mv)

    @staticmethod
    def clip_poly(poly: list) -> list:
        """Sutherland-Hodgman against the near plane z >= -w; each vertex is a list
        [cx, cy, cz, cw, attr...], attributes interpolated linearly."""
        out = []
        n = len(poly)
        for i in range(n):
            a = poly[i]
            b = poly[(i + 1) % n]
            da = a[2] + a[3]
            db = b[2] + b[3]
            if da >= 0.0:
                out.append(a)
            if (da >= 0.0) != (db >= 0.0):
                t = da / (da - db)
                out.append([a[m] + (b[m] - a[m]) * t for m in range(len(a))])
        return out

    @staticmethod
    def to_screen(v) -> tuple[float, float, float]:
        iw = 1.0 / v[3]
        return ((v[0] * iw + 1.0) * (W * 0.5), (1.0 - v[1] * iw) * (H * 0.5), v[2] * iw)

    def mesh(self, mv, verts, norms, faces, edges, lit: bool = True, face_norms=None,
             fill: bool = True, stroke: bool = True, mat=None, textured: bool = True, text: bool = False) -> None:
        """Draw polygons (faces: each a list of contours, each a tuple of vertex indices; the first
        contour is the outline, the rest holes) and edges ((i, j) pairs) of a vertex list in model
        space. norms: a normal per vertex, or None and face_norms (one per face) for flat faces."""
        api = self.api
        st = api.st
        n = len(verts)
        if n > MAX_VERTS:
            from .errors import PdeError
            raise PdeError("memory", f"a 3D shape has more than {MAX_VERTS} vertices")
        api.budget.charge(4 + n)
        T = mmul(self.proj, mv)
        t0, t1, t2, t3, t4, t5, t6, t7, t8, t9, t10, t11, t12, t13, t14, t15 = T
        do_fill = fill and (st.fill or text) and st.fill_a > 0 and faces
        do_stroke = stroke and st.stroke and st.stroke_a > 0 and st.sw > 0 and edges
        if do_fill:
            clip = [None] * n
            scr = [None] * n
            for i in range(n):
                x, y, z = verts[i]
                cx = t0 * x + t1 * y + t2 * z + t3
                cy = t4 * x + t5 * y + t6 * z + t7
                cz = t8 * x + t9 * y + t10 * z + t11
                cw = t12 * x + t13 * y + t14 * z + t15
                clip[i] = (cx, cy, cz, cw)
                if cz + cw >= 0.0 and cw > NEAR_EPS:
                    iw = 1.0 / cw
                    scr[i] = ((cx * iw + 1.0) * (W * 0.5), (1.0 - cy * iw) * (H * 0.5), cz * iw)
            self._fill_faces(mv, verts, norms, faces, face_norms, clip, scr, lit and self.lighting, mat,
                             textured)
        if do_stroke:
            self._stroke_edges(mv, verts, edges)

    # ------------------------------------------------------------------ fills
    def _material(self):
        st = self.api.st
        c = st.fill_c
        fill = (((c >> 16) & 255) / 255.0, ((c >> 8) & 255) / 255.0, (c & 255) / 255.0)
        amb = st.amb_c
        amb = fill if amb is None else (((amb >> 16) & 255) / 255.0, ((amb >> 8) & 255) / 255.0, (amb & 255) / 255.0)
        sp = st.spec_c
        spec = (((sp >> 16) & 255) / 255.0, ((sp >> 8) & 255) / 255.0, (sp & 255) / 255.0)
        em = st.emis_c
        emis = (((em >> 16) & 255) / 255.0, ((em >> 8) & 255) / 255.0, (em & 255) / 255.0)
        return (fill, amb, spec, emis, st.shine)

    def _fill_faces(self, mv, verts, norms, faces, face_norms, clip, scr, lit, mat, textured) -> None:
        api = self.api
        st = api.st
        a = st.fill_a
        g_flat = st.fill_g
        painter_on = textured and st.tex is not None
        cache: dict = {}
        if lit:
            if mat is None:
                mat = self._material()
            m0, m1, m2, m4, m5, m6, m8, m9, m10 = mv[0], mv[1], mv[2], mv[4], mv[5], mv[6], mv[8], mv[9], mv[10]
            m3, m7, m11 = mv[3], mv[7], mv[11]
            ni = inv3(mv)

            def eye_normal(nx, ny, nz):
                if ni is None:
                    return 0.0, 0.0, 0.0
                ex = nx * ni[0] + ny * ni[3] + nz * ni[6]
                ey = nx * ni[1] + ny * ni[4] + nz * ni[7]
                ez = nx * ni[2] + ny * ni[5] + nz * ni[8]
                d = math.sqrt(ex * ex + ey * ey + ez * ez)
                if d == 0.0:
                    return 0.0, 0.0, 0.0
                return ex / d, ey / d, ez / d

            def colour(i, front, fn):
                """(float grey, rgb) of vertex i for this facing; fn = the face's own normal."""
                key = (i, front, fn)
                got = cache.get(key)
                if got is not None:
                    return got
                x, y, z = verts[i]
                ex = m0 * x + m1 * y + m2 * z + m3
                ey = m4 * x + m5 * y + m6 * z + m7
                ez = m8 * x + m9 * y + m10 * z + m11
                nn = fn if fn is not None else norms[i]
                nx, ny, nz = eye_normal(nn[0], nn[1], nn[2])
                if not front:
                    nx, ny, nz = -nx, -ny, -nz
                rgb = self.shade(ex, ey, ez, nx, ny, nz, mat)
                got = (self.grey_f(rgb), rgb)
                cache[key] = got
                api.budget.charge(2 + 2 * len(self.lights))
                return got
        for fi, face in enumerate(faces):
            fn = face_norms[fi] if face_norms is not None else None
            need_clip = any(scr[i] is None for c in face for i in c)
            if not need_clip:
                sc = [[scr[i] for i in c] for c in face]
                area = _signed2(sc)
                if area == 0.0:
                    continue
                front = area > 0.0
                if lit:
                    cols = [[colour(i, front, fn) for i in c] for c in face]
                    flat = all(v[0] == cols[0][0][0] for cc in cols for v in cc)
                    if flat:
                        self._paint(sc, None, self.grey_i(cols[0][0][1]), a, painter_on)
                    elif len(face) == 1:
                        pts = [(p[0], p[1], p[2], col[0]) for p, col in zip(sc[0], cols[0])]
                        self._gouraud(pts, a, painter_on)
                    else:
                        g = self.grey_i(cols[0][0][1])
                        self._paint(sc, None, g, a, painter_on)
                else:
                    self._paint(sc, None, g_flat, a, painter_on)
                continue
            # some vertex is behind the near plane: clip, carrying both facings' greys
            polys = []
            for c in face:
                pv = []
                for i in c:
                    cx, cy, cz, cw = clip[i]
                    if lit:
                        gfa, rgba = colour(i, True, fn)
                        gfb, rgbb = colour(i, False, fn)
                        pv.append([cx, cy, cz, cw, gfa, gfb, rgba[0], rgba[1], rgba[2], rgbb[0], rgbb[1], rgbb[2]])
                    else:
                        pv.append([cx, cy, cz, cw])
                pv = self.clip_poly(pv)
                if len(pv) >= 3 and all(v[3] > NEAR_EPS for v in pv):
                    polys.append(pv)
            if not polys:
                continue
            sc = [[self.to_screen(v) for v in pv] for pv in polys]
            area = _signed2(sc)
            if area == 0.0:
                continue
            front = area > 0.0
            if not lit:
                self._paint(sc, None, g_flat, a, painter_on)
                continue
            gi = 4 if front else 5
            ci = 6 if front else 9
            gs = [v[gi] for pv in polys for v in pv]
            if all(x == gs[0] for x in gs):
                v0 = polys[0][0]
                self._paint(sc, None, self.grey_i((v0[ci], v0[ci + 1], v0[ci + 2])), a, painter_on)
            elif len(polys) == 1:
                pts = [(p[0], p[1], p[2], v[gi]) for p, v in zip(sc[0], polys[0])]
                self._gouraud(pts, a, painter_on)
            else:
                v0 = polys[0][0]
                self._paint(sc, None, self.grey_i((v0[ci], v0[ci + 1], v0[ci + 2])), a, painter_on)

    def _painter_for(self, sc, g):
        api = self.api
        st = api.st
        dev = [[(p[0], p[1]) for p in c] for c in sc]
        return api._painter(st.tex, st.tex_p, g, st.tex_g2, dev)

    def _paint(self, sc, gp, g, a, painter_on) -> None:
        zp = _plane(sc, 2)
        if zp is None:
            return
        painter = self._painter_for(sc, g) if painter_on else None
        self.raster(sc, zp, g, gp, a, painter)

    def _gouraud(self, pts, a, painter_on) -> None:
        """A polygon whose vertices differ in grey: as triangles (fan), each with its own planes."""
        for k in range(1, len(pts) - 1):
            tri = [pts[0], pts[k], pts[k + 1]]
            zp = _plane([tri], 2)
            if zp is None:
                continue
            if painter_on:
                gf = (tri[0][3] + tri[1][3] + tri[2][3]) / 3.0 + 0.5
                g = 0 if not gf >= 1.0 else 255 if gf >= 255.0 else int(gf)
                self.raster([tri], zp, g, None, a, self._painter_for([tri], g))
                continue
            gp = _plane([tri], 3)
            if gp is None:
                continue
            self.raster([tri], zp, 0, gp, a)

    # ------------------------------------------------------------------ strokes
    def _stroke_edges(self, mv, verts, edges) -> None:
        """Edges as screen-space quads strokeWeight wide, pulled toward the eye (LineVert.glsl)."""
        st = self.api.st
        n = len(verts)
        m = mv
        P = self.proj
        sx = sy = DISPLACE if not self.ortho else 1.0
        sz = DISPLACE
        bc = [None] * n
        bs = [None] * n
        used = set()
        for i, j in edges:
            used.add(i)
            used.add(j)
        for i in used:
            x, y, z = verts[i]
            ex = (m[0] * x + m[1] * y + m[2] * z + m[3]) * sx
            ey = (m[4] * x + m[5] * y + m[6] * z + m[7]) * sy
            ez = (m[8] * x + m[9] * y + m[10] * z + m[11]) * sz
            ew = m[12] * x + m[13] * y + m[14] * z + m[15]
            cx = P[0] * ex + P[1] * ey + P[2] * ez + P[3] * ew
            cy = P[4] * ex + P[5] * ey + P[6] * ez + P[7] * ew
            cz = P[8] * ex + P[9] * ey + P[10] * ez + P[11] * ew
            cw = P[12] * ex + P[13] * ey + P[14] * ez + P[15] * ew
            bc[i] = (cx, cy, cz, cw)
            if cz + cw >= 0.0 and cw > NEAR_EPS:
                iw = 1.0 / cw
                bs[i] = ((cx * iw + 1.0) * (W * 0.5), (1.0 - cy * iw) * (H * 0.5), cz * iw)
        g, a, sw = st.stroke_g, st.stroke_a, st.sw
        for i, j in edges:
            p, q = bs[i], bs[j]
            if p is None or q is None:
                seg = self.clip_poly_line(bc[i], bc[j])
                if seg is None:
                    continue
                p, q = seg
            self.line_px(p, q, g, a, sw)

    def clip_poly_line(self, a, b):
        da = a[2] + a[3]
        db = b[2] + b[3]
        if da < 0.0 and db < 0.0:
            return None
        if da < 0.0 or db < 0.0:
            t = da / (da - db)
            c = tuple(a[m] + (b[m] - a[m]) * t for m in range(4))
            if da < 0.0:
                a = c
            else:
                b = c
        if a[3] <= NEAR_EPS or b[3] <= NEAR_EPS:
            return None
        return self.to_screen(a), self.to_screen(b)

    def line_px(self, p, q, g, a, sw) -> None:
        """A screen-space line (x, y, z) to (x, y, z), sw pixels wide, depth along it."""
        x0, y0, z0 = p
        x1, y1, z1 = q
        dx, dy = x1 - x0, y1 - y0
        l2 = dx * dx + dy * dy
        if not l2 > 0.0:
            return
        ln = math.sqrt(l2)
        hw = sw * 0.5
        nx, ny = -dy / ln * hw, dx / ln * hw
        quad = [(x0 + nx, y0 + ny), (x1 + nx, y1 + ny), (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)]
        dz = z1 - z0
        za = dz * dx / l2
        zb = dz * dy / l2
        zc = z0 - za * x0 - zb * y0
        self.raster([quad], (za, zb, zc), g, None, a)

    def point_px(self, p, g, a, sw, cap, ts) -> None:
        """PGraphicsOpenGL.tessellateRound/SquarePoints + PointVert.glsl: a camera-facing fan of
        min(200, max(20, int(TWO_PI * sw / 10))) perimeter points (sinLUT/cosLUT steps; the fan
        leaves out its last wedge, as Processing's indices do), or a square, sw * ts across."""
        x, y, z = p
        if cap == 2:           # ROUND
            npt = min(200, max(20, int(f32(f32(f32(2 * math.pi) * f32(sw)) / 10.0)))) + 1
            perim = npt - 1
            inc = f32(SINCOS_LENGTH / perim)
            val = 0.0
            pts = [(x, y)]
            for _ in range(perim):
                i = int(val)
                pts.append((x + 0.5 * COS_LUT[i] * ts * sw, y - 0.5 * SIN_LUT[i] * ts * sw))
                val = f32(math.fmod(f32(val + inc), SINCOS_LENGTH))
        else:
            h = 0.5 * ts * sw
            pts = [(x - h, y - h), (x - h, y + h), (x + h, y + h), (x + h, y - h)]
        self.raster([pts], (0.0, 0.0, z), g, None, a)

    def points(self, mv, verts) -> None:
        """Points: not pulled toward the eye (PointVert.glsl has no displacement)."""
        st = self.api.st
        if not st.stroke or st.stroke_a <= 0 or st.sw <= 0:
            return
        m = mv
        det = (m[0] * (m[5] * m[10] - m[6] * m[9]) + m[1] * (m[6] * m[8] - m[4] * m[10])
               + m[2] * (m[4] * m[9] - m[5] * m[8]))
        ts = abs(det) ** (1.0 / 3.0)
        T = mmul(self.proj, mv)
        for x, y, z in verts:
            cx = T[0] * x + T[1] * y + T[2] * z + T[3]
            cy = T[4] * x + T[5] * y + T[6] * z + T[7]
            cz = T[8] * x + T[9] * y + T[10] * z + T[11]
            cw = T[12] * x + T[13] * y + T[14] * z + T[15]
            if cz + cw >= 0.0 and cw > NEAR_EPS:
                self.point_px(self.to_screen((cx, cy, cz, cw)), st.stroke_g, st.stroke_a, st.sw, st.cap, ts)

    # ------------------------------------------------------------------ queries
    def screen(self, mv, x, y, z) -> tuple[float, float, float]:
        """screenX/Y/Z: the window position (y down) and the depth (0..1) of a model point."""
        T = mmul(self.proj, mv)
        cx = T[0] * x + T[1] * y + T[2] * z + T[3]
        cy = T[4] * x + T[5] * y + T[6] * z + T[7]
        cz = T[8] * x + T[9] * y + T[10] * z + T[11]
        cw = T[12] * x + T[13] * y + T[14] * z + T[15]
        if cw == 0.0:
            return math.nan, math.nan, math.nan
        return ((cx / cw + 1.0) * (W * 0.5), (1.0 - cy / cw) * (H * 0.5), (cz / cw + 1.0) * 0.5)
