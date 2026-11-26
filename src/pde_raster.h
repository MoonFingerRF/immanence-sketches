// The grey canvas and the one way anything gets onto it: filled contours, non-zero winding,
// bit for bit with python/immanence/pde/raster.py (see that file for the why of every rule).
// Build with -O2 -ffp-contract=off and without -ffast-math (see pde_pyops.h).
#pragma once
#include <cstdint>
#include <vector>

#include "pde_pyops.h"
#include "pde_textures.h"

namespace pde {

constexpr int SUB = 4;                 // sub-rows per pixel row (anti-aliased fills)
constexpr int ROUND = 2, SQUARE = 1, PROJECT = 4, MITER = 8, BEVEL = 32;
constexpr double MITER_LIMIT = 10.0;
constexpr double kPI = 3.141592653589793;
// Memory guard: points of one fill / one flattened outline. Python has no such limit (it would
// just be slow); past it the runtime reports a Memory fault instead of allocating more.
constexpr size_t MAX_POINTS = 1u << 18;

struct Pt {
  double x, y;
};

// A list of contours: flat points, contour i is [start(i), ends[i]).
struct Contours {
  std::vector<Pt> pts;
  std::vector<uint32_t> ends;
  void clear() {
    pts.clear();
    ends.clear();
  }
  void add(double x, double y) { pts.push_back({x, y}); }
  void close() { ends.push_back((uint32_t)pts.size()); }   // ends the contour being added
  int count() const { return (int)ends.size(); }
  uint32_t start(int i) const { return i ? ends[i - 1] : 0; }
  uint32_t size(int i) const { return ends[i] - start(i); }
  const Pt *at(int i) const { return pts.data() + start(i); }
  Pt *at(int i) { return pts.data() + start(i); }
  bool empty() const { return ends.empty(); }
};

class Canvas {
 public:
  explicit Canvas(int grey = 204);
  uint8_t buf[W * H];
  bool aa = true;
  int blend = 1;             // blendMode(): 1 = BLEND (raster.blend_grey for the others)
  uint64_t cost = 0;         // work in "pixel touches", read and reset by the Api (Python's cost)
  FaultRef fr;

  void clear(int g, int a = 255);
  void span(int y, int x0, int x1, int g, int a);
  void pixel(int i, int g, double cov);
  void thin_line(double x0, double y0, double x1, double y1, int g, int a, const Painter *painter);
  // the same with the end points already rounded: floor(v + 0.5) as exact doubles (round_half)
  void thin_line_r(double ax, double ay, double bx, double by, int g, int a, const Painter *painter);
  bool round_half(double v, double *out);   // floor(v + 0.5); NaN/inf raise
  void fill_rect(double x0, double y0, double x1, double y1, int g, int a);
  void fill(const Contours &contours, int g, int a, const Painter *painter);
  // Canvas.table(g, a): the blend of every grey with g at opacity a (under the blend mode)
  const uint8_t *table(int g, int a);

 private:
  struct Edge {
    double y0, y1, x0, dx;
    int d;
  };
  struct Xd {
    double x;
    int d;
  };
  struct Span {
    double a, b;
  };
  struct EdgeLt {
    bool operator()(const Edge &a, const Edge &b) const {
      if (!(a.y0 == b.y0)) return a.y0 < b.y0;
      if (!(a.y1 == b.y1)) return a.y1 < b.y1;
      if (!(a.x0 == b.x0)) return a.x0 < b.x0;
      if (!(a.dx == b.dx)) return a.dx < b.dx;
      return a.d < b.d;
    }
  };
  struct XdLt {
    bool operator()(const Xd &a, const Xd &b) const {
      if (!(a.x == b.x)) return a.x < b.x;
      return a.d < b.d;
    }
  };
  void spans(double sy, std::vector<Span> &out);
  int tab_g_ = -1, tab_a_ = -1, tab_m_ = 1;
  uint8_t tab_[256];
  std::vector<Edge> edges_, active_;
  std::vector<Xd> xs_;
  std::vector<Span> sub_[SUB];
  PySort<Edge, EdgeLt> esort_;
  PySort<Xd, XdLt> xsort_;
  int diff_[W + 1];
  double part_[W];
};

// ---------------------------------------------------------------------- geometry helpers
// raster.ellipse_points(cx, cy, rx, ry, n) appended to `out`'s open contour (n points)
void ellipse_points(double cx, double cy, double rx, double ry, int n, Contours &out);
// raster.segments_for; faults (math.ceil of NaN/inf) give 0 with fr raised
int segments_for(double r_device, double sweep, FaultRef fr);
constexpr double kTWO_PI = 2 * kPI;
// ccw() of the last contour of `c`, in place
void ccw_last(Contours &c);
void reverse_last(Contours &c);

class Stroker {
 public:
  // raster.stroke_polyline: contours (user space) appended to `out` (cleared first)
  void polyline(const Pt *pts, int n, double w, bool closed, int cap, int join, double scale, Contours &out,
                FaultRef fr);

 private:
  std::vector<Pt> clean_, dirs_;
};

// raster.blend_grey: one grey channel of PImage.blendColor(dst v, src g at alpha a)
int blend_grey(int mode, int v, int g, int a);

// raster.ring(outer, inner) -> out (cleared first): outer made CCW, inner CW
void ring(const Pt *outer, int no, const Pt *inner, int ni, Contours &out);

}  // namespace pde
