// SPDX-License-Identifier: LGPL-2.1-only
// Follows Processing's own source (PGraphicsOpenGL, InGeometry and its shaders, Processing 4.5.2,
// GNU LGPL 2.1) through python/immanence/pde/three.py, to draw 3D exactly as Processing does.
// See THIRD-PARTY.md.
// P3D in the VM: a bit-exact port of the reference interpreter's python/immanence/pde/three.py (see that file for
// the why of every rule: Processing's P3D camera and projection, near-plane clipping, pixel-centre
// sampling, a float32 depth buffer with <=, strokes pulled toward the eye, LightVert.glsl lighting,
// box() and sphere() as InGeometry.addBox / addSphere).
//
// Same double arithmetic in the same order as the Python; float32 exactly where Python stores into
// array('f') or calls f32(). Build with -O2 -ffp-contract=off, never -ffast-math (pde_pyops.h).
#pragma once
#include <cstdint>
#include <vector>

#include "pde_pyops.h"
#include "pde_raster.h"

namespace pde {

class Api;

struct V3 {
  double x, y, z;
};

// Faces: each face is a list of contours (the first the outline, the rest holes), each contour a
// list of vertex indices. Flattened: idx[] per contour ranges `cend`, contours per face `fend`.
struct Mesh {
  std::vector<V3> verts;
  std::vector<V3> norms;           // one per vertex, or empty (then face_norms, one per face)
  std::vector<V3> face_norms;
  std::vector<uint32_t> idx, cend, fend;
  std::vector<uint32_t> edges;     // pairs
  void clear() {
    verts.clear();
    norms.clear();
    face_norms.clear();
    idx.clear();
    cend.clear();
    fend.clear();
    edges.clear();
  }
  void contour_end() { cend.push_back((uint32_t)idx.size()); }
  void face_end() { fend.push_back((uint32_t)cend.size()); }
  void edge(uint32_t a, uint32_t b) {
    edges.push_back(a);
    edges.push_back(b);
  }
};

struct Light {
  double pos[3];
  double w;                        // 0: directional, 1: positional
  double nrm[3], amb[3], dif[3], spec[3], fall[3], spot[2];
};

// three.mmul / look_at / frustum_m / ortho_m / perspective_m / inv3 (row-major 4x4)
void mmul4(const double *a, const double *b, double *out);
void look_at(double ex, double ey, double ez, double cx, double cy, double cz, double ux, double uy, double uz,
             double *m);
bool frustum_m(double l, double r, double b, double t, double n, double f, double *m, FaultRef fr);
bool ortho_m(double l, double r, double b, double t, double n, double f, double *m, FaultRef fr);
bool perspective_m(double fov, double aspect, double n, double f, double *m, FaultRef fr);
bool inv3(const double *m, double *out);
void default_camera(double *m);
void default_projection(double *m);
extern const double CAM_Z;

class Three {
 public:
  explicit Three(Api *api);
  Api *api;
  double cam[16], proj[16];
  bool cam_default = true, proj_default = true, ortho = false;
  double eye_dist;
  std::vector<float> depth;
  bool dirty = false, depth_test = true, depth_mask = true, lighting = false;
  Light lights[8];
  int nlights = 0;
  double fall[3] = {1.0, 0.0, 0.0}, lspec[3] = {0.0, 0.0, 0.0}, normal[3] = {0.0, 0.0, 1.0};
  int su = 0, sv = 0;
  std::vector<float> stx, sty, stz;

  void begin_frame();
  void clear_depth();
  void set_camera(const double *m);
  void set_projection(const double *m, bool is_ortho);
  bool add_light(const double *pos, double w, const double *nrm, const double *amb, const double *dif,
                 const double *spec, const double *fall, const double *spot);
  void sphere_tables(int ures, int vres);

  // Material of the current style: fill, ambient, specular, emissive (rgb 0..1) and shininess
  struct Mat {
    double fill[3], amb[3], spec[3], emis[3], shine;
  };
  void mesh(const double *mv, const Mesh &m, bool lit, bool fill, bool stroke, bool text);
  void points(const double *mv, const V3 *v, int n);
  void screen(const double *mv, double x, double y, double z, double *sx, double *sy, double *sz);

 private:
  struct SP {                      // a screen point (x, y, depth) and a grey (Gouraud)
    double x, y, z, g;
  };
  struct Poly {                    // screen contours
    std::vector<SP> pts;
    std::vector<uint32_t> ends;
    void clear() {
      pts.clear();
      ends.clear();
    }
  };
  struct Col {
    bool have = false;
    double g, rgb[3];
  };
  void shade(double ex, double ey, double ez, double nx, double ny, double nz, const Mat &mat, double *rgb);
  static double grey_f(const double *rgb);
  static int grey_i(const double *rgb);
  bool plane(const Poly &p, int k, double *a, double *b, double *c);
  void raster(const Poly &p, const double *zp, int g, const double *gp, int a, bool painter);
  void fill_faces(const double *mv, const Mesh &m, bool lit, bool textured);
  void paint(const Poly &sc, int g, int a, bool painter_on);
  void gouraud(const SP *pts, int n, int a, bool painter_on);
  bool painter_for(const Poly &sc, int g);
  void stroke_edges(const double *mv, const Mesh &m);
  void line_px(const SP &p, const SP &q, int g, int a, double sw);
  void point_px(const SP &p, int g, int a, double sw, int cap, double ts);
  bool colour(const Mesh &m, uint32_t i, bool front, const V3 *fn, Col *out);

  // scratch
  Mat mat_;
  const double *mv_ = nullptr;
  double ni_[9];
  bool has_ni_ = false;
  std::vector<double> clip_;       // 4 per vertex
  std::vector<SP> scr_;
  std::vector<uint8_t> scr_ok_;
  std::vector<Col> colf_, colb_;
  std::vector<double> bclip_;
  std::vector<SP> bscr_;
  std::vector<uint8_t> bok_, used_;
  Poly sc_, tri_, quad_;
  std::vector<double> cpoly_, cpoly2_;
  struct Edge {
    double y0, y1, x0, dx;
    int d;
  };
  struct Xd {
    double x;
    int d;
  };
  struct XdLt {
    bool operator()(const Xd &a, const Xd &b) const {
      if (!(a.x == b.x)) return a.x < b.x;
      return a.d < b.d;
    }
  };
  std::vector<Edge> edges_;
  std::vector<Xd> xs_;
  std::vector<double> spa_, spb_;
  PySort<Xd, XdLt> xsort_;
};

}  // namespace pde
