#include "pde_fp.h"  // first: no fused multiply-add (see the header)
#include "pde_raster.h"

#include <cmath>
#include <cstring>

namespace pde {

static const char *const ERR_FLOAT = "arithmetic error (ValueError)";
static const char *const ERR_INDEX = "index out of range (IndexError)";

// math.floor / math.ceil / int() of a float: NaN and +-inf raise in Python
static inline bool finite_or_raise(double v, FaultRef fr) {
  if (std::isfinite(v)) return true;
  fr.raise(Fault::Runtime, ERR_FLOAT);
  return false;
}

static inline void add_cost(uint64_t &cost, double n) {
  if (n <= 0) return;
  cost += n >= 1.8e19 ? (uint64_t)1 << 62 : (uint64_t)n;
}

Canvas::Canvas(int grey) { std::memset(buf, grey, sizeof buf); }

int blend_grey(int mode, int v, int g, int a) {
  if (mode == 0) return g;
  const int sa = a + (a >= 0x7F ? 1 : 0), da = 0x100 - sa;
  switch (mode) {
    case 2: { int r = v + ((g * sa) >> 8); return r < 255 ? r : 255; }
    case 4: { int r = v - ((g * sa) >> 8); return r > 0 ? r : 0; }
    case 8: return (v * da + (g > v ? g : v) * sa) >> 8;
    case 16: return (v * da + (g < v ? g : v) * sa) >> 8;
    case 32: return (v * da + (v > g ? v - g : g - v) * sa) >> 8;
    case 64: {
      int sub = (g * (v + (v >= 0x7F ? 1 : 0))) >> 7;
      int r = (v * da + (v + g - sub) * sa) >> 8;     // Python >> floors (arithmetic shift)
      return r < 0 ? 0 : r > 255 ? 255 : r;
    }
    case 128: return (v * da + ((g * (v + 1)) >> 8) * sa) >> 8;
    case 256: return (v * da + (v + g - ((g * (v + 1)) >> 8)) * sa) >> 8;
    default: return (int)(v + (g - v) * (a / 255.0) + 0.5);
  }
}

const uint8_t *Canvas::table(int g, int a) {
  if (g != tab_g_ || a != tab_a_ || blend != tab_m_) {
    if (blend == 1) {
      double f = a / 255.0;
      for (int v = 0; v < 256; v++) tab_[v] = (uint8_t)(int)(v + (g - v) * f + 0.5);
    } else {
      for (int v = 0; v < 256; v++) tab_[v] = (uint8_t)blend_grey(blend, v, g, a);
    }
    tab_g_ = g;
    tab_a_ = a;
    tab_m_ = blend;
  }
  return tab_;
}

void Canvas::clear(int g, int a) {
  if (a >= 255) {
    std::memset(buf, g, sizeof buf);
  } else {
    const int m = blend;             // background() ignores blendMode()
    blend = 1;
    const uint8_t *t = table(g, a);
    for (int i = 0; i < W * H; i++) buf[i] = t[buf[i]];
    blend = m;
  }
  cost += W * H / 8;
}

void Canvas::span(int y, int x0, int x1, int g, int a) {
  if (x1 <= x0) return;
  uint8_t *p = buf + y * W;
  if (blend != 1) {
    if (a > 0 || blend == 0) {
      const uint8_t *t = table(g, a);
      for (int x = x0; x < x1; x++) p[x] = t[p[x]];
    }
  } else if (a >= 255) {
    std::memset(p + x0, g, (size_t)(x1 - x0));
  } else if (a > 0) {
    const uint8_t *t = table(g, a);
    for (int x = x0; x < x1; x++) p[x] = t[p[x]];
  }
  cost += (uint64_t)((x1 - x0) >> 3);
}

void Canvas::pixel(int i, int g, double cov) {
  if (cov <= 0.0) return;
  int v = buf[i];
  if (blend != 1) {       // (the table's entry, without building the table)
    buf[i] = (uint8_t)blend_grey(blend, v, g, (int)((cov < 1.0 ? cov : 1.0) * 255 + 0.5));
    return;
  }
  if (cov >= 1.0) buf[i] = (uint8_t)g;
  else buf[i] = (uint8_t)(int)(v + (g - v) * cov + 0.5);
}

bool Canvas::round_half(double v, double *out) {
  v += 0.5;
  if (!finite_or_raise(v, fr)) return false;
  *out = std::floor(v);
  return true;
}

void Canvas::thin_line(double x0, double y0, double x1, double y1, int g, int a, const Painter *painter) {
  double axd, ayd, bxd, byd;
  if (!round_half(x0, &axd) || !round_half(y0, &ayd) || !round_half(x1, &bxd) || !round_half(y1, &byd)) return;
  thin_line_r(axd, ayd, bxd, byd, g, a, painter);
}

void Canvas::thin_line_r(double axd, double ayd, double bxd, double byd, int g, int a, const Painter *painter) {
  double dxd = std::fabs(bxd - axd), ndyd = std::fabs(byd - ayd);
  double stepsd = (ndyd > dxd ? ndyd : dxd) + 1;
  add_cost(cost, stepsd);
  if (stepsd > 4 * (W + H)) return;
  // a short line far off the canvas touches no pixel (and its ints would not fit)
  if (std::fabs(axd) > 1e9 || std::fabs(ayd) > 1e9) return;
  int ax = (int)axd, ay = (int)ayd, bx = (int)bxd, by = (int)byd;
  int dx = ax > bx ? ax - bx : bx - ax, dy = -(ay > by ? ay - by : by - ay);
  int sx = bx > ax ? 1 : -1, sy = by > ay ? 1 : -1;
  int err = dx + dy;
  int steps = (int)stepsd;
  double f = a / 255.0;
  for (int k = 0; k < steps; k++) {
    if (0 <= ax && ax < W && 0 <= ay && ay < H) {
      int i = ay * W + ax;
      if (painter) {
        if (!painter->row_ok(ay)) {
          fr.raise(Fault::Runtime, "arithmetic error (OverflowError)");
          return;
        }
        buf[i] = painter->pixel(ax, ay);
      } else if (blend != 1) {
        buf[i] = table(g, a)[buf[i]];
      } else if (a >= 255) {
        buf[i] = (uint8_t)g;
      } else {
        int v = buf[i];
        buf[i] = (uint8_t)(int)(v + (g - v) * f + 0.5);
      }
    }
    if (ax == bx && ay == by) break;
    int e2 = 2 * err;
    if (e2 >= dy) {
      err += dy;
      ax += sx;
    }
    if (e2 <= dx) {
      err += dx;
      ay += sy;
    }
  }
}

// crisp rectangle corner: int(math.ceil(floor(v + 0.25) + 0.25 - 0.5)), v already in [0, W or H]
static bool crisp_first(double v, int *out, FaultRef fr) {
  if (!finite_or_raise(v + 0.25, fr)) return false;
  double q = std::floor(v + 0.25) + 0.25;
  *out = (int)std::ceil(q - 0.5);
  return true;
}

void Canvas::fill_rect(double x0, double y0, double x1, double y1, int g, int a) {
  if (x1 < x0) {
    double t = x0;
    x0 = x1;
    x1 = t;
  }
  if (y1 < y0) {
    double t = y0;
    y0 = y1;
    y1 = t;
  }
  x0 = pymax(x0, 0.0);
  x1 = pymin(x1, (double)W);
  y0 = pymax(y0, 0.0);
  y1 = pymin(y1, (double)H);
  if (x1 <= x0 || y1 <= y0 || a <= 0) return;
  if (!aa) {
    int fx0, fx1, fy0, fy1;
    if (!crisp_first(x0, &fx0, fr) || !crisp_first(x1, &fx1, fr)) return;
    int xa = fx0 > 0 ? fx0 : 0, xb = fx1 < W ? fx1 : W;
    if (!crisp_first(y0, &fy0, fr) || !crisp_first(y1, &fy1, fr)) return;
    int ya = fy0 > 0 ? fy0 : 0, yb = fy1 < H ? fy1 : H;
    for (int y = ya; y < yb; y++) span(y, xa, xb, g, a);
    return;
  }
  if (!finite_or_raise(x0, fr) || !finite_or_raise(x1, fr) || !finite_or_raise(y0, fr) || !finite_or_raise(y1, fr))
    return;
  double af = a / 255.0;
  int ix0 = (int)x0, ix1 = (int)std::ceil(x1);
  int full0 = (int)std::ceil(x0), full1 = (int)x1;
  int yb = (int)std::ceil(y1);
  for (int y = (int)y0; y < yb; y++) {
    double vy = pymin(y1, y + 1.0) - pymax(y0, (double)y);
    if (vy <= 0) continue;
    int base = y * W;
    double f = vy * af;
    if (ix1 - ix0 == 1) {
      pixel(base + ix0, g, (x1 - x0) * f);
      continue;
    }
    if (full0 > ix0) pixel(base + ix0, g, (full0 - x0) * f);
    if (vy >= 0.999999) {
      span(y, full0, full1, g, a);
    } else {
      for (int x = full0; x < full1; x++) pixel(base + x, g, f);
      cost += (uint64_t)((full1 - full0) >> 2);
    }
    if (ix1 > full1) pixel(base + full1, g, (x1 - full1) * f);
  }
}

// raster._spans: non-zero spans [xa, xb) where the line y = sy is inside the active edges
void Canvas::spans(double sy, std::vector<Span> &out) {
  out.clear();
  xs_.clear();
  for (const Edge &e : active_)
    if (e.y0 <= sy && sy < e.y1) xs_.push_back({e.x0 + (sy - e.y0) * e.dx, e.d});
  if (xs_.empty()) return;
  xsort_.sort(xs_.data(), (int)xs_.size(), XdLt());
  int wind = 0;
  double start = 0.0;
  for (const Xd &v : xs_) {
    int before = wind;
    wind += v.d;
    if (before == 0 && wind != 0) {
      start = v.x;
    } else if (before != 0 && wind == 0) {
      if (v.x > start) {
        if (!out.empty() && out.back().b >= start) out.back().b = v.x;
        else out.push_back({start, v.x});
      }
    }
  }
}

void Canvas::fill(const Contours &contours, int g, int a, const Painter *painter) {
  if (a <= 0) return;
  // Contours wholly above or below the canvas (finite) never reach a pixel: they are counted
  // (cost, ymin/ymax) but not kept -- unless a NaN makes the sort order depend on every element.
  size_t count = 0;
  double ymin = H, ymax = 0.0;
  for (int pass = 0; pass < 2; pass++) {
    edges_.clear();
    count = 0;
    ymin = H;
    ymax = 0.0;
    bool culled = false, nan = false;
    for (int ci = 0; ci < contours.count(); ci++) {
      const int n = (int)contours.size(ci);
      if (n < 2) continue;
      const Pt *pts = contours.at(ci);
      bool cull = false;
      if (pass == 0) {
        bool fin = true;
        double lo = pts[0].y, hi = pts[0].y;
        for (int i = 0; i < n && fin; i++) {
          fin = std::isfinite(pts[i].x) && std::isfinite(pts[i].y);
          if (pts[i].y < lo) lo = pts[i].y;
          if (pts[i].y > hi) hi = pts[i].y;
        }
        cull = fin && (hi <= 0.0 || lo >= H);
      }
      double px = pts[n - 1].x, py = pts[n - 1].y;
      for (int i = 0; i < n; i++) {
        double qx = pts[i].x, qy = pts[i].y;
        if (qy != py && qy == qy && py == py) {
          Edge e;
          if (py < qy) e = {py, qy, px, (qx - px) / (qy - py), 1};
          else e = {qy, py, qx, (px - qx) / (py - qy), -1};
          if (e.y0 < ymin) ymin = e.y0;
          if (e.y1 > ymax) ymax = e.y1;
          count++;
          if (cull) {
            culled = true;
          } else {
            if (edges_.size() >= MAX_POINTS) {
              fr.raise(Fault::Memory, "a shape has too many points for the VM");
              return;
            }
            if (e.x0 != e.x0 || e.dx != e.dx) nan = true;
            edges_.push_back(e);
          }
        }
        px = qx;
        py = qy;
      }
    }
    if (!(culled && nan)) break;
  }
  if (count == 0) return;
  if (!finite_or_raise(ymin, fr) || !finite_or_raise(ymax, fr)) return;
  double ys = std::floor(ymin), ye = std::ceil(ymax);
  ys = ys > 0 ? ys : 0;
  ye = ye < H ? ye : H;
  if (ys >= ye) return;
  const int ystart = (int)ys, yend = (int)ye;
  esort_.sort(edges_.data(), (int)edges_.size(), EdgeLt());
  cost += count + (uint64_t)(yend - ystart) * SUB;
  const double af = a / 255.0;
  active_.clear();
  size_t k = 0;
  const size_t ne = edges_.size();
  for (int y = ystart; y < yend; y++) {
    const double ytop = y, ybot = y + 1.0;
    while (k < ne && edges_[k].y0 < ybot) active_.push_back(edges_[k++]);
    if (!active_.empty()) {
      size_t j = 0;
      for (size_t i = 0; i < active_.size(); i++)
        if (active_[i].y1 > ytop) active_[j++] = active_[i];
      active_.resize(j);
    }
    if (active_.empty()) {
      if (k >= ne) break;
      continue;
    }
    if (painter && !painter->row_ok(y)) {
      fr.raise(Fault::Runtime, "arithmetic error (OverflowError)");
      return;
    }
    if (!aa || painter) {
      std::vector<Span> &sp = sub_[0];
      spans(y + 0.5, sp);
      for (const Span &s : sp) {
        double ca = s.a - 0.5, cb = s.b - 0.5;
        if (!finite_or_raise(ca, fr) || !finite_or_raise(cb, fr)) return;
        ca = std::ceil(ca);
        cb = std::ceil(cb);
        int ia = ca > 0 ? (ca > W ? W + 1 : (int)ca) : 0;
        int ib = cb < W ? (cb < -1 ? -1 : (int)cb) : W;
        if (ib <= ia) continue;
        if (painter) {
          painter->span(y, ia, ib, buf + y * W);
          cost += (uint64_t)((ib - ia) >> 3);
        } else {
          span(y, ia, ib, g, a);
        }
      }
      continue;
    }
    // anti-aliased: SUB sub-rows, `diff` counts full sub-row covers, `part` the fractional ends
    for (int s = 0; s < SUB; s++) spans(ytop + (s + 0.5) / SUB, sub_[s]);
    double lo = W, hi = 0;
    for (int s = 0; s < SUB; s++) {
      const std::vector<Span> &sp = sub_[s];
      if (!sp.empty()) {
        if (sp[0].a < lo) lo = sp[0].a;
        if (sp.back().b > hi) hi = sp.back().b;
      }
    }
    if (hi <= lo) continue;
    if (!finite_or_raise(lo, fr) || !finite_or_raise(hi, fr)) return;
    double tl = std::trunc(lo), th = std::ceil(hi);
    int c0 = tl > 0 ? (tl > W ? W + 1 : (int)tl) : 0;
    int c1 = th < W ? (th < -1 ? -1 : (int)th) : W;
    if (c1 <= c0) continue;
    const int width = c1 - c0;
    for (int i = 0; i <= width; i++) diff_[i] = 0;
    for (int i = 0; i < width; i++) part_[i] = 0.0;
    // Python list indexing: a negative index counts from the end, past the end raises
    auto pidx = [&](int i, int len, int *out) {
      if (i < 0) i += len;
      if (i < 0 || i >= len) {
        fr.raise(Fault::Runtime, ERR_INDEX);
        return false;
      }
      *out = i;
      return true;
    };
    for (int s = 0; s < SUB; s++) {
      for (Span sp : sub_[s]) {
        double xa = sp.a, xb = sp.b;
        if (xa < 0.0) xa = 0.0;
        if (xb > W) xb = W;
        if (xb <= xa) continue;
        int fa = (int)xa, fb = (int)xb, j = 0;
        if (fa == fb) {
          if (!pidx(fa - c0, width, &j)) return;
          part_[j] += (xb - xa) * 0.25;
          continue;
        }
        int ca = (int)std::ceil(xa);
        if (ca > fa) {
          if (!pidx(fa - c0, width, &j)) return;
          part_[j] += (ca - xa) * 0.25;
        }
        if (fb > ca) {
          if (!pidx(ca - c0, width + 1, &j)) return;
          diff_[j] += 1;
          if (!pidx(fb - c0, width + 1, &j)) return;
          diff_[j] -= 1;
        }
        if (xb > fb && fb < W) {
          if (!pidx(fb - c0, width, &j)) return;
          part_[j] += (xb - fb) * 0.25;
        }
      }
    }
    const int base = y * W;
    int run = 0, full_start = -1;
    for (int x = 0; x < width; x++) {
      run += diff_[x];
      if (run == SUB && part_[x] == 0.0) {
        if (full_start < 0) full_start = x;
        continue;
      }
      if (full_start >= 0) {
        span(y, c0 + full_start, c0 + x, g, a);
        full_start = -1;
      }
      double cov = run * 0.25 + part_[x];
      if (cov > 0.0) {
        if (cov > 1.0) cov = 1.0;
        int i = base + c0 + x;
        double f = cov * af;
        if (blend != 1) {
          buf[i] = (uint8_t)blend_grey(blend, buf[i], g, (int)(f * 255 + 0.5));
        } else if (f >= 0.999) {
          buf[i] = (uint8_t)g;
        } else {
          int v = buf[i];
          buf[i] = (uint8_t)(int)(v + (g - v) * f + 0.5);
        }
      }
    }
    if (full_start >= 0) span(y, c0 + full_start, c0 + width, g, a);
    cost += (uint64_t)(width >> 1);
  }
}

// ---------------------------------------------------------------------- geometry helpers
void ellipse_points(double cx, double cy, double rx, double ry, int n, Contours &out) {
  const double a0 = 0.0, a1 = kTWO_PI;
  for (int i = 0; i < n; i++) {
    double t = a0 + (a1 - a0) * i / n;
    out.add(cx + rx * py_cos(t), cy + ry * py_sin(t));
  }
}

int segments_for(double r_device, double sweep, FaultRef fr) {
  double v = std::fabs(sweep) * pymax(r_device, 0.5) / 1.2;
  if (!finite_or_raise(v, fr)) return 0;
  double n = std::ceil(v);
  double full = pymax(8.0, pymin(160.0, n));
  if (sweep < kTWO_PI - 1e-9) {
    double m = full * std::fabs(sweep) / kTWO_PI;
    if (!finite_or_raise(m, fr)) return 0;
    return (int)pymax(2.0, std::ceil(m));
  }
  return (int)full;
}

void reverse_last(Contours &c) {
  int i = c.count() - 1;
  Pt *p = c.at(i);
  uint32_t n = c.size(i);
  for (uint32_t a = 0, b = n ? n - 1 : 0; a < b; a++, b--) {
    Pt t = p[a];
    p[a] = p[b];
    p[b] = t;
  }
}

void ccw_last(Contours &c) {
  int ci = c.count() - 1;
  const Pt *p = c.at(ci);
  int n = (int)c.size(ci);
  double s = 0.0;
  for (int i = 0; i < n; i++) {
    const Pt &a = p[i], &b = p[(i + 1) % n];
    s += a.x * b.y - b.x * a.y;
  }
  if (!(s >= 0)) reverse_last(c);
}

void ring(const Pt *outer, int no, const Pt *inner, int ni, Contours &out) {
  out.clear();
  for (int i = 0; i < no; i++) out.add(outer[i].x, outer[i].y);
  out.close();
  ccw_last(out);
  for (int i = 0; i < ni; i++) out.add(inner[i].x, inner[i].y);
  out.close();
  ccw_last(out);
  reverse_last(out);
}

static void cap_pts(double x, double y, double ux, double uy, double hw, int n, Contours &out) {
  double base = std::atan2(uy, ux);
  for (int i = 0; i <= n; i++) {
    double t = base - kPI / 2 + kPI * i / n;
    out.add(x + hw * py_cos(t), y + hw * py_sin(t));
  }
}

void Stroker::polyline(const Pt *pts, int np, double w, bool closed, int cap, int join, double scale,
                       Contours &out, FaultRef fr) {
  out.clear();
  double hw = w / 2.0;
  if (hw <= 0) return;
  clean_.clear();
  for (int i = 0; i < np; i++) {
    const Pt &p = pts[i];
    if (clean_.empty() || std::fabs(p.x - clean_.back().x) > 1e-9 || std::fabs(p.y - clean_.back().y) > 1e-9)
      clean_.push_back(p);
  }
  if (closed && clean_.size() > 2 && std::fabs(clean_[0].x - clean_.back().x) < 1e-9 &&
      std::fabs(clean_[0].y - clean_.back().y) < 1e-9)
    clean_.pop_back();
  double nr = hw * scale * 2.5;
  if (!finite_or_raise(nr, fr)) return;
  const int nround = (int)pymax(4.0, pymin(32.0, std::ceil(nr)));
  if (clean_.size() == 1) {
    double x = clean_[0].x, y = clean_[0].y;
    if (cap == ROUND) {
      ellipse_points(x, y, hw, hw, 2 * nround, out);
      out.close();
    } else if (cap == PROJECT) {
      out.add(x - hw, y - hw);
      out.add(x + hw, y - hw);
      out.add(x + hw, y + hw);
      out.add(x - hw, y + hw);
      out.close();
    }
    return;
  }
  const int n = (int)clean_.size();
  const int segs = closed ? n : n - 1;
  dirs_.clear();
  for (int i = 0; i < segs; i++) {
    const Pt &a = clean_[i], &b = clean_[(i + 1) % n];
    double dx = b.x - a.x, dy = b.y - a.y;
    double ln = py_hypot(dx, dy);
    if (ln > 0) dirs_.push_back({dx / ln, dy / ln});
    else dirs_.push_back({1.0, 0.0});
  }
  for (int i = 0; i < segs; i++) {
    const Pt &a = clean_[i], &b = clean_[(i + 1) % n];
    double ux = dirs_[i].x, uy = dirs_[i].y;
    double nx = -uy * hw, ny = ux * hw;
    double ex0 = a.x, ey0 = a.y, ex1 = b.x, ey1 = b.y;
    if (!closed && cap == PROJECT) {
      if (i == 0) {
        ex0 = a.x - ux * hw;
        ey0 = a.y - uy * hw;
      }
      if (i == segs - 1) {
        ex1 = b.x + ux * hw;
        ey1 = b.y + uy * hw;
      }
    }
    out.add(ex0 + nx, ey0 + ny);
    out.add(ex1 + nx, ey1 + ny);
    out.add(ex1 - nx, ey1 - ny);
    out.add(ex0 - nx, ey0 - ny);
    out.close();
    ccw_last(out);
  }
  const int njoin = closed ? n : n - 2;
  for (int j = 0; j < njoin; j++) {
    int vi = closed ? j : (j + 1) % n;
    int i_in = closed ? (int)floormod(vi - 1, segs) : j;
    int i_out = closed ? vi % segs : j + 1;
    double ax = dirs_[i_in].x, ay = dirs_[i_in].y, bx = dirs_[i_out].x, by = dirs_[i_out].y;
    double cross = ax * by - ay * bx;
    if (std::fabs(cross) < 1e-9 && ax * bx + ay * by > 0) continue;
    double vx = clean_[vi].x, vy = clean_[vi].y;
    if (join == ROUND) {
      ellipse_points(vx, vy, hw, hw, 2 * nround, out);
      out.close();
      continue;
    }
    double s = cross > 0 ? 1.0 : -1.0;
    out.add(vx, vy);
    out.add(vx + ay * hw * s, vy - ax * hw * s);
    if (join == MITER) {
      double cos_t = ax * bx + ay * by;
      double half = std::sqrt(pymax(0.0, (1.0 + cos_t) / 2.0));
      if (half > 1e-6 && 1.0 / half <= MITER_LIMIT) {
        double mx = (ay * s + by * s), my = (-ax * s - bx * s);
        double ml = py_hypot(mx, my);
        if (ml > 1e-9) {
          double d = hw / half;
          out.add(vx + mx / ml * d, vy + my / ml * d);
        }
      }
    }
    out.add(vx + by * hw * s, vy - bx * hw * s);
    out.close();
    ccw_last(out);
  }
  if (!closed && cap == ROUND) {
    cap_pts(clean_[0].x, clean_[0].y, -dirs_[0].x, -dirs_[0].y, hw, nround, out);
    out.close();
    ccw_last(out);
    cap_pts(clean_.back().x, clean_.back().y, dirs_.back().x, dirs_.back().y, hw, nround, out);
    out.close();
    ccw_last(out);
  }
}

}  // namespace pde
