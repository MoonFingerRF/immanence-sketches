#include "pde_fp.h"  // first: no fused multiply-add (see the header)
#include "pde_api.h"
#include "pde_mem.h"

#include <cmath>
#include <cstring>

#include "pde_font.h"

namespace pde {

static const char *const ERR_ARITH = "arithmetic error (ValueError)";
static const char *const ERR_ZERO = "arithmetic error (ZeroDivisionError)";

static inline int32_t jint(int64_t v) { return (int32_t)(uint32_t)(uint64_t)v; }

int luma(int32_t argb) {
  int r = (argb >> 16) & 255, g = (argb >> 8) & 255, b = argb & 255;
  return (r * 299 + g * 587 + b * 114) / 1000;
}

Api::Api() : canvas(204) {
  canvas.aa = false;
  canvas.fr = fr();
}

Api::~Api() { delete t3; }

bool Api::to_int(double v, int64_t *out) {
  if (!std::isfinite(v)) {
    raise(Fault::Runtime, ERR_ARITH);
    *out = 0;
    return false;
  }
  double t = std::trunc(v);
  if (t >= 9.2e18) *out = INT64_MAX;
  else if (t <= -9.2e18) *out = INT64_MIN;
  else *out = (int64_t)t;
  return true;
}

bool Api::to_floor(double v, double *out) {
  if (!std::isfinite(v)) {
    raise(Fault::Runtime, ERR_ARITH);
    *out = 0;
    return false;
  }
  *out = std::floor(v);
  return true;
}

// ================================================================== frame bookkeeping
void Api::size(double w, double h, bool want3d) {
  int64_t iw, ih;
  if (!to_int(w, &iw) || !to_int(h, &ih)) return;
  if (iw != W || ih != H) {
    raise(Fault::Size, "size() is not size(240, 128)");
    return;
  }
  if (want3d && !p3d) {
    if (!can_alloc((size_t) W * H * sizeof(float)) || !can_alloc(sizeof(Three))) {
      raise(Fault::Memory, "not enough memory for P3D (its depth buffer needs 120 KB in one block)");
      return;
    }
    p3d = true;
    t3 = new Three(this);
  }
}

int Api::millis() { return (int)((double)((int64_t)frame_count * 1000) / 60); }

int Api::clock(int what) {
  int64_t t = now_s + tz_s;
  int64_t days = floordiv(t, 86400), secs = t - days * 86400;
  if (what == 0) return (int)(secs % 60);
  if (what == 1) return (int)(secs / 60 % 60);
  if (what == 2) return (int)(secs / 3600);
  // civil_from_days (H. Hinnant)
  int64_t z = days + 719468;
  int64_t era = floordiv(z, 146097);
  int64_t doe = z - era * 146097;
  int64_t yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
  int64_t y = yoe + era * 400;
  int64_t doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
  int64_t mp = (5 * doy + 2) / 153;
  int64_t d = doy - (153 * mp + 2) / 5 + 1;
  int64_t m = mp < 10 ? mp + 3 : mp - 9;
  if (m <= 2) y += 1;
  if (what == 3) return (int)d;
  if (what == 4) return (int)m;
  return (int)y;
}

void Api::reset_shape() {
  shape_open_ = false;
  items_.clear();
  ctrs_.clear();
  ctr_pts_.clear();
  ctr_anc_.clear();
  ctr_items_.clear();
}

// ================================================================== colour
void Api::color_mode(double mode, const double *maxes, int n) {
  int64_t md;
  if (!to_int(mode, &md)) return;
  if (md != 1 && md != 3) md = 1;
  double m[4] = {st_.cmax[0], st_.cmax[1], st_.cmax[2], st_.cmax[3]};
  if (n == 1) {
    for (double &v : m) v = f32(maxes[0]);
  } else if (n == 3) {
    for (int i = 0; i < 3; i++) m[i] = f32(maxes[i]);
  } else if (n >= 4) {
    for (int i = 0; i < 4; i++) m[i] = f32(maxes[i]);
  }
  st_.cmode = (int)md;
  for (int i = 0; i < 4; i++) st_.cmax[i] = m[i];
}

int32_t Api::grey(double g, double a) {
  const double mx = st_.cmax[0], ma = st_.cmax[3];
  g = pymin(pymax(g, 0.0), mx);
  a = pymin(pymax(a, 0.0), ma);
  int64_t gi = 0, ai = 0;
  if (mx != 0 && !to_int(f32(f32(g / mx) * 255), &gi)) return 0;
  if (ma != 0 && !to_int(f32(f32(a / ma) * 255), &ai)) return 0;
  return jint((ai << 24) | (gi << 16) | (gi << 8) | gi);
}

int32_t Api::three(double x, double y, double z, double a) {
  const double mx = st_.cmax[0], my = st_.cmax[1], mz = st_.cmax[2], ma = st_.cmax[3];
  x = pymin(pymax(x, 0.0), mx);
  y = pymin(pymax(y, 0.0), my);
  z = pymin(pymax(z, 0.0), mz);
  a = pymin(pymax(a, 0.0), ma);
  if (mx == 0 || my == 0 || mz == 0) {
    raise(Fault::Runtime, ERR_ZERO);
    return 0;
  }
  double r, g, b;
  if (st_.cmode == 3) {
    double hue = f32(x / mx), sat = f32(y / my), bri = f32(z / mz);
    if (sat == 0) {
      r = g = b = bri;
    } else {
      int64_t ih, iw;
      if (!to_int(hue, &ih)) return 0;
      double which = f32((hue - (double)ih) * 6.0);
      if (!to_int(which, &iw)) return 0;
      double f = f32(which - (double)iw);
      double p = f32(bri * f32(1.0 - sat));
      double q = f32(bri * f32(1.0 - f32(sat * f)));
      double t = f32(bri * f32(1.0 - f32(sat * f32(1.0 - f))));
      switch (floormod(iw, 6)) {
        case 0: r = bri, g = t, b = p; break;
        case 1: r = q, g = bri, b = p; break;
        case 2: r = p, g = bri, b = t; break;
        case 3: r = p, g = q, b = bri; break;
        case 4: r = t, g = p, b = bri; break;
        default: r = bri, g = p, b = q; break;
      }
    }
  } else {
    r = f32(x / mx);
    g = f32(y / my);
    b = f32(z / mz);
  }
  int64_t ri, gi, bi, ai;
  if (!to_int(f32(r * 255), &ri) || !to_int(f32(g * 255), &gi) || !to_int(f32(b * 255), &bi)) return 0;
  if (ma == 0) {
    raise(Fault::Runtime, ERR_ZERO);
    return 0;
  }
  if (!to_int(f32(f32(a / ma) * 255), &ai)) return 0;
  return jint((ai << 24) | (ri << 16) | (gi << 8) | bi);
}

int32_t Api::calc(const double *args, int n, const bool *ints) {
  const double mx = st_.cmax[0], ma = st_.cmax[3];
  if ((n == 1 || n == 2) && ints[0]) {
    int64_t rgb;
    if (!to_int(args[0], &rgb)) return 0;
    if ((rgb & 0xFF000000LL) == 0 && (double)rgb <= mx) return grey((double)rgb, n == 2 ? args[1] : ma);
    if (n == 1 || args[1] == ma) return jint(rgb);
    if (ma == 0) {
      raise(Fault::Runtime, ERR_ZERO);
      return 0;
    }
    int64_t a;
    if (!to_int((double)((rgb >> 24) & 255) * pymax(0.0, pymin(1.0, f32(args[1] / ma))), &a)) return 0;
    return jint((a << 24) | (rgb & 0xFFFFFF));
  }
  if (n == 1 || n == 2) return grey(args[0], n == 2 ? args[1] : ma);
  if (n == 3 || n == 4) return three(args[0], args[1], args[2], n == 4 ? args[3] : ma);
  raise(Fault::Runtime, "a colour takes 1 to 4 numbers");
  return 0;
}

int32_t Api::color(const double *a, int n, const bool *ints) { return calc(a, n, ints); }

void Api::fill(const double *a, int n, const bool *ints) {
  int32_t c = calc(a, n, ints);
  if (!ok()) return;
  st_.fill = true;
  st_.fill_c = c;
  st_.fill_g = luma(c);
  st_.fill_a = (c >> 24) & 255;
}

void Api::stroke(const double *a, int n, const bool *ints) {
  int32_t c = calc(a, n, ints);
  if (!ok()) return;
  st_.stroke = true;
  st_.stroke_c = c;
  st_.stroke_g = luma(c);
  st_.stroke_a = (c >> 24) & 255;
}

void Api::background(const double *a, int n, const bool *ints) {
  int32_t c = calc(a, n, ints);
  if (!ok()) return;
  int al = (c >> 24) & 255, g = luma(c);
  if (al < 255) g = (g * al + 255 * (255 - al)) / 255;
  canvas.clear(g);
  cost_();
  if (t3) t3->clear_depth();
}

void Api::stroke_cap(double c) {
  int64_t v;
  if (!to_int(c, &v)) return;
  st_.cap = (v == 1 || v == 2 || v == 4) ? (int)v : 2;
}

void Api::stroke_join(double j) {
  int64_t v;
  if (!to_int(j, &v)) return;
  st_.join = (v == 2 || v == 8 || v == 32) ? (int)v : 8;
}

void Api::rect_mode(double m) {
  int64_t v;
  if (to_int(m, &v)) st_.rect_mode = v;
}

void Api::ellipse_mode(double m) {
  int64_t v;
  if (to_int(m, &v)) st_.ellipse_mode = v;
}

// int(c) of a colour, as its low 64 bits (Python's big int shifted and masked the same way)
static bool colour_bits(Api *api, double c, int64_t *out, FaultRef fr) {
  (void)api;
  if (!std::isfinite(c)) {
    fr.raise(Fault::Runtime, ERR_ARITH);
    *out = 0;
    return false;
  }
  *out = (int64_t)pyint_bits(c);
  return true;
}

double Api::red(double c) {
  int64_t v;
  return colour_bits(this, c, &v, fr()) ? (double)((v >> 16) & 255) : 0.0;
}
double Api::green(double c) {
  int64_t v;
  return colour_bits(this, c, &v, fr()) ? (double)((v >> 8) & 255) : 0.0;
}
double Api::blue(double c) {
  int64_t v;
  return colour_bits(this, c, &v, fr()) ? (double)(v & 255) : 0.0;
}
double Api::alpha(double c) {
  int64_t v;
  return colour_bits(this, c, &v, fr()) ? (double)((v >> 24) & 255) : 0.0;
}

double Api::brightness(double c) {
  int64_t v;
  if (!colour_bits(this, c, &v, fr())) return 0.0;
  int64_t r = (v >> 16) & 255, g = (v >> 8) & 255, b = v & 255;
  int64_t mx = imax(imax(r, g), b);
  return f32((double)mx / 255.0 * st_.cmax[2]);
}

double Api::saturation(double c) {
  int64_t v;
  if (!colour_bits(this, c, &v, fr())) return 0.0;
  int64_t r = (v >> 16) & 255, g = (v >> 8) & 255, b = v & 255;
  int64_t mx = imax(imax(r, g), b), mn = imin(imin(r, g), b);
  double s = mx ? (double)(mx - mn) / (double)mx : 0.0;
  return f32(s * st_.cmax[1]);
}

double Api::hue(double c) {
  int64_t v;
  if (!colour_bits(this, c, &v, fr())) return 0.0;
  int64_t r = (v >> 16) & 255, g = (v >> 8) & 255, b = v & 255;
  int64_t mx = imax(imax(r, g), b), mn = imin(imin(r, g), b);
  if (mx == mn) return 0.0;
  double d = (double)(mx - mn);
  double rc = (double)(mx - r) / d, gc = (double)(mx - g) / d, bc = (double)(mx - b) / d;
  double hh = r == mx ? bc - gc : g == mx ? 2.0 + rc - bc : 4.0 + gc - rc;
  hh = pyfmod(hh / 6.0, 1.0);
  return f32(hh * st_.cmax[0]);
}

int32_t Api::lerp_color(double c1d, double c2d, double t) {
  t = pymin(pymax(t, 0.0), 1.0);
  int64_t c1, c2;
  if (!colour_bits(this, c1d, &c1, fr()) || !colour_bits(this, c2d, &c2, fr())) return 0;
  int64_t out = 0;
  for (int sh = 24; sh >= 0; sh -= 8) {
    int64_t a = (c1 >> sh) & 255, b = (c2 >> sh) & 255, v;
    if (!to_int(a + (b - a) * t + 0.5, &v)) return 0;
    out |= (v & 255) << sh;
  }
  return jint(out);
}

// ================================================================== matrix
void Api::push_matrix() {
  if (nm_ >= 32) {
    raise(Fault::Runtime, "too many calls to pushMatrix()");
    return;
  }
  mzstack_[nm_] = mz_;
  m3has_stack_[nm_] = has_m3_;
  if (has_m3_) std::memcpy(m3stack_[nm_], m3_, sizeof m3_);
  std::memcpy(mstack_[nm_++], m_, sizeof m_);
}

void Api::pop_matrix() {
  if (!nm_) {
    raise(Fault::Runtime, "too many calls to popMatrix(), and not enough to pushMatrix()");
    return;
  }
  std::memcpy(m_, mstack_[--nm_], sizeof m_);
  mz_ = mzstack_[nm_];
  has_m3_ = m3has_stack_[nm_];
  if (has_m3_) std::memcpy(m3_, m3stack_[nm_], sizeof m3_);
}

void Api::push_style() {
  if (ns_ >= 64) {
    raise(Fault::Runtime, "too many calls to pushStyle()");
    return;
  }
  sstack_[ns_++] = st_;
}

void Api::pop_style() {
  if (!ns_) {
    raise(Fault::Runtime, "too many calls to popStyle(), and not enough to pushStyle()");
    return;
  }
  st_ = sstack_[--ns_];
  canvas.blend = st_.blend;
}

void Api::reset_matrix() {
  const double id[6] = {1.0, 0.0, 0.0, 1.0, 0.0, 0.0};
  std::memcpy(m_, id, sizeof m_);
  mz_ = 1.0;
  has_m3_ = false;
}

void Api::translate(double x, double y, double z) {
  if (has_m3_ || (p3d && z != 0.0)) {
    const double t[16] = {1.0, 0.0, 0.0, x, 0.0, 1.0, 0.0, y, 0.0, 0.0, 1.0, z, 0.0, 0.0, 0.0, 1.0};
    apply3(t);
    return;
  }
  double a = m_[0], b = m_[1], c = m_[2], d = m_[3], e = m_[4], f = m_[5];
  m_[4] = e + a * x + c * y;
  m_[5] = f + b * x + d * y;
}

void Api::rotate(double t) {
  if (std::isinf(t)) {            // math.cos(inf) raises ValueError (NaN passes through)
    raise(Fault::Runtime, ERR_ARITH);
    return;
  }
  if (has_m3_) {
    const double co = py_cos(t), si = py_sin(t);
    const double r[16] = {co, -si, 0.0, 0.0, si, co, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0};
    apply3(r);
    return;
  }
  double a = m_[0], b = m_[1], c = m_[2], d = m_[3];
  double co = py_cos(t), si = py_sin(t);
  m_[0] = a * co + c * si;
  m_[1] = b * co + d * si;
  m_[2] = c * co - a * si;
  m_[3] = d * co - b * si;
}

void Api::scale(double sx, bool has_sy, double sy, bool has_sz, double sz) {
  if (p3d) {
    const double z = !has_sy ? sx : (has_sz ? sz : 1.0);
    if (has_m3_) {
      const double y = has_sy ? sy : sx;
      const double t[16] = {sx, 0.0, 0.0, 0.0, 0.0, y, 0.0, 0.0, 0.0, 0.0, z, 0.0, 0.0, 0.0, 0.0, 1.0};
      apply3(t);
      return;
    }
    mz_ *= z;
  }
  if (!has_sy) sy = sx;
  m_[0] *= sx;
  m_[1] *= sx;
  m_[2] *= sy;
  m_[3] *= sy;
}

void Api::shear_x(double t) {
  if (std::isinf(t)) {            // math.tan(inf) raises ValueError
    raise(Fault::Runtime, ERR_ARITH);
    return;
  }
  double k = std::tan(t);
  if (has_m3_) {
    const double s[16] = {1.0, k, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0};
    apply3(s);
    return;
  }
  m_[2] += m_[0] * k;
  m_[3] += m_[1] * k;
}

void Api::shear_y(double t) {
  if (std::isinf(t)) {            // math.tan(inf) raises ValueError
    raise(Fault::Runtime, ERR_ARITH);
    return;
  }
  double k = std::tan(t);
  if (has_m3_) {
    const double s[16] = {1.0, 0.0, 0.0, 0.0, k, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0};
    apply3(s);
    return;
  }
  m_[0] += m_[2] * k;
  m_[1] += m_[3] * k;
}

double Api::screen_x(double x, double y, bool has_z, double z) {
  if (p3d && (!flat3() || (has_z && z != 0.0))) {
    double m[16], sx, sy, sz;
    mv(m);
    t3->screen(m, x, y, has_z ? z : 0.0, &sx, &sy, &sz);
    return f32(sx);
  }
  return f32(m_[0] * x + m_[2] * y + m_[4]);
}
double Api::screen_y(double x, double y, bool has_z, double z) {
  if (p3d && (!flat3() || (has_z && z != 0.0))) {
    double m[16], sx, sy, sz;
    mv(m);
    t3->screen(m, x, y, has_z ? z : 0.0, &sx, &sy, &sz);
    return f32(sy);
  }
  return f32(m_[1] * x + m_[3] * y + m_[5]);
}

void Api::xf(const Contours &in, Contours &out) {
  const double a = m_[0], b = m_[1], c = m_[2], d = m_[3], e = m_[4], f = m_[5];
  out.clear();
  out.pts.reserve(in.pts.size());
  for (const Pt &p : in.pts) out.add(a * p.x + c * p.y + e, b * p.x + d * p.y + f);
  out.ends = in.ends;
}

bool Api::inverse(double inv[6]) {
  const double a = m_[0], b = m_[1], c = m_[2], d = m_[3];
  double det = a * d - b * c;
  if (std::fabs(det) < 1e-12) return false;
  inv[0] = d / det;
  inv[1] = -b / det;
  inv[2] = -c / det;
  inv[3] = a / det;
  inv[4] = m_[4];
  inv[5] = m_[5];
  return true;
}

// Api._normalize: Java2D's stroke normalisation; returns the new points (norm_) or `pts`
const Pt *Api::normalize(const Pt *pts, int n, const uint8_t *anchors) {
  double inv[6];
  if (!inverse(inv) || n == 0) return pts;
  const double a = m_[0], b = m_[1], c = m_[2], d = m_[3], e = m_[4], f = m_[5];
  const bool aa = canvas.aa;
  auto snap = [&](double v, double *out) {
    if (aa) {
      if (!to_floor(v, out)) return false;
      *out += 0.5;
    } else {
      if (!to_floor(v + 0.25, out)) return false;
      *out += 0.25;
    }
    return true;
  };
  norm_.resize((size_t)n);
  off_.resize((size_t)n);
  off_known_.assign((size_t)n, 0);
  for (int i = 0; i < n; i++) norm_[i] = {a * pts[i].x + c * pts[i].y + e, b * pts[i].x + d * pts[i].y + f};
  int nknown = 0;
  for (int i = 0; i < n; i++) {
    if (!anchors || anchors[i]) {
      double sx, sy;
      if (!snap(norm_[i].x, &sx)) return pts;
      double ox = sx - norm_[i].x;
      if (!snap(norm_[i].y, &sy)) return pts;
      off_[i] = {ox, sy - norm_[i].y};
      off_known_[i] = 1;
      nknown++;
    }
  }
  if (anchors) {
    if (!nknown) {
      double sx, sy;
      if (!snap(norm_[0].x, &sx)) return pts;
      double ox = sx - norm_[0].x;
      if (!snap(norm_[0].y, &sy)) return pts;
      Pt o = {ox, sy - norm_[0].y};
      for (int i = 0; i < n; i++) off_[i] = o;
    } else {
      // known = the anchor indices; each other point interpolates the anchors around it
      pts2_.clear();
      std::vector<Pt> &known = pts2_;   // x = index (exact small ints)
      for (int i = 0; i < n; i++)
        if (off_known_[i]) known.push_back({(double)i, 0});
      size_t k = 0;
      for (int i = 0; i < n; i++) {
        if (off_known_[i]) continue;
        while (k + 1 < known.size() && (int)known[k + 1].x < i) k++;
        int lo = (int)known[k].x < i ? (int)known[k].x : -1;
        int hi = -1;
        for (size_t j = k; j < known.size(); j++)
          if ((int)known[j].x > i) {
            hi = (int)known[j].x;
            break;
          }
        if (lo < 0) {
          off_[i] = off_[hi];
        } else if (hi < 0) {
          off_[i] = off_[lo];
        } else {
          double t = (double)(i - lo) / (double)(hi - lo);
          off_[i] = {off_[lo].x + (off_[hi].x - off_[lo].x) * t, off_[lo].y + (off_[hi].y - off_[lo].y) * t};
        }
      }
    }
  }
  const double ia = inv[0], ib = inv[1], ic = inv[2], id = inv[3];
  for (int i = 0; i < n; i++) {
    double x = norm_[i].x + off_[i].x, y = norm_[i].y + off_[i].y;
    norm_[i] = {ia * (x - e) + ic * (y - f), ib * (x - e) + id * (y - f)};
  }
  return norm_.data();
}

double Api::mscale() {
  if (has_m3_) {
    const double v = std::sqrt(std::fabs(m3_[0] * m3_[5] - m3_[1] * m3_[4]));
    return v != 0.0 ? v : 1e-9;
  }
  double s = std::sqrt(std::fabs(m_[0] * m_[3] - m_[1] * m_[2]));
  return s != 0.0 ? s : 1e-9;
}

// ================================================================== painting helpers
bool Api::make_painter(int tex, int p, int g, int g2, const Contours *dev) {
  Painter &pt = painter_;
  pt.has_box = false;
  if (tex_is_gradient(tex) && dev) {
    if (dev->pts.empty()) {
      raise(Fault::Runtime, "arithmetic error (ValueError)");   // min() of an empty list
      return false;
    }
    double mnx = dev->pts[0].x, mny = dev->pts[0].y, mxx = mnx, mxy = mny;
    for (size_t i = 1; i < dev->pts.size(); i++) {
      double x = dev->pts[i].x, y = dev->pts[i].y;
      if (x < mnx) mnx = x;
      if (x > mxx) mxx = x;
      if (y < mny) mny = y;
      if (y > mxy) mxy = y;
    }
    double bx0, by0, bx1, by1;
    if (!to_floor(mnx, &bx0) || !to_floor(mny, &by0)) return false;
    if (!std::isfinite(mxx) || !std::isfinite(mxy)) {
      raise(Fault::Runtime, ERR_ARITH);
      return false;
    }
    bx1 = std::ceil(mxx);
    by1 = std::ceil(mxy);
    pt.has_box = true;
    pt.bx0 = bx0;
    pt.by0 = by0;
    pt.bx1 = bx1;
    pt.by1 = by1;
  }
  int64_t seed = has_tex_seed ? tex_seed : last_rseed;
  double ox, oy;
  const double tx = has_m3_ ? m3_[3] : m_[4], ty = has_m3_ ? m3_[7] : m_[5];
  if (!to_floor(tx + 0.5, &ox) || !to_floor(ty + 0.5, &oy)) return false;
  bool box = pt.has_box;
  double b[4] = {pt.bx0, pt.by0, pt.bx1, pt.by1};
  pt.init(tex, p, level_of(g), level_of(g2), seed, ox, oy);
  pt.has_box = box;
  pt.bx0 = b[0];
  pt.by0 = b[1];
  pt.bx1 = b[2];
  pt.by1 = b[3];
  return true;
}

void Api::crisp(Contours &dev, bool rect) {
  if (canvas.aa) return;
  if (rect) {
    for (Pt &p : dev.pts) {
      double fx, fy;
      if (!to_floor(p.x + 0.25, &fx) || !to_floor(p.y + 0.25, &fy)) return;
      p = {fx + 0.25, fy + 0.25};
    }
    return;
  }
  for (Pt &p : dev.pts) p = {p.x + 0.5, p.y + 0.5};
}

void Api::fill_contours(const Contours &c, bool rect) {
  const Style &s = st_;
  if (!(s.fill && s.fill_a > 0)) return;
  if (use3d()) {
    fill3(c, true);
    return;
  }
  xf(c, dev_);
  has_painter_ = false;
  if (s.tex != TEX_NONE) {
    if (!make_painter(s.tex, s.tex_p, s.fill_g, s.tex_g2, &dev_)) return;
    has_painter_ = true;
  }
  crisp(dev_, rect);
  if (!ok()) return;
  canvas.fill(dev_, s.fill_g, s.fill_a, has_painter_ ? &painter_ : nullptr);
  cost_();
}

void Api::stroke_contours(const Contours &c) {
  const Style &s = st_;
  if (c.empty() || s.stroke_a <= 0) return;
  xf(c, dev_);
  has_painter_ = false;
  if (s.stex != TEX_NONE) {
    if (!make_painter(s.stex, s.stex_p, s.stroke_g, s.stex_g2, &dev_)) return;
    has_painter_ = true;
  }
  canvas.fill(dev_, s.stroke_g, s.stroke_a, has_painter_ ? &painter_ : nullptr);
  cost_();
}

void Api::one(const Pt *pts, int n, Contours &out) {
  out.clear();
  out.pts.assign(pts, pts + n);
  out.close();
}

void Api::thin_path(const Pt *pts, int n, bool closed) {
  const Style &s = st_;
  if (s.stroke_a <= 0 || n == 0) return;
  one(pts, n, poly_);
  xf(poly_, dev_);
  has_painter_ = false;
  if (s.stex != TEX_NONE) {
    if (!make_painter(s.stex, s.stex_p, s.stroke_g, s.stex_g2, &dev_)) return;
    has_painter_ = true;
  }
  const Painter *pp = has_painter_ ? &painter_ : nullptr;
  const std::vector<Pt> &d = dev_.pts;
  if (n == 1) {
    canvas.thin_line(d[0].x, d[0].y, d[0].x, d[0].y, s.stroke_g, s.stroke_a, pp);
  } else {
    for (int i = 0; i + 1 < n && ok(); i++)
      canvas.thin_line(d[i].x, d[i].y, d[i + 1].x, d[i + 1].y, s.stroke_g, s.stroke_a, pp);
    if (closed && n > 2 && ok())
      canvas.thin_line(d[n - 1].x, d[n - 1].y, d[0].x, d[0].y, s.stroke_g, s.stroke_a, pp);
  }
  if (!ok()) return;
  cost_();
}

void Api::stroke_path(const Pt *pts, int n, bool closed, const uint8_t *anchors) {
  const Style &s = st_;
  if (!s.stroke || s.sw <= 0 || n == 0) return;
  if (use3d()) {
    v3_.clear();
    for (int i = 0; i < n; i++) v3_.push_back({pts[i].x, pts[i].y, 0.0});
    stroke3(v3_.data(), n, closed);
    return;
  }
  if (thin()) {
    thin_path(pts, n, closed);
    return;
  }
  const Pt *np = normalize(pts, n, anchors);
  if (!ok()) return;
  stroker_.polyline(np, n, s.sw, closed, s.cap, s.join, mscale(), stroke_, fr());
  if (!ok()) return;
  stroke_contours(stroke_);
}

// ================================================================== textures (an extension to Processing)
static bool py_space(uint16_t c) {
  return (c >= 9 && c <= 13) || (c >= 28 && c <= 32) || c == 0x85 || c == 0xA0 || c == 0x1680 ||
         (c >= 0x2000 && c <= 0x200A) || c == 0x2028 || c == 0x2029 || c == 0x202F || c == 0x205F || c == 0x3000;
}

int Api::tex_name(const uint16_t *name, int n) {
  int a = 0, b = n;
  while (a < b && py_space(name[a])) a++;
  while (b > a && py_space(name[b - 1])) b--;
  char low[16];
  int len = b - a;
  bool fits = len > 0 && len < (int)sizeof low;
  for (int i = 0; fits && i < len; i++) {
    uint16_t c = name[a + i];
    if (c == 0x212A) c = 'k';             // KELVIN SIGN lowercases to ASCII 'k'
    if (c >= 'A' && c <= 'Z') c = (uint16_t)(c + 32);
    if (c >= 128) fits = false;
    low[i] = (char)c;
  }
  if (fits) {
    low[len] = 0;
    for (int t = 0; t < TEX_COUNT; t++)
      if (std::strcmp(low, TEX_NAMES[t]) == 0) return t;
  }
  raise(Fault::Runtime, "there is no texture called that");
  return TEX_NONE;
}

int Api::clamp_p(int tex, bool has_p, double p) {
  if (!has_p) return tex_default_p(tex);
  int64_t v;
  if (!to_int(p, &v)) return 6;
  int64_t lo = tex == TEX_STIPPLE ? 1 : 2;
  return (int)imax(lo, imin(64, v));
}

void Api::set_texture(bool stroke, const uint16_t *name, int n, bool has_p, double p) {
  int t = tex_name(name, n);
  if (!ok()) return;
  int &tex = stroke ? st_.stex : st_.tex;
  int &tp = stroke ? st_.stex_p : st_.tex_p;
  int &g2 = stroke ? st_.stex_g2 : st_.tex_g2;
  tex = t;
  if (tex_is_gradient(t)) {
    if (has_p) {
      int32_t c = grey(p, st_.cmax[3]);
      if (!ok()) return;
      g2 = luma(c);
    } else {
      g2 = 255;
    }
  } else {
    int v = clamp_p(t, has_p, p);
    if (ok()) tp = v;
  }
}

void Api::texture(const uint16_t *name, int n, bool has_p, double p) { set_texture(false, name, n, has_p, p); }

void Api::stroke_texture(const uint16_t *name, int n, bool has_p, double p) {
  set_texture(true, name, n, has_p, p);
}

void Api::texture_seed(double n) {
  if (!std::isfinite(n)) {
    raise(Fault::Runtime, ERR_ARITH);
    return;
  }
  tex_seed = (int)(uint32_t)pyint_bits(n);
  has_tex_seed = true;
}

void Api::background_texture(const double *nums, int n, const bool *ints, const uint16_t *name, int nn,
                             bool has_extra, double extra) {
  int t = tex_name(name, nn);
  if (!ok()) return;
  if (tex_is_gradient(t)) {
    int g1 = luma(grey(nums[0], st_.cmax[3]));
    if (!ok()) return;
    int g2 = 255;
    if (n > 1) g2 = luma(grey(nums[1], st_.cmax[3]));
    else if (has_extra) g2 = luma(grey(extra, st_.cmax[3]));
    if (!ok()) return;
    poly_.clear();
    poly_.add(0, 0);
    poly_.add(W, H);
    poly_.close();
    if (!make_painter(t, 6, g1, g2, &poly_)) return;
  } else {
    int32_t c = calc(nums, n, ints);
    if (!ok()) return;
    int p = clamp_p(t, has_extra, extra);
    if (!ok()) return;
    if (!make_painter(t, p, luma(c), 255, nullptr)) return;
  }
  for (int y = 0; y < H; y++) {
    if (!painter_.row_ok(y)) {
      raise(Fault::Runtime, "arithmetic error (OverflowError)");
      return;
    }
    painter_.span(y, 0, W, canvas.buf + y * W);
  }
  charged += W * H / 8;
  if (t3) t3->clear_depth();
}

// ================================================================== shapes
void Api::point3(double x, double y, double z) {
  const Style &s = st_;
  if (!s.stroke || s.sw <= 0) return;
  double m[16];
  mv(m);
  const V3 v = {x, y, z};
  t3->points(m, &v, 1);
}

void Api::point_z(double x, double y, bool has_z, double z) {
  const Style &s = st_;
  if (!s.stroke || s.sw <= 0) return;
  if (p3d && (use3d() || (has_z && z != 0.0))) {
    point3(x, y, has_z ? z : 0.0);
    return;
  }
  point(x, y);
}

void Api::point(double x, double y) {
  const Style &s = st_;
  if (!s.stroke || s.sw <= 0) return;
  if (use3d()) {
    point3(x, y, 0.0);
    return;
  }
  double hw = s.sw / 2;
  Pt p = {x, y};
  if (thin()) {
    thin_path(&p, 1, false);
    return;
  }
  p = normalize(&p, 1, nullptr)[0];
  if (!ok()) return;
  if (s.cap == ROUND) {
    int n = segments_for(hw * mscale(), kTWO_PI, fr());
    if (!ok()) return;
    ring_.clear();
    ellipse_points(p.x, p.y, hw, hw, n, ring_);
    ring_.close();
    stroke_contours(ring_);
  } else if (s.cap == PROJECT) {
    ring_.clear();
    ring_.add(p.x - hw, p.y - hw);
    ring_.add(p.x + hw, p.y - hw);
    ring_.add(p.x + hw, p.y + hw);
    ring_.add(p.x - hw, p.y + hw);
    ring_.close();
    stroke_contours(ring_);
  }
}

void Api::line(double x1, double y1, double x2, double y2) {
  Pt p[2] = {{x1, y1}, {x2, y2}};
  stroke_path(p, 2, false);
}

void Api::line3(double x1, double y1, double z1, double x2, double y2, double z2) {
  if (p3d && (use3d() || z1 != 0.0 || z2 != 0.0)) {
    const Style &s = st_;
    if (s.stroke && s.sw > 0) {
      const V3 v[2] = {{x1, y1, z1}, {x2, y2, z2}};
      stroke3(v, 2, false);
    }
    return;
  }
  line(x1, y1, x2, y2);
}

void Api::rect_box(double a, double b, double c, double d, int64_t mode, double *x0, double *y0, double *x1,
                   double *y1) {
  if (mode == 1) {
    *x0 = a, *y0 = b, *x1 = c, *y1 = d;
  } else if (mode == 3) {
    *x0 = a - c / 2, *y0 = b - d / 2, *x1 = a + c / 2, *y1 = b + d / 2;
  } else if (mode == 2) {
    *x0 = a - c, *y0 = b - d, *x1 = a + c, *y1 = b + d;
  } else {
    *x0 = a, *y0 = b, *x1 = a + c, *y1 = b + d;
  }
  if (*x1 < *x0) {
    double t = *x0;
    *x0 = *x1;
    *x1 = t;
  }
  if (*y1 < *y0) {
    double t = *y0;
    *y0 = *y1;
    *y1 = t;
  }
}

void Api::rect(double a, double b, double c, double d, const double *radii, int nr) {
  double x0, y0, x1, y1;
  rect_box(a, b, c, d, st_.rect_mode, &x0, &y0, &x1, &y1);
  bool any = false;
  for (int i = 0; i < nr; i++) any |= radii[i] > 0;
  if (nr && any) {
    round_rect(x0, y0, x1, y1, radii, nr);
    return;
  }
  const Style &s = st_;
  if (use3d()) {
    poly_.clear();
    poly_.add(x0, y0);
    poly_.add(x1, y0);
    poly_.add(x1, y1);
    poly_.add(x0, y1);
    poly_.close();
    fill_contours(poly_);
    if (!ok()) return;
    const Pt box[4] = {{x0, y0}, {x1, y0}, {x1, y1}, {x0, y1}};
    stroke_path(box, 4, true);
    return;
  }
  if (s.fill && s.fill_a > 0) {
    if (axis() && s.tex == TEX_NONE) {
      const double *m = m_;
      canvas.fill_rect(m[0] * x0 + m[4], m[3] * y0 + m[5], m[0] * x1 + m[4], m[3] * y1 + m[5], s.fill_g, s.fill_a);
      if (!ok()) return;
      charged += 4 + canvas.cost;
      canvas.cost = 0;
    } else {
      Pt q[4] = {{x0, y0}, {x1, y0}, {x1, y1}, {x0, y1}};
      one(q, 4, poly_);
      fill_contours(poly_, true);
      if (!ok()) return;
    }
  }
  Pt box[4] = {{x0, y0}, {x1, y0}, {x1, y1}, {x0, y1}};
  if (s.stroke && s.sw > 0 && thin()) {
    const double ma = m_[0], mb = m_[1], mc = m_[2], md = m_[3], me = m_[4], mf = m_[5];
    Pt q[4];
    for (int i = 0; i < 4; i++) {
      double fx, fy;
      if (!to_floor(ma * box[i].x + mc * box[i].y + me + 0.25, &fx) ||
          !to_floor(mb * box[i].x + md * box[i].y + mf + 0.25, &fy))
        return;
      q[i] = {fx, fy};
    }
    double inv[6];
    if (inverse(inv)) {
      for (Pt &p : q)
        p = {inv[0] * (p.x - inv[4]) + inv[2] * (p.y - inv[5]), inv[1] * (p.x - inv[4]) + inv[3] * (p.y - inv[5])};
      thin_path(q, 4, true);
    } else {
      thin_path(box, 4, true);
    }
  } else if (s.stroke && s.sw > 0) {
    const Pt *nb = normalize(box, 4, nullptr);
    if (!ok()) return;
    double hw = s.sw / 2;
    if (s.join == MITER && axis()) {
      double bx0 = nb[0].x, by0 = nb[0].y, bx1 = nb[2].x, by1 = nb[2].y;
      Pt outer[4] = {{bx0 - hw, by0 - hw}, {bx1 + hw, by0 - hw}, {bx1 + hw, by1 + hw}, {bx0 - hw, by1 + hw}};
      if (std::fabs(bx1 - bx0) > s.sw && std::fabs(by1 - by0) > s.sw) {
        Pt inner[4] = {{bx0 + hw, by0 + hw}, {bx1 - hw, by0 + hw}, {bx1 - hw, by1 - hw}, {bx0 + hw, by1 - hw}};
        ring(outer, 4, inner, 4, ring_);
      } else {
        one(outer, 4, ring_);
      }
      stroke_contours(ring_);
    } else {
      pts_.assign(nb, nb + 4);
      stroker_.polyline(pts_.data(), 4, s.sw, true, s.cap, s.join, mscale(), stroke_, fr());
      if (!ok()) return;
      stroke_contours(stroke_);
    }
  }
}

void Api::round_rect(double x0, double y0, double x1, double y1, const double *radii, int nr) {
  double r4[4] = {radii[0], radii[0], radii[0], radii[0]};
  if (nr >= 4)
    for (int i = 0; i < 4; i++) r4[i] = radii[i];
  double mr = pymin(x1 - x0, y1 - y0) / 2;
  for (double &r : r4) r = pymax(0.0, pymin(r, mr));
  const double tl = r4[0], tr = r4[1], br = r4[2], bl = r4[3];
  std::vector<Pt> &pts = pts_;
  pts.clear();
  const double sc = mscale();
  auto corner = [&](double cx, double cy, double r, double a0) {
    if (r <= 0) {
      pts.push_back({cx, cy});
      return true;
    }
    int n = segments_for(r * sc, kPI / 2, fr());
    if (!ok()) return false;
    if (n < 2) n = 2;
    for (int i = 0; i <= n; i++) {
      double a = a0 + (kPI / 2) * i / n;
      pts.push_back({cx + r * py_cos(a), cy + r * py_sin(a)});
    }
    return true;
  };
  if (!corner(tr != 0 ? x1 - tr : x1, tr != 0 ? y0 + tr : y0, tr, -kPI / 2)) return;
  if (!corner(br != 0 ? x1 - br : x1, br != 0 ? y1 - br : y1, br, 0.0)) return;
  if (!corner(bl != 0 ? x0 + bl : x0, bl != 0 ? y1 - bl : y1, bl, kPI / 2)) return;
  if (!corner(tl != 0 ? x0 + tl : x0, tl != 0 ? y0 + tl : y0, tl, kPI)) return;
  one(pts.data(), (int)pts.size(), poly_);
  fill_contours(poly_);
  if (!ok()) return;
  stroke_path(pts.data(), (int)pts.size(), true);
}

void Api::ellipse_box(double a, double b, double c, double d, int64_t mode, double *cx, double *cy, double *rx,
                      double *ry) {
  if (mode == 1) {
    *cx = (a + c) / 2, *cy = (b + d) / 2, *rx = std::fabs(c - a) / 2, *ry = std::fabs(d - b) / 2;
  } else if (mode == 0) {
    *cx = a + c / 2, *cy = b + d / 2, *rx = std::fabs(c) / 2, *ry = std::fabs(d) / 2;
  } else if (mode == 2) {
    *cx = a, *cy = b, *rx = std::fabs(c), *ry = std::fabs(d);
  } else {
    *cx = a, *cy = b, *rx = std::fabs(c) / 2, *ry = std::fabs(d) / 2;
  }
}

// The noSmooth outline of an ellipse: Java2D's four Bezier quarters stepped about half a pixel,
// generated twice (the painter's box, then the lines) instead of kept.
void Api::thin_ellipse(double cx, double cy, double rx, double ry, double sc) {
  const Style &s = st_;
  const double k = 0.5522847498;
  // ~half a pixel a step; an ellipse far bigger than the canvas is capped at THIN_SEG_MAX steps a
  // quarter, charged before any point exists (runtime.py Api.ellipse, the same)
  int64_t seg;
  const double q = (std::fabs(rx) + std::fabs(ry)) * sc * 1.6;
  if (q >= (double) THIN_SEG_MAX) {
    seg = THIN_SEG_MAX;
  } else {
    if (!to_int(q, &seg)) return;
    if (seg < 4) seg = 4;
  }
  charged += (uint64_t) (4 * seg);
  if (s.stroke_a <= 0) return;
  const double Q[4][4] = {{rx, 0, 0, ry}, {0, ry, -rx, 0}, {-rx, 0, 0, -ry}, {0, -ry, rx, 0}};
  const int total = (int)seg * 4;
  // the Bernstein weights of t = i / seg depend on (i, seg) only: once per seg, not per quarter
  if (ecoef_seg_ != seg) {
    ecoef_.resize((size_t)seg * 4);
    for (int i = 0; i < (int)seg; i++) {
      double t = (double)i / (double)seg, u = 1 - t;
      ecoef_[4 * i] = u * u * u;
      ecoef_[4 * i + 1] = 3 * u * u * t;
      ecoef_[4 * i + 2] = 3 * u * t * t;
      ecoef_[4 * i + 3] = t * t * t;
    }
    ecoef_seg_ = seg;
  }
  auto gen = [&](int idx) {
    const double *q = Q[idx / seg];
    int i = idx % (int)seg;
    double ax = q[0], ay = q[1], bx = q[2], by = q[3];
    double c1x = ax == 0 ? ax + k * bx : ax, c1y = ay == 0 ? ay + k * by : ay;
    double c2x = bx == 0 ? bx + k * ax : bx, c2y = by == 0 ? by + k * ay : by;
    const double *w = &ecoef_[4 * (size_t)i];
    return Pt{cx + w[0] * ax + w[1] * c1x + w[2] * c2x + w[3] * bx, cy + w[0] * ay + w[1] * c1y + w[2] * c2y + w[3] * by};
  };
  const double ma = m_[0], mb = m_[1], mc = m_[2], md = m_[3], me = m_[4], mf = m_[5];
  auto dev = [&](int idx) {
    Pt p = gen(idx);
    return Pt{ma * p.x + mc * p.y + me, mb * p.x + md * p.y + mf};
  };
  has_painter_ = false;
  if (s.stex != TEX_NONE) {
    if (tex_is_gradient(s.stex)) {
      // the box of every point: a one-point-at-a-time version of make_painter's min/max
      Pt p0 = dev(0);
      double mnx = p0.x, mny = p0.y, mxx = mnx, mxy = mny;
      for (int i = 1; i < total; i++) {
        Pt p = dev(i);
        if (p.x < mnx) mnx = p.x;
        if (p.x > mxx) mxx = p.x;
        if (p.y < mny) mny = p.y;
        if (p.y > mxy) mxy = p.y;
      }
      poly_.clear();
      poly_.add(mnx, mny);
      poly_.add(mxx, mxy);
      poly_.close();
      if (!make_painter(s.stex, s.stex_p, s.stroke_g, s.stex_g2, &poly_)) return;
    } else if (!make_painter(s.stex, s.stex_p, s.stroke_g, s.stex_g2, nullptr)) {
      return;
    }
    has_painter_ = true;
  }
  const Painter *pp = has_painter_ ? &painter_ : nullptr;
  // each point rounded once (Python rounds it for both of its segments; same values)
  Pt first = dev(0), fr0;
  if (!canvas.round_half(first.x, &fr0.x) || !canvas.round_half(first.y, &fr0.y)) return;
  Pt prev = fr0;
  for (int i = 1; i < total && ok(); i++) {
    Pt p = dev(i), r;
    if (!canvas.round_half(p.x, &r.x) || !canvas.round_half(p.y, &r.y)) return;
    canvas.thin_line_r(prev.x, prev.y, r.x, r.y, s.stroke_g, s.stroke_a, pp);
    prev = r;
  }
  if (ok()) canvas.thin_line_r(prev.x, prev.y, fr0.x, fr0.y, s.stroke_g, s.stroke_a, pp);
  if (!ok()) return;
  cost_();
}

void Api::ellipse(double a, double b, double c, double d) {
  double cx, cy, rx, ry;
  ellipse_box(a, b, c, d, st_.ellipse_mode, &cx, &cy, &rx, &ry);
  const Style &s = st_;
  const double sc = mscale();
  if (use3d()) {
    int n = segments_for(pymax(rx, ry) * sc, kTWO_PI, fr());
    if (!ok()) return;
    poly_.clear();
    ellipse_points(cx, cy, rx, ry, n, poly_);
    poly_.close();
    std::vector<Pt> pts(poly_.pts);
    if (rx > 0 && ry > 0) {
      fill_contours(poly_);
      if (!ok()) return;
    }
    stroke_path(pts.data(), (int)pts.size(), true);
    return;
  }
  if (s.fill && s.fill_a > 0 && rx > 0 && ry > 0) {
    int n = segments_for(pymax(rx, ry) * sc, kTWO_PI, fr());
    if (!ok()) return;
    poly_.clear();
    ellipse_points(cx, cy, rx, ry, n, poly_);
    poly_.close();
    fill_contours(poly_);
    if (!ok()) return;
  }
  if (s.stroke && s.sw > 0 && thin()) {
    thin_ellipse(cx, cy, rx, ry, sc);
  } else if (s.stroke && s.sw > 0) {
    double hw = s.sw / 2;
    if (axis()) {
      Pt q[4] = {{cx - rx, cy}, {cx + rx, cy}, {cx, cy - ry}, {cx, cy + ry}};
      const Pt *nq = normalize(q, 4, nullptr);
      if (!ok()) return;
      double lx = nq[0].x, rx_ = nq[1].x, ty = nq[2].y, by = nq[3].y;
      cx = (lx + rx_) / 2;
      rx = std::fabs(rx_ - lx) / 2;
      cy = (ty + by) / 2;
      ry = std::fabs(by - ty) / 2;
    } else {
      Pt q = {cx, cy};
      q = normalize(&q, 1, nullptr)[0];
      if (!ok()) return;
      cx = q.x;
      cy = q.y;
    }
    int n = segments_for((pymax(rx, ry) + hw) * sc, kTWO_PI, fr());
    if (!ok()) return;
    stroke_.clear();
    ellipse_points(cx, cy, rx + hw, ry + hw, n, stroke_);
    stroke_.close();
    if (rx - hw > 0 && ry - hw > 0) {
      ellipse_points(cx, cy, rx - hw, ry - hw, n, stroke_);
      stroke_.close();
      ring(stroke_.at(0), (int)stroke_.size(0), stroke_.at(1), (int)stroke_.size(1), ring_);
      stroke_contours(ring_);
    } else {
      stroke_contours(stroke_);
    }
  }
}

void Api::arc(double a, double b, double c, double d, double start, double stop, bool has_mode, double mode_d) {
  double cx, cy, rx, ry;
  ellipse_box(a, b, c, d, st_.ellipse_mode, &cx, &cy, &rx, &ry);
  if (std::isinf(start) || std::isinf(stop) || start != start || stop != stop || stop < start) return;
  if (start < -ARC_FOLD) {
    // far below zero: folded into (-2pi, 0] first, the sweep kept (runtime.py Api.arc, the same)
    const double r = std::fmod(start, 2 * kPI);
    stop = stop + (r - start);
    start = r;
  }
  while (start < 0) {
    start += 2 * kPI;
    stop += 2 * kPI;
  }
  if (stop - start > 2 * kPI) {
    start = 0.0;
    stop = 2 * kPI;
  }
  const double sweep = stop - start;
  int64_t mode = 0;
  if (has_mode && !to_int(mode_d, &mode)) return;
  const double sc = mscale();
  int n = segments_for(pymax(rx, ry) * sc, sweep, fr());
  if (!ok()) return;
  pts_.clear();
  for (int i = 0; i <= n; i++) {
    double t = start + sweep * i / n;
    pts_.push_back({cx + rx * py_cos(t), cy + ry * py_sin(t)});
  }
  const Style &s = st_;
  if (s.fill && s.fill_a > 0 && rx > 0 && ry > 0) {
    one(pts_.data(), (int)pts_.size(), poly_);
    if (!(mode == 1 || mode == 2)) {
      poly_.pts.push_back({cx, cy});
      poly_.ends[0]++;
    }
    fill_contours(poly_);
    if (!ok()) return;
  }
  if (s.stroke && s.sw > 0) {
    anc_.assign((size_t)n + 2, 0);
    anc_[0] = anc_[n] = 1;
    if (mode == 3) {
      pts_.push_back({cx, cy});
      anc_[n + 1] = 1;
      stroke_path(pts_.data(), n + 2, true, anc_.data());
    } else {
      stroke_path(pts_.data(), n + 1, mode == 2, anc_.data());
    }
  }
}

void Api::triangle(double x1, double y1, double x2, double y2, double x3, double y3) {
  Pt p[3] = {{x1, y1}, {x2, y2}, {x3, y3}};
  poly(p, 3);
}

void Api::quad(double x1, double y1, double x2, double y2, double x3, double y3, double x4, double y4) {
  Pt p[4] = {{x1, y1}, {x2, y2}, {x3, y3}, {x4, y4}};
  poly(p, 4);
}

void Api::poly(const Pt *pts, int n) {
  one(pts, n, poly_);
  fill_contours(poly_);
  if (!ok()) return;
  stroke_path(pts, n, true);
}

// ---------------------------------------------------------------- curves
bool Api::bez_pts(double x0, double y0, double x1, double y1, double x2, double y2, double x3, double y3,
                  std::vector<Pt> &out) {
  double ln = py_hypot(x1 - x0, y1 - y0) + py_hypot(x2 - x1, y2 - y1) + py_hypot(x3 - x2, y3 - y2);
  int64_t v;
  if (!to_int(ln * mscale() / 3, &v)) return false;
  int n = (int)imax(4, imin(64, v + (v < INT64_MAX ? 1 : 0)));
  if (out.size() + (size_t)n > MAX_POINTS) {
    raise(Fault::Memory, "a shape has too many points for the VM");
    return false;
  }
  for (int i = 1; i <= n; i++) {
    double t = (double)i / n, u = 1 - t;
    double a = u * u * u, b = 3 * u * u * t, c = 3 * u * t * t, d = t * t * t;
    out.push_back({a * x0 + b * x1 + c * x2 + d * x3, a * y0 + b * y1 + c * y2 + d * y3});
  }
  return true;
}

bool Api::curve_seg(const Pt &p0, const Pt &p1, const Pt &p2, const Pt &p3, std::vector<Pt> &out) {
  double s = (1 - curve_tightness_) / 2;
  double c1x = p1.x + s * (p2.x - p0.x) / 3, c1y = p1.y + s * (p2.y - p0.y) / 3;
  double c2x = p2.x - s * (p3.x - p1.x) / 3, c2y = p2.y - s * (p3.y - p1.y) / 3;
  return bez_pts(p1.x, p1.y, c1x, c1y, c2x, c2y, p2.x, p2.y, out);
}

void Api::bezier(double x1, double y1, double x2, double y2, double x3, double y3, double x4, double y4) {
  kpts_.clear();
  kpts_.push_back({x1, y1});
  if (!bez_pts(x1, y1, x2, y2, x3, y3, x4, y4, kpts_)) return;
  int n = (int)kpts_.size();
  one(kpts_.data(), n, poly_);
  fill_contours(poly_);
  if (!ok()) return;
  anc_.assign((size_t)n, 0);
  anc_[0] = anc_[n - 1] = 1;
  stroke_path(kpts_.data(), n, false, anc_.data());
}

void Api::curve(double x1, double y1, double x2, double y2, double x3, double y3, double x4, double y4) {
  kpts_.clear();
  kpts_.push_back({x2, y2});
  if (!curve_seg({x1, y1}, {x2, y2}, {x3, y3}, {x4, y4}, kpts_)) return;
  int n = (int)kpts_.size();
  one(kpts_.data(), n, poly_);
  fill_contours(poly_);
  if (!ok()) return;
  anc_.assign((size_t)n, 0);
  anc_[0] = anc_[n - 1] = 1;
  stroke_path(kpts_.data(), n, false, anc_.data());
}

double Api::bezier_point(double a, double b, double c, double d, double t) {
  double u = 1 - t;
  return f32(a * u * u * u + 3 * b * t * u * u + 3 * c * t * t * u + d * t * t * t);
}

double Api::bezier_tangent(double a, double b, double c, double d, double t) {
  return f32(3 * t * t * (-a + 3 * b - 3 * c + d) + 6 * t * (a - 2 * b + c) + 3 * (-a + b));
}

double Api::curve_point(double a, double b, double c, double d, double t) {
  double t2 = t * t, t3 = t * t * t;
  return f32(0.5 * ((2 * b) + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t2 + (-a + 3 * b - 3 * c + d) * t3));
}

// ---------------------------------------------------------------- beginShape
void Api::begin_shape(bool has_kind, double kind) {
  int64_t k = 20;
  if (has_kind && !to_int(kind, &k)) return;
  reset_shape();
  shape_open_ = true;
  shape_kind_ = k;
  shape_normals_ = false;
}

bool Api::need_shape() {
  if (!shape_open_) {
    raise(Fault::Runtime, "vertex() must be between beginShape() and endShape()");
    return false;
  }
  if (items_.size() > 20000) {
    raise(Fault::Memory, "a shape has more than 20000 vertices");
    return false;
  }
  charged += 2;
  return true;
}

void Api::vertex(double x, double y) { vertex3(x, y, false, 0.0); }

void Api::vertex3(double x, double y, bool has_z, double z) {
  if (!need_shape()) return;
  Item it{'v', {x, y, 0, 0, 0, 0}};
  if (p3d) {
    it.z[0] = has_z ? z : 0.0;
    std::memcpy(it.n, t3->normal, sizeof it.n);
  }
  items_.push_back(it);
}

void Api::bezier_vertex(const double *a, int n) {
  if (!need_shape()) return;
  if (n == 6) {
    items_.push_back({'b', {a[0], a[1], a[2], a[3], a[4], a[5]}});
    return;
  }
  Item it{'b', {a[0], a[1], a[3], a[4], a[6], a[7]}};
  if (p3d) {
    it.z[0] = a[2];
    it.z[1] = a[5];
    it.z[2] = a[8];
    it.zset = true;
  }
  items_.push_back(it);
}

void Api::quadratic_vertex(const double *a, int n) {
  if (!need_shape()) return;
  if (n == 4) {
    items_.push_back({'q', {a[0], a[1], a[2], a[3], 0, 0}});
    return;
  }
  Item it{'q', {a[0], a[1], a[3], a[4], 0, 0}};
  if (p3d) {
    it.z[0] = a[2];
    it.z[1] = a[5];
    it.zset = true;
  }
  items_.push_back(it);
}

void Api::curve_vertex(double x, double y) { curve_vertex3(x, y, 0.0); }

void Api::curve_vertex3(double x, double y, double z) {
  if (!need_shape()) return;
  Item it{'c', {x, y, 0, 0, 0, 0}};
  if (p3d) {
    it.z[0] = z;
    std::memcpy(it.n, t3->normal, sizeof it.n);
  }
  items_.push_back(it);
}

// Api._flatten of items_[from, to): points and which of them are the sketch's own vertices
bool Api::flatten(size_t from, size_t to, std::vector<Pt> &pts, std::vector<uint8_t> &anchors) {
  std::vector<Pt> &curve = curve_;
  curve.clear();
  const size_t base = pts.size();      // Python flattens into a fresh list: points before are not "pts"
  auto add_tail = [&](size_t before) {   // anchors for points appended since `before`
    size_t added = pts.size() - before;
    for (size_t i = 0; i < added; i++) anchors.push_back(i + 1 == added ? 1 : 0);
  };
  auto flush_curve = [&]() {
    if (curve.size() >= 4) {
      pts.push_back(curve[1]);
      anchors.push_back(1);
      for (size_t i = 0; i + 3 < curve.size(); i++) {
        size_t before = pts.size();
        if (!curve_seg(curve[i], curve[i + 1], curve[i + 2], curve[i + 3], pts)) return false;
        add_tail(before);
      }
    }
    curve.clear();
    return true;
  };
  for (size_t j = from; j < to; j++) {
    const Item &it = items_[j];
    if (it.k == 'c') {
      curve.push_back({it.v[0], it.v[1]});
      continue;
    }
    if (!flush_curve()) return false;
    if (it.k == 'v') {
      pts.push_back({it.v[0], it.v[1]});
      anchors.push_back(1);
    } else if (it.k == 'b' && pts.size() > base) {
      Pt p0 = pts.back();
      size_t before = pts.size();
      if (!bez_pts(p0.x, p0.y, it.v[0], it.v[1], it.v[2], it.v[3], it.v[4], it.v[5], pts)) return false;
      add_tail(before);
    } else if (it.k == 'q' && pts.size() > base) {
      Pt p0 = pts.back();
      double x0 = p0.x, y0 = p0.y, cx = it.v[0], cy = it.v[1], x3 = it.v[2], y3 = it.v[3];
      const double k = 2.0 / 3.0;
      size_t before = pts.size();
      if (!bez_pts(x0, y0, x0 + k * (cx - x0), y0 + k * (cy - y0), x3 + k * (cx - x3), y3 + k * (cy - y3), x3, y3,
                   pts))
        return false;
      add_tail(before);
    }
    if (pts.size() > MAX_POINTS) {
      raise(Fault::Memory, "a shape has too many points for the VM");
      return false;
    }
  }
  return flush_curve();
}

void Api::begin_contour() {
  if (!need_shape()) return;
  uint32_t a = (uint32_t)ctr_pts_.size();
  if (!flatten(0, items_.size(), ctr_pts_, ctr_anc_)) return;
  const uint32_t ia = (uint32_t)ctr_items_.size();
  ctr_items_.insert(ctr_items_.end(), items_.begin(), items_.end());
  ctrs_.push_back({false, a, (uint32_t)ctr_pts_.size(), ia, (uint32_t)ctr_items_.size()});
  items_.clear();
}

void Api::end_contour() {
  if (!need_shape()) return;
  uint32_t a = (uint32_t)ctr_pts_.size();
  if (!flatten(0, items_.size(), ctr_pts_, ctr_anc_)) return;
  const uint32_t ia = (uint32_t)ctr_items_.size();
  ctr_items_.insert(ctr_items_.end(), items_.begin(), items_.end());
  ctrs_.push_back({true, a, (uint32_t)ctr_pts_.size(), ia, (uint32_t)ctr_items_.size()});
  items_.clear();
}

void Api::end_shape(bool has_mode, double mode_d) {
  if (!shape_open_) {
    raise(Fault::Runtime, "endShape() without beginShape()");
    return;
  }
  shape_open_ = false;           // (the items and contours are dropped when this call ends)
  struct Reset {
    Api *a;
    ~Reset() { a->reset_shape(); }
  } reset{this};
  const int64_t kind = shape_kind_;
  int64_t mode = 1;
  if (p3d && (use3d() || shape_has_z())) {
    if (has_mode && !to_int(mode_d, &mode)) return;
    end_shape3d(mode == 2);
    return;
  }
  if (has_mode && !to_int(mode_d, &mode)) return;
  const bool close = mode == 2;
  if (kind == 20 || !(kind == 3 || kind == 5 || kind == 9 || kind == 10 || kind == 11 || kind == 17 || kind == 18)) {
    std::vector<Pt> &main = kpts_;
    std::vector<uint8_t> &anchors = anc_;
    main.clear();
    anchors.clear();
    for (const SavedContour &c : ctrs_) {
      if (c.hole) continue;
      main.insert(main.end(), ctr_pts_.begin() + c.a, ctr_pts_.begin() + c.b);
      anchors.insert(anchors.end(), ctr_anc_.begin() + c.a, ctr_anc_.begin() + c.b);
    }
    if (!flatten(0, items_.size(), main, anchors)) return;
    const int n = (int)main.size();
    if (n >= 2) {
      if (n >= 3) {
        poly_.clear();
        poly_.pts.assign(main.begin(), main.end());
        poly_.close();
        for (const SavedContour &c : ctrs_) {
          if (!c.hole) continue;
          poly_.pts.insert(poly_.pts.end(), ctr_pts_.begin() + c.a, ctr_pts_.begin() + c.b);
          poly_.close();
        }
        fill_contours(poly_);
        if (!ok()) return;
      }
      stroke_path(main.data(), n, close, anchors.data());
      if (!ok()) return;
    }
    for (const SavedContour &c : ctrs_) {
      if (!c.hole) continue;
      stroke_path(ctr_pts_.data() + c.a, (int)(c.b - c.a), true, ctr_anc_.data() + c.a);
      if (!ok()) return;
    }
    return;
  }
  std::vector<Pt> &pts = kind_pts_;
  pts.clear();
  for (const Item &it : items_)
    if (it.k == 'v' || it.k == 'c') pts.push_back({it.v[0], it.v[1]});
  const int n = (int)pts.size();
  if (kind == 3) {
    for (int i = 0; i < n && ok(); i++) point(pts[i].x, pts[i].y);
  } else if (kind == 5) {
    for (int i = 0; i + 1 < n && ok(); i += 2) line(pts[i].x, pts[i].y, pts[i + 1].x, pts[i + 1].y);
  } else if (kind == 9) {
    for (int i = 0; i + 2 < n && ok(); i += 3) poly(&pts[i], 3);
  } else if (kind == 10) {
    for (int i = 0; i + 2 < n && ok(); i++) poly(&pts[i], 3);
  } else if (kind == 11) {
    for (int i = 1; i + 1 < n && ok(); i++) {
      Pt t[3] = {pts[0], pts[i], pts[i + 1]};
      poly(t, 3);
    }
  } else if (kind == 17) {
    for (int i = 0; i + 3 < n && ok(); i += 4) poly(&pts[i], 4);
  } else if (kind == 18) {
    for (int i = 0; i + 3 < n && ok(); i += 2) {
      Pt q[4] = {pts[i], pts[i + 1], pts[i + 3], pts[i + 2]};
      poly(q, 4);
    }
  }
}

// ================================================================== text
void Api::text_size(double s) {
  st_.text_size = pymax(0.0, s);
  st_.has_leading = false;
}

void Api::text_align(double ax, bool has_ay, double ay) {
  int64_t x, y = 0;
  if (!to_int(ax, &x)) return;
  if (has_ay && !to_int(ay, &y)) return;
  st_.align_x = x;
  st_.align_y = y;
}

double Api::text_ascent() { return f32(font::ASCENT * st_.text_size / 1000); }
double Api::text_descent() { return f32(font::DESCENT * st_.text_size / 1000); }

double Api::leading_() {
  const Style &s = st_;
  return s.has_leading ? s.leading : (font::ASCENT + font::DESCENT) * s.text_size / 1000 * 1.275;
}

static inline const font::Glyph *glyph_for_width(uint16_t c) {
  const font::Glyph *g = font::find(c);
  return g ? g : font::find(63);
}

// the running sum text_width takes (without the f32 and the size), of s[a, b) with no '\n'
double Api::width_of(const uint16_t *s, int a, int b) {
  double w = 0.0;
  for (int i = a; i < b; i++) w += glyph_for_width(s[i])->advance;
  return w;
}

double Api::text_width(const uint16_t *s, int n) {
  if (n > 0 && !s) return 0.0;
  double w = 0.0, best = 0.0;
  for (int i = 0; i < n; i++) {
    if (s[i] == '\n') {
      best = pymax(best, w);
      w = 0.0;
      continue;
    }
    w += glyph_for_width(s[i])->advance;
  }
  return f32(pymax(best, w) * st_.text_size / 1000);
}

bool Api::glyphs(const uint16_t *s, int a, int b, double x, double y, Contours &out) {
  const double k = st_.text_size / 1000;
  for (int i = a; i < b; i++) {
    const font::Glyph *g = font::find(s[i]);
    if (!g) g = font::find(s[i] > 32 ? 63 : 32);
    if (out.pts.size() + 4096 > MAX_POINTS) {
      raise(Fault::Memory, "the text has too many points for the VM");
      return false;
    }
    for (int c = 0; c < g->n_contours; c++) {
      const font::Contour &ct = font::CONTOURS[g->first_contour + c];
      const int16_t *p = font::POINTS + 2 * ct.first_point;
      for (int j = 0; j < ct.n_points; j++) out.add(x + p[2 * j] * k, y + p[2 * j + 1] * k);
      out.close();
    }
    x += g->advance * k;
    charged += 8;
  }
  return true;
}

void Api::text_paint(const Contours &c) {
  const Style &st = st_;
  if (c.empty() || st.fill_a <= 0) return;
  if (use3d()) {
    fill3(c, false);
    return;
  }
  xf(c, dev_);
  has_painter_ = false;
  if (st.tex != TEX_NONE) {
    if (!make_painter(st.tex, st.tex_p, st.fill_g, st.tex_g2, &dev_)) return;
    has_painter_ = true;
  }
  bool aa = canvas.aa;
  canvas.aa = true;
  canvas.fill(dev_, st.fill_g, st.fill_a, has_painter_ ? &painter_ : nullptr);
  canvas.aa = aa;
  if (!ok()) return;
  cost_();
}

void Api::text_z(const uint16_t *s, int n, double x, double y, double z) {
  if (p3d && z != 0.0) {
    push_matrix();
    if (!ok()) return;
    translate(0.0, 0.0, z);
    text(s, n, x, y);
    if (!ok()) return;
    pop_matrix();
    return;
  }
  text(s, n, x, y);
}

void Api::text(const uint16_t *s, int n, double x, double y) {
  const Style &st = st_;
  if (n > 2000) n = 2000;
  if (n <= 0 || st.text_size <= 0) return;
  int nlines = 1;
  for (int i = 0; i < n; i++) nlines += s[i] == '\n';
  const double lead = leading_();
  const double asc = text_ascent(), desc = text_descent();
  if (st.align_y == 3) y += asc / 2 - (nlines - 1) * lead / 2;
  else if (st.align_y == 101) y += asc;
  else if (st.align_y == 102) y -= desc + (nlines - 1) * lead;
  poly_.clear();
  int a = 0, li = 0;
  for (int i = 0; i <= n; i++) {
    if (i < n && s[i] != '\n') continue;
    double w = text_width(s + a, i - a);
    double lx = st.align_x == 3 ? x - w / 2 : st.align_x == 39 ? x - w : x;
    if (!glyphs(s, a, i, lx, y + li * lead, poly_)) return;
    a = i + 1;
    li++;
  }
  text_paint(poly_);
}

void Api::text_box(const uint16_t *s, int n, double x, double y, double w, double h) {
  const Style &st = st_;
  if (n > 2000) n = 2000;
  if (n <= 0 || st.text_size <= 0) return;
  if (st.rect_mode == 3) {
    x = x - w / 2;
    y = y - h / 2;
  } else if (st.rect_mode == 1) {
    w = w - x;
    h = h - y;
  }
  // word wrap: `cur` is always a contiguous range of its paragraph (words split on one space)
  lines_.clear();
  const double space = glyph_for_width(' ')->advance;
  int ps = 0;
  for (int pe = 0; pe <= n; pe++) {
    if (pe < n && s[pe] != '\n') continue;
    int ca = ps, cb = ps;          // cur = s[ca, cb), empty when ca == cb
    double cw = 0.0;               // text_width's running sum over cur
    int ws = ps;
    for (int we = ps; we <= pe; we++) {
      if (we < pe && s[we] != ' ') continue;
      if (ca == cb) {
        ca = ws;
        cb = we;
        cw = width_of(s, ws, we);
      } else {
        double tw = cw + space;
        for (int i = ws; i < we; i++) tw += glyph_for_width(s[i])->advance;
        if (f32(pymax(0.0, tw) * st.text_size / 1000) > w) {
          lines_.push_back({ca, cb});
          ca = ws;
          cb = we;
          cw = width_of(s, ws, we);
        } else {
          cb = we;
          cw = tw;
        }
      }
      ws = we + 1;
    }
    lines_.push_back({ca, cb});
    ps = pe + 1;
  }
  const double lead = leading_();
  const double asc = text_ascent();
  double yy = y + asc;
  poly_.clear();
  for (const auto &ln : lines_) {
    if (yy - asc + lead > y + h + 0.01 && !poly_.empty()) break;
    double lw = text_width(s + ln.first, ln.second - ln.first);
    double lx = st.align_x == 3 ? x + (w - lw) / 2 : st.align_x == 39 ? x + w - lw : x;
    if (!glyphs(s, ln.first, ln.second, lx, yy, poly_)) return;
    yy += lead;
  }
  text_paint(poly_);
}

// ================================================================== pixels
void Api::load_pixels(int32_t *out, bool first) {
  if (first) charged += (W * H * 4) >> 6;
  for (int i = 0; i < W * H; i++) {
    int g = canvas.buf[i];
    out[i] = (int32_t)(0xFF000000u | (uint32_t)(g << 16) | (uint32_t)(g << 8) | (uint32_t)g);
  }
  charged += W * H / 16;
}

void Api::update_pixels(const int32_t *px) {
  for (int i = 0; i < W * H; i++) {
    int32_t c = px[i];
    int a = (c >> 24) & 255, g = luma(c);
    canvas.buf[i] = (uint8_t)(a == 255 ? g : (g * a + 255 * (255 - a)) / 255);
  }
  charged += W * H / 8;
}

int32_t Api::get(double x, double y) {
  int64_t ix, iy;
  if (!to_int(x, &ix) || !to_int(y, &iy)) return 0;
  if (0 <= ix && ix < W && 0 <= iy && iy < H) {
    uint32_t g = canvas.buf[iy * W + ix];
    return (int32_t)(0xFF000000u | (g << 16) | (g << 8) | g);
  }
  return 0;
}

void Api::set(double x, double y, double c) {
  int64_t ix, iy, cv;
  if (!to_int(x, &ix) || !to_int(y, &iy)) return;
  if (0 <= ix && ix < W && 0 <= iy && iy < H) {
    if (!colour_bits(this, c, &cv, fr())) return;
    canvas.buf[iy * W + ix] = (uint8_t)luma((int32_t)(uint32_t)(uint64_t)cv);
  }
}

// ================================================================== randomness
double Api::noise(double x, double y, double z) {
  charged += 4;
  return rand.noise(x, y, z, fr());
}

void Api::random_seed(double s) {
  if (!std::isfinite(s)) {
    raise(Fault::Runtime, ERR_ARITH);
    return;
  }
  uint64_t bits = pyint_bits(s);
  last_rseed = (int)(uint32_t)bits;
  rand.seed(bits);
}

void Api::noise_seed(double s) {
  if (!std::isfinite(s)) {
    raise(Fault::Runtime, ERR_ARITH);
    return;
  }
  rand.noise_seed(pyint_bits(s));
}

void Api::noise_detail(double lod, bool has_falloff, double falloff) {
  int64_t l;
  if (!to_int(lod, &l)) return;
  rand.noise_detail(l, has_falloff, falloff);
}

}  // namespace pde
