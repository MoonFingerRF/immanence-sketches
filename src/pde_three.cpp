#include "pde_fp.h"  // first: no fused multiply-add (see the header)
// SPDX-License-Identifier: LGPL-2.1-only
// Follows Processing's own source (PGraphicsOpenGL, InGeometry and its shaders, Processing 4.5.2,
// GNU LGPL 2.1) through python/immanence/pde/three.py, to draw 3D exactly as Processing does.
// See THIRD-PARTY.md.
#include "pde_three.h"

#include <cmath>
#include <cstring>

#include "pde_api.h"

namespace pde {

static const char *const ERR_ARITH3 = "arithmetic error (ValueError)";
static const char *const ERR_ZERO3 = "arithmetic error (ZeroDivisionError)";
static constexpr double NEAR_EPS = 1e-9;
static constexpr double DISPLACE = 0.999;
static constexpr int MAX_LIGHTS = 8;
static constexpr size_t MAX_VERTS = 65536;

// ---------------------------------------------------------------------------- matrices
void mmul4(const double *a, const double *b, double *out) {
  double o[16];
  for (int r = 0; r < 4; r++) {
    const double a0 = a[r * 4], a1 = a[r * 4 + 1], a2 = a[r * 4 + 2], a3 = a[r * 4 + 3];
    for (int c = 0; c < 4; c++) o[r * 4 + c] = a0 * b[c] + a1 * b[4 + c] + a2 * b[8 + c] + a3 * b[12 + c];
  }
  std::memcpy(out, o, sizeof o);
}

void look_at(double ex, double ey, double ez, double cx, double cy, double cz, double ux, double uy, double uz,
             double *m) {
  double z0 = ex - cx, z1 = ey - cy, z2 = ez - cz;
  double d = std::sqrt(z0 * z0 + z1 * z1 + z2 * z2);
  if (d != 0.0) {
    z0 = z0 / d;
    z1 = z1 / d;
    z2 = z2 / d;
  }
  double y0 = ux, y1 = uy, y2 = uz;
  double x0 = y1 * z2 - y2 * z1;
  double x1 = -y0 * z2 + y2 * z0;
  double x2 = y0 * z1 - y1 * z0;
  y0 = z1 * x2 - z2 * x1;
  y1 = -z0 * x2 + z2 * x0;
  y2 = z0 * x1 - z1 * x0;
  double xm = std::sqrt(x0 * x0 + x1 * x1 + x2 * x2);
  if (xm != 0.0) {
    x0 = x0 / xm;
    x1 = x1 / xm;
    x2 = x2 / xm;
  }
  double ym = std::sqrt(y0 * y0 + y1 * y1 + y2 * y2);
  if (ym != 0.0) {
    y0 = y0 / ym;
    y1 = y1 / ym;
    y2 = y2 / ym;
  }
  const double t[16] = {x0, x1, x2, 0.0, y0, y1, y2, 0.0, z0, z1, z2, 0.0, 0.0, 0.0, 0.0, 1.0};
  std::memcpy(m, t, sizeof t);
  const double tx = -ex, ty = -ey, tz = -ez;
  for (int r = 0; r < 3; r++) m[r * 4 + 3] = m[r * 4] * tx + m[r * 4 + 1] * ty + m[r * 4 + 2] * tz + m[r * 4 + 3];
}

static inline bool pydiv(double a, double b, double *out, FaultRef fr) {
  if (b == 0.0) {                  // Python float division by (+-)0 raises, NaN numerator too
    fr.raise(Fault::Runtime, ERR_ZERO3);
    return false;
  }
  *out = a / b;
  return true;
}

bool frustum_m(double left, double right, double bottom, double top, double near, double far, double *m,
               FaultRef fr) {
  const double n2 = 2.0 * near, w = right - left, h = top - bottom, d = far - near;
  double m00, m02, m11, m12, m22, m23;
  if (!pydiv(n2, w, &m00, fr) || !pydiv(right + left, w, &m02, fr) || !pydiv(-n2, h, &m11, fr) ||
      !pydiv(top + bottom, h, &m12, fr) || !pydiv(-(far + near), d, &m22, fr) || !pydiv(-(n2 * far), d, &m23, fr))
    return false;
  const double t[16] = {m00, 0.0, m02, 0.0, 0.0, m11, m12, 0.0, 0.0, 0.0, m22, m23, 0.0, 0.0, -1.0, 0.0};
  std::memcpy(m, t, sizeof t);
  return true;
}

bool ortho_m(double left, double right, double bottom, double top, double near, double far, double *m,
             FaultRef fr) {
  const double w = right - left, h = top - bottom, d = far - near;
  double m00, m03, m11, m13, m22, m23;
  if (!pydiv(2.0, w, &m00, fr) || !pydiv(-(right + left), w, &m03, fr) || !pydiv(2.0, h, &m11, fr) ||
      !pydiv(-(top + bottom), h, &m13, fr) || !pydiv(-2.0, d, &m22, fr) || !pydiv(-(far + near), d, &m23, fr))
    return false;
  const double t[16] = {m00, 0.0, 0.0, m03, 0.0, -m11, 0.0, m13, 0.0, 0.0, m22, m23, 0.0, 0.0, 0.0, 1.0};
  std::memcpy(m, t, sizeof t);
  return true;
}

bool perspective_m(double fov, double aspect, double near, double far, double *m, FaultRef fr) {
  const double half = fov / 2.0;
  if (std::isinf(half)) {          // math.tan(inf) raises
    fr.raise(Fault::Runtime, ERR_ARITH3);
    return false;
  }
  const double ymax = near * std::tan(half);
  const double ymin = -ymax;
  return frustum_m(ymin * aspect, ymax * aspect, ymin, ymax, near, far, m, fr);
}

static const double FOV = 60.0 * 3.141592653589793 / 180.0;
const double CAM_Z = (H / 2.0) / std::tan(FOV / 2.0);

void default_camera(double *m) { look_at(W / 2.0, H / 2.0, CAM_Z, W / 2.0, H / 2.0, 0.0, 0.0, 1.0, 0.0, m); }

void default_projection(double *m) {
  Fault f = Fault::None;
  const char *msg = "";
  FaultRef fr{&f, &msg};
  perspective_m(FOV, (double)W / H, CAM_Z / 10.0, CAM_Z * 10.0, m, fr);
}

bool inv3(const double *m, double *out) {
  const double a = m[0], b = m[1], c = m[2], d = m[4], e = m[5], f = m[6], g = m[8], h = m[9], i = m[10];
  const double A = e * i - f * h, B = f * g - d * i, C = d * h - e * g;
  const double det = a * A + b * B + c * C;
  if (det == 0.0 || det != det) return false;
  const double k = 1.0 / det;
  out[0] = A * k;
  out[1] = (c * h - b * i) * k;
  out[2] = (b * f - c * e) * k;
  out[3] = B * k;
  out[4] = (a * i - c * g) * k;
  out[5] = (c * d - a * f) * k;
  out[6] = C * k;
  out[7] = (b * g - a * h) * k;
  out[8] = (a * e - b * d) * k;
  return true;
}

// ---------------------------------------------------------------------------- sphereDetail's tables
static constexpr int SINCOS_LENGTH = 720;
static float SIN_LUT[SINCOS_LENGTH], COS_LUT[SINCOS_LENGTH];
static bool luts_ready = false;

static void luts() {
  if (luts_ready) return;
  const double dtr = f32(f32(3.141592653589793) / 180.0);
  for (int i = 0; i < SINCOS_LENGTH; i++) {
    const double t = f32(f32(i * dtr) * 0.5);
    SIN_LUT[i] = (float)py_sin(t);
    COS_LUT[i] = (float)py_cos(t);
  }
  luts_ready = true;
}

void Three::sphere_tables(int ures, int vres) {
  luts();
  const double delta = f32((double)SINCOS_LENGTH / ures);
  std::vector<double> cx(ures), cz(ures);
  for (int i = 0; i < ures; i++) {
    const int k = (int)f32(i * delta) % SINCOS_LENGTH;
    cx[i] = COS_LUT[k];
    cz[i] = SIN_LUT[k];
  }
  stx.clear();
  sty.clear();
  stz.clear();
  const double step = f32(SINCOS_LENGTH * 0.5 / vres);
  double angle = step;
  for (int i = 1; i < vres; i++) {
    const int k = (int)angle % SINCOS_LENGTH;
    const double rad = SIN_LUT[k], cy = COS_LUT[k];
    for (int j = 0; j < ures; j++) {
      stx.push_back((float)(cx[j] * rad));
      sty.push_back((float)cy);
      stz.push_back((float)(cz[j] * rad));
    }
    angle = f32(angle + step);
  }
  su = ures;
  sv = vres;
}

// ---------------------------------------------------------------------------- state
Three::Three(Api *a) : api(a), depth(W * H, 1.0f) {
  default_camera(cam);
  default_projection(proj);
  eye_dist = CAM_Z;
}

void Three::begin_frame() {
  clear_depth();
  lighting = false;
  nlights = 0;
  fall[0] = 1.0;
  fall[1] = fall[2] = 0.0;
  lspec[0] = lspec[1] = lspec[2] = 0.0;
  normal[0] = normal[1] = 0.0;
  normal[2] = 1.0;
}

void Three::clear_depth() {
  std::fill(depth.begin(), depth.end(), 1.0f);
  dirty = false;
}

void Three::set_camera(const double *m) {
  std::memcpy(cam, m, sizeof cam);
  double d[16];
  default_camera(d);
  cam_default = std::memcmp(d, m, sizeof d) == 0;
  // (Python compares the lists with ==: 0.0 == -0.0 and NaN != NaN; memcmp is stricter only for
  // those, which default_camera() never contains and a NaN camera never equals anyway)
  for (int i = 0; i < 16 && !cam_default; i++) {
    if (!(d[i] == m[i])) break;
    if (i == 15) cam_default = true;
  }
}

void Three::set_projection(const double *m, bool is_ortho) {
  std::memcpy(proj, m, sizeof proj);
  ortho = is_ortho;
  double d[16];
  default_projection(d);
  bool same = true;
  for (int i = 0; i < 16; i++)
    if (!(d[i] == m[i])) same = false;
  proj_default = !is_ortho && same;
}

bool Three::add_light(const double *pos, double w, const double *nrm, const double *amb, const double *dif,
                      const double *spec, const double *fl, const double *spot) {
  if (nlights >= MAX_LIGHTS) {
    api->fault_raise(Fault::Runtime, "can only create 8 lights");
    return false;
  }
  lighting = true;
  Light &L = lights[nlights++];
  for (int k = 0; k < 3; k++) {
    L.pos[k] = pos[k];
    L.nrm[k] = nrm[k];
    L.amb[k] = amb[k];
    L.dif[k] = dif[k];
    L.spec[k] = spec[k];
    L.fall[k] = fl[k];
  }
  L.w = w;
  L.spot[0] = spot[0];
  L.spot[1] = spot[1];
  return true;
}

// ---------------------------------------------------------------------------- shading
void Three::shade(double ex, double ey, double ez, double nx, double ny, double nz, const Mat &mat, double *rgb) {
  double tar = 0.0, tag = 0.0, tab = 0.0, tdr = 0.0, tdg = 0.0, tdb = 0.0, tsr = 0.0, tsg = 0.0, tsb = 0.0;
  for (int li = 0; li < nlights; li++) {
    const Light &L = lights[li];
    double falloff, ldx, ldy, ldz;
    if (L.w < 1.0) {
      falloff = 1.0;
      ldx = -L.nrm[0];
      ldy = -L.nrm[1];
      ldz = -L.nrm[2];
    } else {
      const double lx = L.pos[0] - ex, ly = L.pos[1] - ey, lz = L.pos[2] - ez;
      const double dd = lx * lx + ly * ly + lz * lz;
      const double d = std::sqrt(dd);
      const double den = L.fall[0] + d * L.fall[1] + dd * L.fall[2];
      falloff = den != 0.0 ? 1.0 / den : 0.0;
      if (d > 0.0) {
        ldx = lx / d;
        ldy = ly / d;
        ldz = lz / d;
      } else {
        ldx = ldy = ldz = 0.0;
      }
    }
    double spotf;
    if (L.spot[1] > 0.0) {
      double lx = L.pos[0] - ex, ly = L.pos[1] - ey, lz = L.pos[2] - ez;
      const double d = std::sqrt(lx * lx + ly * ly + lz * lz);
      if (d > 0.0) {
        lx = lx / d;
        ly = ly / d;
        lz = lz / d;
      }
      double sc = -L.nrm[0] * lx - L.nrm[1] * ly - L.nrm[2] * lz;
      if (sc > 1.0) sc = 1.0;
      spotf = sc <= L.spot[0] ? 0.0 : std::pow(sc, L.spot[1]);
    } else {
      spotf = 1.0;
    }
    if (L.amb[0] > 0.0 || L.amb[1] > 0.0 || L.amb[2] > 0.0) {
      tar += L.amb[0] * falloff;
      tag += L.amb[1] * falloff;
      tab += L.amb[2] * falloff;
    }
    if (L.dif[0] > 0.0 || L.dif[1] > 0.0 || L.dif[2] > 0.0) {
      double lam = ldx * nx + ldy * ny + ldz * nz;
      if (lam < 0.0) lam = 0.0;
      const double f = falloff * spotf * lam;
      tdr += L.dif[0] * f;
      tdg += L.dif[1] * f;
      tdb += L.dif[2] * f;
    }
    if (L.spec[0] > 0.0 || L.spec[1] > 0.0 || L.spec[2] > 0.0) {
      const double pd = std::sqrt(ex * ex + ey * ey + ez * ez);
      double px = 0.0, py = 0.0, pz = 0.0;
      if (pd > 0.0) {
        px = ex / pd;
        py = ey / pd;
        pz = ez / pd;
      }
      double hx = ldx - px, hy = ldy - py, hz = ldz - pz;
      const double hd = std::sqrt(hx * hx + hy * hy + hz * hz);
      if (hd > 0.0) {
        hx = hx / hd;
        hy = hy / hd;
        hz = hz / hd;
      }
      double bp = hx * nx + hy * ny + hz * nz;
      if (bp > 1.0) bp = 1.0;
      double pw;
      if (bp > 0.0) {
        pw = std::pow(bp, mat.shine);
        if (std::isinf(pw) && std::isfinite(mat.shine)) {       // math.pow overflow raises
          api->fault_raise(Fault::Runtime, "arithmetic error (OverflowError)");
          pw = 0.0;
        }
      } else {
        pw = mat.shine > 0.0 ? 0.0 : 1.0;
      }
      const double f = falloff * spotf * pw;
      tsr += L.spec[0] * f;
      tsg += L.spec[1] * f;
      tsb += L.spec[2] * f;
    }
  }
  rgb[0] = tar * mat.amb[0] + tdr * mat.fill[0] + tsr * mat.spec[0] + mat.emis[0];
  rgb[1] = tag * mat.amb[1] + tdg * mat.fill[1] + tsg * mat.spec[1] + mat.emis[1];
  rgb[2] = tab * mat.amb[2] + tdb * mat.fill[2] + tsb * mat.spec[2] + mat.emis[2];
}

static inline double clamp01(double c) { return !(c > 0.0) ? 0.0 : c > 1.0 ? 1.0 : c; }

double Three::grey_f(const double *rgb) {
  const double r = clamp01(rgb[0]), g = clamp01(rgb[1]), b = clamp01(rgb[2]);
  return (r * 299.0 + g * 587.0 + b * 114.0) * 0.255;
}

int Three::grey_i(const double *rgb) {
  int o[3];
  for (int k = 0; k < 3; k++) o[k] = (int)(clamp01(rgb[k]) * 255.0 + 0.5);
  return (o[0] * 299 + o[1] * 587 + o[2] * 114) / 1000;
}

// ---------------------------------------------------------------------------- the rasteriser
static inline int pix_of(double v) {        // three._pix
  if (-1e9 < v && v < 1e9) return (int)std::ceil(v - 0.5);
  return v >= 1e9 ? 1000000000 : -1;
}

bool Three::plane(const Poly &p, int k, double *pa, double *pb, double *pc) {
  double nx = 0.0, ny = 0.0, nz = 0.0, sx = 0.0, sy = 0.0, sv = 0.0;
  int cnt = 0;
  uint32_t s = 0;
  for (uint32_t e : p.ends) {
    const int n = (int)(e - s);
    for (int i = 0; i < n; i++) {
      const SP &a = p.pts[s + i];
      const SP &b = p.pts[s + (i + 1) % n];
      const double av = k == 2 ? a.z : a.g, bv = k == 2 ? b.z : b.g;
      nx += (a.y - b.y) * (av + bv);
      ny += (av - bv) * (a.x + b.x);
      nz += (a.x - b.x) * (a.y + b.y);
      sx += a.x;
      sy += a.y;
      sv += av;
      cnt++;
    }
    s = e;
  }
  if (cnt < 3 || !(std::fabs(nz) > 1e-9)) return false;
  const double a = -nx / nz, b = -ny / nz;
  *pa = a;
  *pb = b;
  *pc = sv / cnt - a * (sx / cnt) - b * (sy / cnt);
  return true;
}

void Three::raster(const Poly &p, const double *zp, int g, const double *gp, int a, bool painter) {
  Canvas &cv = api->canvas;
  uint8_t *buf = cv.buf;
  float *D = depth.data();
  for (const SP &q : p.pts)
    if (!(-1e30 < q.x && q.x < 1e30 && -1e30 < q.y && q.y < 1e30)) return;
  edges_.clear();
  double ymin = INFINITY, ymax = -INFINITY;
  uint32_t s = 0;
  for (uint32_t e : p.ends) {
    const int n = (int)(e - s);
    if (n >= 2) {
      double px = p.pts[e - 1].x, py = p.pts[e - 1].y;
      for (int i = 0; i < n; i++) {
        const double qx = p.pts[s + i].x, qy = p.pts[s + i].y;
        if (qy != py) {
          if (py < qy) edges_.push_back({py, qy, px, (qx - px) / (qy - py), 1});
          else edges_.push_back({qy, py, qx, (px - qx) / (py - qy), -1});
          if (qy < ymin) ymin = qy;
          if (qy > ymax) ymax = qy;
          if (py < ymin) ymin = py;
          if (py > ymax) ymax = py;
        }
        px = qx;
        py = qy;
      }
    }
    s = e;
  }
  if (edges_.empty() || !(ymin <= ymax)) return;
  int r0 = pix_of(ymin), r1 = pix_of(ymax);
  if (r0 < 0) r0 = 0;
  if (r1 > H) r1 = H;
  if (r1 <= r0) return;
  const int ne = (int)edges_.size();
  const double za = zp[0], zb = zp[1], zc = zp[2];
  const bool test = depth_test;
  const bool write = test && depth_mask;
  const uint8_t *tbl = (a < 255 && gp == nullptr && !painter) ? cv.table(g, a) : nullptr;
  uint8_t tcopy[256];
  if (tbl) {
    std::memcpy(tcopy, tbl, 256);             // (the canvas's table cache is shared)
    tbl = tcopy;
  }
  const double af = a / 255.0;
  uint64_t pix = 0;
  uint8_t trow[W];
  const Painter *pt = painter ? &api->painter_ref() : nullptr;
  for (int r = r0; r < r1; r++) {
    const double yc = r + 0.5;
    xs_.clear();
    for (const Edge &ed : edges_)
      if (ed.y0 <= yc && ed.y1 > yc) xs_.push_back({ed.x0 + (yc - ed.y0) * ed.dx, ed.d});
    if (xs_.size() < 2) continue;
    xsort_.sort(xs_.data(), (int)xs_.size(), XdLt());
    spa_.clear();
    spb_.clear();
    int wind = 0;
    double start = 0.0;
    for (const Xd &xd : xs_) {
      const int before = wind;
      wind += xd.d;
      if (before == 0 && wind != 0) {
        start = xd.x;
      } else if (before != 0 && wind == 0 && xd.x > start) {
        if (!spb_.empty() && spb_.back() >= start) spb_.back() = xd.x;
        else {
          spa_.push_back(start);
          spb_.push_back(xd.x);
        }
      }
    }
    if (spa_.empty()) continue;
    const int base = r * W;
    const double zr = zb * yc + zc;
    if (pt && !pt->row_ok(r)) {
      api->fault_raise(Fault::Runtime, "arithmetic error (OverflowError)");
      return;
    }
    const double gr = gp ? gp[1] * yc + gp[2] : 0.0;
    const double ga = gp ? gp[0] : 0.0;
    for (size_t k = 0; k < spa_.size(); k++) {
      int ia = pix_of(spa_[k]), ib = pix_of(spb_[k]);
      if (ia < 0) ia = 0;
      if (ib > W) ib = W;
      if (ib <= ia) continue;
      pix += (uint64_t)(ib - ia);
      if (pt) pt->span(r, ia, ib, trow);      // only the pixels this span needs (same bytes)
      for (int x = ia; x < ib; x++) {
        const int j = base + x;
        if (test) {
          const double z = za * (x + 0.5) + zr;
          if (!(z <= (double)D[j])) continue;
          if (write) D[j] = (float)z;
        }
        if (pt) {
          buf[j] = trow[x];
        } else if (gp) {
          const double gf = ga * (x + 0.5) + gr + 0.5;
          const int gv = !(gf >= 1.0) ? 0 : gf >= 255.0 ? 255 : (int)gf;
          if (a >= 255) {
            buf[j] = (uint8_t)gv;
          } else {
            const int v = buf[j];
            buf[j] = (uint8_t)(int)(v + (gv - v) * af + 0.5);
          }
        } else if (tbl) {
          buf[j] = tbl[buf[j]];
        } else {
          buf[j] = (uint8_t)g;
        }
      }
    }
  }
  api->charged += 8 + (uint64_t)ne + (uint64_t)(r1 - r0) + (pix >> 2);
  dirty = true;
}

// ---------------------------------------------------------------------------- meshes
static inline void to_screen(const double *v, double *sx, double *sy, double *sz) {
  const double iw = 1.0 / v[3];
  *sx = (v[0] * iw + 1.0) * (W * 0.5);
  *sy = (1.0 - v[1] * iw) * (H * 0.5);
  *sz = v[2] * iw;
}

void Three::mesh(const double *mv, const Mesh &m, bool lit, bool fill, bool stroke, bool text) {
  const Style &st = api->style();
  const size_t n = m.verts.size();
  if (n > MAX_VERTS) {
    api->fault_raise(Fault::Memory, "a 3D shape has too many vertices");
    return;
  }
  api->charged += 4 + n;
  double T[16];
  mmul4(proj, mv, T);
  const bool do_fill = fill && (st.fill || text) && st.fill_a > 0 && !m.fend.empty();
  const bool do_stroke = stroke && st.stroke && st.stroke_a > 0 && st.sw > 0 && !m.edges.empty();
  if (do_fill) {
    clip_.resize(4 * n);
    scr_.resize(n);
    scr_ok_.assign(n, 0);
    for (size_t i = 0; i < n; i++) {
      const double x = m.verts[i].x, y = m.verts[i].y, z = m.verts[i].z;
      const double cx = T[0] * x + T[1] * y + T[2] * z + T[3];
      const double cy = T[4] * x + T[5] * y + T[6] * z + T[7];
      const double cz = T[8] * x + T[9] * y + T[10] * z + T[11];
      const double cw = T[12] * x + T[13] * y + T[14] * z + T[15];
      double *c = &clip_[4 * i];
      c[0] = cx;
      c[1] = cy;
      c[2] = cz;
      c[3] = cw;
      if (cz + cw >= 0.0 && cw > NEAR_EPS) {
        const double iw = 1.0 / cw;
        scr_[i] = {(cx * iw + 1.0) * (W * 0.5), (1.0 - cy * iw) * (H * 0.5), cz * iw, 0.0};
        scr_ok_[i] = 1;
      }
    }
    fill_faces(mv, m, lit && lighting, true);
    if (!api->ok_pub()) return;
  }
  if (do_stroke) stroke_edges(mv, m);
}

bool Three::colour(const Mesh &m, uint32_t i, bool front, const V3 *fn, Col *out) {
  Col &c = front ? colf_[i] : colb_[i];
  if (c.have) {
    *out = c;
    return true;
  }
  const double *mv = mv_;
  const double x = m.verts[i].x, y = m.verts[i].y, z = m.verts[i].z;
  const double ex = mv[0] * x + mv[1] * y + mv[2] * z + mv[3];
  const double ey = mv[4] * x + mv[5] * y + mv[6] * z + mv[7];
  const double ez = mv[8] * x + mv[9] * y + mv[10] * z + mv[11];
  const V3 nn = fn ? *fn : m.norms[i];
  double nx = 0.0, ny = 0.0, nz = 0.0;
  if (has_ni_) {
    const double *ni = ni_;
    const double qx = nn.x * ni[0] + nn.y * ni[3] + nn.z * ni[6];
    const double qy = nn.x * ni[1] + nn.y * ni[4] + nn.z * ni[7];
    const double qz = nn.x * ni[2] + nn.y * ni[5] + nn.z * ni[8];
    const double d = std::sqrt(qx * qx + qy * qy + qz * qz);
    if (!(d == 0.0)) {
      nx = qx / d;
      ny = qy / d;
      nz = qz / d;
    }
  }
  if (!front) {
    nx = -nx;
    ny = -ny;
    nz = -nz;
  }
  shade(ex, ey, ez, nx, ny, nz, mat_, c.rgb);
  c.g = grey_f(c.rgb);
  c.have = true;
  api->charged += 2 + 2 * (uint64_t)nlights;
  *out = c;
  return api->ok_pub();
}

void Three::fill_faces(const double *mv, const Mesh &m, bool lit, bool textured) {
  const Style &st = api->style();
  const int a = st.fill_a, g_flat = st.fill_g;
  const bool painter_on = textured && st.tex != TEX_NONE;
  const size_t n = m.verts.size();
  if (lit) {
    auto ch = [](int32_t c, double *o) {
      o[0] = ((c >> 16) & 255) / 255.0;
      o[1] = ((c >> 8) & 255) / 255.0;
      o[2] = (c & 255) / 255.0;
    };
    ch(st.fill_c, mat_.fill);
    if (st.amb_set) ch(st.amb_c, mat_.amb);
    else std::memcpy(mat_.amb, mat_.fill, sizeof mat_.fill);
    ch(st.spec_c, mat_.spec);
    ch(st.emis_c, mat_.emis);
    mat_.shine = st.shine;
    mv_ = mv;
    has_ni_ = inv3(mv, ni_);
    colf_.assign(n, Col());
    colb_.assign(n, Col());
  }
  uint32_t cs = 0;                 // first contour of this face
  for (size_t fi = 0; fi < m.fend.size(); fi++) {
    const uint32_t ce = m.fend[fi];
    const V3 *fn = m.face_norms.empty() ? nullptr : &m.face_norms[fi];
    bool need_clip = false;
    for (uint32_t c = cs; c < ce; c++) {
      const uint32_t a0 = c ? m.cend[c - 1] : 0;
      for (uint32_t k = a0; k < m.cend[c]; k++)
        if (!scr_ok_[m.idx[k]]) need_clip = true;
    }
    if (!need_clip) {
      sc_.clear();
      for (uint32_t c = cs; c < ce; c++) {
        const uint32_t a0 = c ? m.cend[c - 1] : 0;
        for (uint32_t k = a0; k < m.cend[c]; k++) sc_.pts.push_back(scr_[m.idx[k]]);
        sc_.ends.push_back((uint32_t)sc_.pts.size());
      }
      double area = 0.0;
      {
        uint32_t s = 0;
        for (uint32_t e : sc_.ends) {
          const int nn = (int)(e - s);
          for (int i = 0; i < nn; i++) {
            const SP &p = sc_.pts[s + i];
            const SP &q = sc_.pts[s + (i + 1) % nn];
            area += (p.x - q.x) * (p.y + q.y);
          }
          s = e;
        }
      }
      if (area == 0.0) {
        cs = ce;
        continue;
      }
      const bool front = area > 0.0;
      if (lit) {
        Col first;
        bool flat = true;
        size_t q = 0;
        for (uint32_t c = cs; c < ce; c++) {
          const uint32_t a0 = c ? m.cend[c - 1] : 0;
          for (uint32_t k = a0; k < m.cend[c]; k++, q++) {
            Col col;
            if (!colour(m, m.idx[k], front, fn, &col)) return;
            if (q == 0) first = col;
            else if (!(col.g == first.g)) flat = false;
            sc_.pts[q].g = col.g;
          }
        }
        if (flat) paint(sc_, grey_i(first.rgb), a, painter_on);
        else if (ce - cs == 1) gouraud(sc_.pts.data(), (int)sc_.pts.size(), a, painter_on);
        else paint(sc_, grey_i(first.rgb), a, painter_on);
      } else {
        paint(sc_, g_flat, a, painter_on);
      }
      if (!api->ok_pub()) return;
      cs = ce;
      continue;
    }
    // some vertex is behind the near plane: clip, carrying both facings' greys
    // (vertex layout: cx cy cz cw, gfront gback, rgb front, rgb back = 12 doubles)
    sc_.clear();
    std::vector<double> &out_all = cpoly2_;
    out_all.clear();
    for (uint32_t c = cs; c < ce; c++) {
      const uint32_t a0 = c ? m.cend[c - 1] : 0;
      cpoly_.clear();
      for (uint32_t k = a0; k < m.cend[c]; k++) {
        const uint32_t i = m.idx[k];
        double v[12] = {clip_[4 * i], clip_[4 * i + 1], clip_[4 * i + 2], clip_[4 * i + 3], 0, 0, 0, 0, 0, 0, 0, 0};
        if (lit) {
          Col fa, fb;
          if (!colour(m, i, true, fn, &fa)) return;
          if (!colour(m, i, false, fn, &fb)) return;
          v[4] = fa.g;
          v[5] = fb.g;
          v[6] = fa.rgb[0];
          v[7] = fa.rgb[1];
          v[8] = fa.rgb[2];
          v[9] = fb.rgb[0];
          v[10] = fb.rgb[1];
          v[11] = fb.rgb[2];
        }
        cpoly_.insert(cpoly_.end(), v, v + 12);
      }
      // Sutherland-Hodgman against z >= -w (attributes interpolated; unlit carries only 4)
      const int nv = (int)(cpoly_.size() / 12);
      const int na = lit ? 12 : 4;
      std::vector<double> clipped;
      for (int i = 0; i < nv; i++) {
        const double *pa = &cpoly_[12 * i];
        const double *pb = &cpoly_[12 * ((i + 1) % nv)];
        const double da = pa[2] + pa[3], db = pb[2] + pb[3];
        if (da >= 0.0) clipped.insert(clipped.end(), pa, pa + 12);
        if ((da >= 0.0) != (db >= 0.0)) {
          const double t = da / (da - db);
          double v[12] = {0};
          for (int q = 0; q < na; q++) v[q] = pa[q] + (pb[q] - pa[q]) * t;
          clipped.insert(clipped.end(), v, v + 12);
        }
      }
      const int nc = (int)(clipped.size() / 12);
      bool okw = nc >= 3;
      for (int i = 0; i < nc && okw; i++)
        if (!(clipped[12 * i + 3] > NEAR_EPS)) okw = false;
      if (!okw) continue;
      for (int i = 0; i < nc; i++) {
        const double *v = &clipped[12 * i];
        double sx, sy, sz;
        to_screen(v, &sx, &sy, &sz);
        sc_.pts.push_back({sx, sy, sz, 0.0});
      }
      sc_.ends.push_back((uint32_t)sc_.pts.size());
      out_all.insert(out_all.end(), clipped.begin(), clipped.end());
    }
    if (sc_.ends.empty()) {
      cs = ce;
      continue;
    }
    double area = 0.0;
    {
      uint32_t s = 0;
      for (uint32_t e : sc_.ends) {
        const int nn = (int)(e - s);
        for (int i = 0; i < nn; i++) {
          const SP &p = sc_.pts[s + i];
          const SP &q = sc_.pts[s + (i + 1) % nn];
          area += (p.x - q.x) * (p.y + q.y);
        }
        s = e;
      }
    }
    if (area == 0.0) {
      cs = ce;
      continue;
    }
    const bool front = area > 0.0;
    if (!lit) {
      paint(sc_, g_flat, a, painter_on);
    } else {
      const int gi = front ? 4 : 5, ci = front ? 6 : 9;
      const size_t np = out_all.size() / 12;
      bool same = true;
      for (size_t i = 1; i < np; i++)
        if (!(out_all[12 * i + gi] == out_all[gi])) same = false;
      if (same) {
        paint(sc_, grey_i(&out_all[ci]), a, painter_on);
      } else if (sc_.ends.size() == 1) {
        for (size_t i = 0; i < np; i++) sc_.pts[i].g = out_all[12 * i + gi];
        gouraud(sc_.pts.data(), (int)np, a, painter_on);
      } else {
        paint(sc_, grey_i(&out_all[ci]), a, painter_on);
      }
    }
    if (!api->ok_pub()) return;
    cs = ce;
  }
}

bool Three::painter_for(const Poly &sc, int g) {
  const Style &st = api->style();
  Contours &dev = api->scratch_dev();
  dev.clear();
  uint32_t s = 0;
  for (uint32_t e : sc.ends) {
    for (uint32_t i = s; i < e; i++) dev.add(sc.pts[i].x, sc.pts[i].y);
    dev.close();
    s = e;
  }
  return api->make_painter_pub(st.tex, st.tex_p, g, st.tex_g2, &dev);
}

void Three::paint(const Poly &sc, int g, int a, bool painter_on) {
  double zp[3];
  if (!plane(sc, 2, &zp[0], &zp[1], &zp[2])) return;
  if (painter_on && !painter_for(sc, g)) return;
  raster(sc, zp, g, nullptr, a, painter_on);
}

void Three::gouraud(const SP *pts, int n, int a, bool painter_on) {
  for (int k = 1; k + 1 < n; k++) {
    tri_.clear();
    tri_.pts.push_back(pts[0]);
    tri_.pts.push_back(pts[k]);
    tri_.pts.push_back(pts[k + 1]);
    tri_.ends.push_back(3);
    double zp[3];
    if (!plane(tri_, 2, &zp[0], &zp[1], &zp[2])) continue;
    if (painter_on) {
      const double gf = (tri_.pts[0].g + tri_.pts[1].g + tri_.pts[2].g) / 3.0 + 0.5;
      const int g = !(gf >= 1.0) ? 0 : gf >= 255.0 ? 255 : (int)gf;
      if (!painter_for(tri_, g)) return;
      raster(tri_, zp, g, nullptr, a, true);
      if (!api->ok_pub()) return;
      continue;
    }
    double gp[3];
    if (!plane(tri_, 3, &gp[0], &gp[1], &gp[2])) continue;
    raster(tri_, zp, 0, gp, a, false);
    if (!api->ok_pub()) return;
  }
}

// ---------------------------------------------------------------------------- strokes and points
void Three::stroke_edges(const double *mv, const Mesh &m) {
  const Style &st = api->style();
  const size_t n = m.verts.size();
  const double *P = proj;
  const double sx = !ortho ? DISPLACE : 1.0, sy = sx, sz = DISPLACE;
  bclip_.resize(4 * n);
  bscr_.resize(n);
  bok_.assign(n, 0);
  used_.assign(n, 0);
  for (uint32_t e : m.edges) used_[e] = 1;
  for (size_t i = 0; i < n; i++) {
    if (!used_[i]) continue;
    const double x = m.verts[i].x, y = m.verts[i].y, z = m.verts[i].z;
    const double ex = (mv[0] * x + mv[1] * y + mv[2] * z + mv[3]) * sx;
    const double ey = (mv[4] * x + mv[5] * y + mv[6] * z + mv[7]) * sy;
    const double ez = (mv[8] * x + mv[9] * y + mv[10] * z + mv[11]) * sz;
    const double ew = mv[12] * x + mv[13] * y + mv[14] * z + mv[15];
    const double cx = P[0] * ex + P[1] * ey + P[2] * ez + P[3] * ew;
    const double cy = P[4] * ex + P[5] * ey + P[6] * ez + P[7] * ew;
    const double cz = P[8] * ex + P[9] * ey + P[10] * ez + P[11] * ew;
    const double cw = P[12] * ex + P[13] * ey + P[14] * ez + P[15] * ew;
    double *c = &bclip_[4 * i];
    c[0] = cx;
    c[1] = cy;
    c[2] = cz;
    c[3] = cw;
    if (cz + cw >= 0.0 && cw > NEAR_EPS) {
      const double iw = 1.0 / cw;
      bscr_[i] = {(cx * iw + 1.0) * (W * 0.5), (1.0 - cy * iw) * (H * 0.5), cz * iw, 0.0};
      bok_[i] = 1;
    }
  }
  const int g = st.stroke_g, a = st.stroke_a;
  const double sw = st.sw;
  for (size_t k = 0; k + 1 < m.edges.size(); k += 2) {
    const uint32_t i = m.edges[k], j = m.edges[k + 1];
    SP p, q;
    if (bok_[i] && bok_[j]) {
      p = bscr_[i];
      q = bscr_[j];
    } else {
      double A[4], B[4];
      std::memcpy(A, &bclip_[4 * i], sizeof A);
      std::memcpy(B, &bclip_[4 * j], sizeof B);
      const double da = A[2] + A[3], db = B[2] + B[3];
      if (da < 0.0 && db < 0.0) continue;
      if (da < 0.0 || db < 0.0) {
        const double t = da / (da - db);
        double C[4];
        for (int q2 = 0; q2 < 4; q2++) C[q2] = A[q2] + (B[q2] - A[q2]) * t;
        if (da < 0.0) std::memcpy(A, C, sizeof A);
        else std::memcpy(B, C, sizeof B);
      }
      if (A[3] <= NEAR_EPS || B[3] <= NEAR_EPS) continue;
      to_screen(A, &p.x, &p.y, &p.z);
      to_screen(B, &q.x, &q.y, &q.z);
    }
    line_px(p, q, g, a, sw);
    if (!api->ok_pub()) return;
  }
}

void Three::line_px(const SP &p, const SP &q, int g, int a, double sw) {
  const double x0 = p.x, y0 = p.y, z0 = p.z, x1 = q.x, y1 = q.y, z1 = q.z;
  const double dx = x1 - x0, dy = y1 - y0;
  const double l2 = dx * dx + dy * dy;
  if (!(l2 > 0.0)) return;
  const double ln = std::sqrt(l2);
  const double hw = sw * 0.5;
  const double nx = -dy / ln * hw, ny = dx / ln * hw;
  quad_.clear();
  quad_.pts.push_back({x0 + nx, y0 + ny, 0.0, 0.0});
  quad_.pts.push_back({x1 + nx, y1 + ny, 0.0, 0.0});
  quad_.pts.push_back({x1 - nx, y1 - ny, 0.0, 0.0});
  quad_.pts.push_back({x0 - nx, y0 - ny, 0.0, 0.0});
  quad_.ends.push_back(4);
  const double dz = z1 - z0;
  const double za = dz * dx / l2, zb = dz * dy / l2;
  const double zp[3] = {za, zb, z0 - za * x0 - zb * y0};
  raster(quad_, zp, g, nullptr, a, false);
}

void Three::point_px(const SP &p, int g, int a, double sw, int cap, double ts) {
  const double x = p.x, y = p.y, z = p.z;
  quad_.clear();
  if (cap == 2) {
    luts();
    const double c = f32(f32(f32(2 * 3.141592653589793) * f32(sw)) / 10.0);
    if (!std::isfinite(c)) {                    // int(inf) raises in Python
      api->fault_raise(Fault::Runtime, "arithmetic error (OverflowError)");
      return;
    }
    const double cc = c < 1e9 ? c : 1e9;
    int npt = (int)cc;
    if (npt < 20) npt = 20;
    if (npt > 200) npt = 200;
    npt += 1;
    const int perim = npt - 1;
    const double inc = f32((double)SINCOS_LENGTH / perim);
    double val = 0.0;
    quad_.pts.push_back({x, y, 0.0, 0.0});
    for (int k = 0; k < perim; k++) {
      const int i = (int)val;
      quad_.pts.push_back({x + 0.5 * COS_LUT[i] * ts * sw, y - 0.5 * SIN_LUT[i] * ts * sw, 0.0, 0.0});
      val = f32(std::fmod(f32(val + inc), (double)SINCOS_LENGTH));
    }
  } else {
    const double h = 0.5 * ts * sw;
    quad_.pts.push_back({x - h, y - h, 0.0, 0.0});
    quad_.pts.push_back({x - h, y + h, 0.0, 0.0});
    quad_.pts.push_back({x + h, y + h, 0.0, 0.0});
    quad_.pts.push_back({x + h, y - h, 0.0, 0.0});
  }
  quad_.ends.push_back((uint32_t)quad_.pts.size());
  const double zp[3] = {0.0, 0.0, z};
  raster(quad_, zp, g, nullptr, a, false);
}

void Three::points(const double *mv, const V3 *v, int n) {
  const Style &st = api->style();
  if (!st.stroke || st.stroke_a <= 0 || st.sw <= 0) return;
  const double *m = mv;
  const double det = (m[0] * (m[5] * m[10] - m[6] * m[9]) + m[1] * (m[6] * m[8] - m[4] * m[10]) +
                      m[2] * (m[4] * m[9] - m[5] * m[8]));
  const double ts = std::pow(std::fabs(det), 1.0 / 3.0);
  double T[16];
  mmul4(proj, mv, T);
  for (int i = 0; i < n; i++) {
    const double x = v[i].x, y = v[i].y, z = v[i].z;
    const double c[4] = {T[0] * x + T[1] * y + T[2] * z + T[3], T[4] * x + T[5] * y + T[6] * z + T[7],
                         T[8] * x + T[9] * y + T[10] * z + T[11], T[12] * x + T[13] * y + T[14] * z + T[15]};
    if (c[2] + c[3] >= 0.0 && c[3] > NEAR_EPS) {
      SP s;
      to_screen(c, &s.x, &s.y, &s.z);
      point_px(s, st.stroke_g, st.stroke_a, st.sw, st.cap, ts);
      if (!api->ok_pub()) return;
    }
  }
}

void Three::screen(const double *mv, double x, double y, double z, double *sx, double *sy, double *sz) {
  double T[16];
  mmul4(proj, mv, T);
  const double cx = T[0] * x + T[1] * y + T[2] * z + T[3];
  const double cy = T[4] * x + T[5] * y + T[6] * z + T[7];
  const double cz = T[8] * x + T[9] * y + T[10] * z + T[11];
  const double cw = T[12] * x + T[13] * y + T[14] * z + T[15];
  if (cw == 0.0) {
    *sx = *sy = *sz = NAN;
    return;
  }
  *sx = (cx / cw + 1.0) * (W * 0.5);
  *sy = (1.0 - cy / cw) * (H * 0.5);
  *sz = (cz / cw + 1.0) * 0.5;
}

}  // namespace pde
