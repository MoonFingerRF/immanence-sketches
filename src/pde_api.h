// Processing's drawing API on the 240x128 grey canvas -- a bit-exact port of the reference
// python/immanence/pde/runtime.py (class Api) for the bytecode VM.
//
// Conventions (the VM relies on them):
//  * Numbers arrive as double (a Python int is exact in a double); optional trailing arguments
//    as (bool has_x, double x) or (const double *a, int n); strings as UTF-16 code units.
//  * A Python exception inside Api becomes `fault` (first one wins) plus a harmless return
//    value; the VM checks `fault` after every call. Nothing here throws, and no input (NaN, inf,
//    1e300) is undefined behaviour.
//  * `charged` accumulates the steps Python charges (budget.charge / _cost / allocate) inside
//    these calls, exactly; the VM adds it to its own step count.
//  * Python raises budget/deadline errors from inside a call once the frame is over its limit;
//    here the call finishes and the VM notices afterwards (the frame is lost either way).
//
// Build with -O2 -ffp-contract=off and never -ffast-math (see pde_pyops.h): the arithmetic is
// the Python's, operation for operation, in double where it is double and float where it is f32.
// libm: sin/cos/tan/atan2/log/pow come from the platform, as math.* does in the reference, so a last-ulp
// difference between the host's libm and the device's newlib can in rare cases move a pixel.
#pragma once
#include <cstdint>
#include <vector>

#include "pde_pyops.h"
#include "pde_random.h"
#include "pde_raster.h"
#include "pde_textures.h"
#include "pde_three.h"

namespace pde {

int luma(int32_t argb);

struct Style {
  bool fill = true;
  int fill_g = 255, fill_a = 255;
  int32_t fill_c = -1;
  bool stroke = true;
  int stroke_g = 0, stroke_a = 255;
  int32_t stroke_c = -16777216;
  double sw = 1.0;
  int cap = ROUND, join = MITER;
  int64_t rect_mode = 0, ellipse_mode = 3;
  double text_size = 12.0;
  int64_t align_x = 37, align_y = 0;
  bool has_leading = false;
  double leading = 0.0;
  int cmode = 1;
  double cmax[4] = {255.0, 255.0, 255.0, 255.0};
  int tex = TEX_NONE, tex_p = 6, tex_g2 = 255;     // the texture extension
  int stex = TEX_NONE, stex_p = 6, stex_g2 = 255;
  // P3D materials: ambient follows the fill until ambient(); blendMode(); imageMode()
  bool amb_set = false;
  int32_t amb_c = 0, spec_c = -8553091, emis_c = -16777216;
  double shine = 1.0;
  int blend = 1;
  int64_t image_mode = 0;
};

class Api {
 public:
  Api();                       // == runtime.Api(): canvas 204, crisp (aa = false), rand seeded 0
  ~Api();
  Api(const Api &) = delete;
  Api &operator=(const Api &) = delete;

  Fault fault = Fault::None;
  const char *fault_msg = "";
  uint64_t charged = 0;
  int frame_count = 0;
  bool looping = true, redraw_ = false, exited = false;
  Randomness rand;
  Canvas canvas;
  int last_rseed = 0;          // low bits of the last randomSeed() (textures use & 0xFFFF)
  bool has_tex_seed = false;
  int tex_seed = 0;
  // an ellipse outline under noSmooth costs at least one step per point: past this many points
  // Python would fail the frame's step budget anyway, so the runtime stops with Budget first
  uint64_t work_cap = 4000000;
  static constexpr int64_t THIN_SEG_MAX = 4 * (W + H);         // runtime.py THIN_SEG_MAX
  static constexpr double ARC_FOLD = 64 * 2 * 3.141592653589793; // runtime.py ARC_FOLD (64 * 2 * math.pi)

  // clock: localtime(now_s + tz_s); what = 0..5: second, minute, hour, day, month, year
  int64_t now_s = 0;
  int32_t tz_s = 0;
  int clock(int what);

  // ---- structure
  void size(double w, double h, bool p3d = false);
  void no_loop() { looping = false; }
  void loop() { looping = true; }
  void do_redraw() { redraw_ = true; }
  void exit() {
    exited = true;
    looping = false;
  }
  void smooth() { canvas.aa = true; }
  void no_smooth() { canvas.aa = false; }
  int millis();
  void reset_shape();          // Sketch.next_frame's `api.shape = None`

  // ---- colour
  void color_mode(double mode, const double *maxes, int n);
  int32_t color(const double *a, int n, const bool *ints);
  void fill(const double *a, int n, const bool *ints);
  void no_fill() { st_.fill = false; }
  void stroke(const double *a, int n, const bool *ints);
  void no_stroke() { st_.stroke = false; }
  void background(const double *a, int n, const bool *ints);
  void clear() {
    canvas.clear(255);
    if (t3) t3->clear_depth();
  }
  void stroke_weight(double w) { st_.sw = pymax(0.0, w); }
  void stroke_cap(double c);
  void stroke_join(double j);
  void rect_mode(double m);
  void ellipse_mode(double m);
  double red(double c);
  double green(double c);
  double blue(double c);
  double alpha(double c);
  double brightness(double c);
  double hue(double c);          // builtins.h_hue_sat
  double saturation(double c);
  int32_t lerp_color(double c1, double c2, double t);

  // ---- matrix
  void push_matrix();
  void pop_matrix();
  void push_style();
  void pop_style();
  void push() {
    push_matrix();
    if (fault == Fault::None) push_style();
  }
  void pop() {
    pop_matrix();
    if (fault == Fault::None) pop_style();
  }
  void reset_matrix();
  void translate(double x, double y, double z = 0.0);
  void rotate(double t);
  void scale(double sx, bool has_sy, double sy, bool has_sz = false, double sz = 0.0);
  void shear_x(double t);
  void shear_y(double t);
  double screen_x(double x, double y, bool has_z = false, double z = 0.0);
  double screen_y(double x, double y, bool has_z = false, double z = 0.0);

  // ---- P3D (three.py; runtime.Api's 3D half)
  bool p3d = false;
  Three *t3 = nullptr;
  void begin_frame3d() {
    if (t3) t3->begin_frame();
  }
  void rotate_x(double t);
  void rotate_y(double t);
  void rotate_axis(double t, double x, double y, double z);
  void apply_matrix(const double *v, int n);
  void camera(const double *v, int n);
  void perspective(const double *v, int n);
  void ortho(const double *v, int n);
  void frustum(const double *v);
  double screen_z(double x, double y, double z);
  void lights();
  void no_lights();
  void ambient_light(const double *v, int n);
  void directional_light(const double *v);
  void point_light(const double *v);
  void spot_light(const double *v);
  void light_falloff(double c, double l, double q);
  void light_specular(double r, double g, double b);
  void ambient(const double *a, int n, const bool *ints);
  void specular(const double *a, int n, const bool *ints);
  void emissive(const double *a, int n, const bool *ints);
  void shininess(double s);
  void normal(double x, double y, double z);
  void hint(double which);
  void box(double w, bool has_hd, double h, double d);
  void sphere_detail(double u, bool has_v, double v);
  void sphere(double r);
  void text_z(const uint16_t *s, int n, double x, double y, double z);
  void point3(double x, double y, double z);
  void point_z(double x, double y, bool has_z, double z);   // point(x, y[, z]) as runtime.Api.point
  void line3(double x1, double y1, double z1, double x2, double y2, double z2);
  void vertex3(double x, double y, bool has_z, double z);
  void curve_vertex3(double x, double y, double z);
  // ---- blendMode(), filter()
  void blend_mode(double m);
  void filter(double kind, bool has_param, double param);

  // what three.cpp reaches in here
  void fault_raise(Fault k, const char *msg) { raise(k, msg); }
  bool ok_pub() const { return ok(); }
  const Painter &painter_ref() const { return painter_; }
  Contours &scratch_dev() { return dev3_; }
  bool make_painter_pub(int tex, int p, int g, int g2, const Contours *dev) {
    return make_painter(tex, p, g, g2, dev);
  }

  // ---- textures (an extension to Processing)
  void texture(const uint16_t *name, int n, bool has_p, double p);
  void no_texture() { st_.tex = TEX_NONE; }
  void stroke_texture(const uint16_t *name, int n, bool has_p, double p);
  void no_stroke_texture() { st_.stex = TEX_NONE; }
  void texture_seed(double n);
  void background_texture(const double *nums, int n, const bool *ints, const uint16_t *name, int nn,
                          bool has_extra, double extra);

  // ---- shapes
  void point(double x, double y);
  void line(double x1, double y1, double x2, double y2);
  void rect(double a, double b, double c, double d, const double *radii = nullptr, int nr = 0);
  void square(double x, double y, double e) { rect(x, y, e, e); }
  void ellipse(double a, double b, double c, double d);
  void circle(double x, double y, double d) { ellipse(x, y, d, d); }
  void arc(double a, double b, double c, double d, double start, double stop, bool has_mode, double mode);
  void triangle(double x1, double y1, double x2, double y2, double x3, double y3);
  void quad(double x1, double y1, double x2, double y2, double x3, double y3, double x4, double y4);
  void bezier(double x1, double y1, double x2, double y2, double x3, double y3, double x4, double y4);
  void curve(double x1, double y1, double x2, double y2, double x3, double y3, double x4, double y4);
  void curve_tightness(double t) { curve_tightness_ = t; }
  static double bezier_point(double a, double b, double c, double d, double t);
  static double bezier_tangent(double a, double b, double c, double d, double t);
  static double curve_point(double a, double b, double c, double d, double t);
  void begin_shape(bool has_kind, double kind);
  void vertex(double x, double y);
  void bezier_vertex(const double *a, int n);       // 6 or 9 numbers
  void quadratic_vertex(const double *a, int n);    // 4 or 6 numbers
  void curve_vertex(double x, double y);
  void begin_contour();
  void end_contour();
  void end_shape(bool has_mode, double mode);

  // ---- text
  void text_size(double s);
  void text_align(double ax, bool has_ay, double ay);
  void text_leading(double v) {
    st_.has_leading = true;
    st_.leading = v;
  }
  void text_font(double size) { text_size(size); }    // Api.text_font(PFont(size)) / (font, size)
  double text_ascent();
  double text_descent();
  double text_width(const uint16_t *s, int n);
  void text(const uint16_t *s, int n, double x, double y);
  void text_box(const uint16_t *s, int n, double x, double y, double w, double h);

  // ---- pixels (the pixels[] array lives in the VM)
  // loadPixels(): fills out[W*H]; `first` = the sketch had no pixels[] yet (Python allocates)
  void load_pixels(int32_t *out, bool first);
  void update_pixels(const int32_t *px);
  int32_t get(double x, double y);
  void set(double x, double y, double c);

  // ---- randomness (builtins call api.rand; these are the same calls)
  double random(double high) { return rand.random(high, fr()); }
  double random2(double lo, double hi) { return rand.random2(lo, hi, fr()); }
  double random_gaussian() { return rand.gaussian(); }
  double math_random() { return rand.next_double(); }
  double noise(double x, double y, double z);          // charges 4 like builtins.h_noise
  void random_seed(double s);
  void noise_seed(double s);
  void noise_detail(double lod, bool has_falloff, double falloff);

  const Style &style() const { return st_; }
  const double *matrix() const { return m_; }

 private:
  FaultRef fr() { return FaultRef{&fault, &fault_msg}; }
  void raise(Fault k, const char *msg) { fr().raise(k, msg); }
  bool ok() const { return fault == Fault::None; }
  bool to_int(double v, int64_t *out);                  // Python int() of a float
  bool to_floor(double v, double *out);                 // math.floor, as an exact double
  void cost_() {
    charged += canvas.cost;
    canvas.cost = 0;
  }

  int32_t calc(const double *a, int n, const bool *ints);
  int32_t grey(double g, double a);
  int32_t three(double x, double y, double z, double a);
  int tex_name(const uint16_t *name, int n);
  void set_texture(bool stroke, const uint16_t *name, int n, bool has_p, double p);
  int clamp_p(int tex, bool has_p, double p);

  double mscale();
  bool axis() const { return m_[1] == 0.0 && m_[2] == 0.0; }
  bool thin() { return !canvas.aa && st_.sw * mscale() <= 1.0; }
  void xf(const Contours &in, Contours &out);
  bool inverse(double inv[6]);
  bool make_painter(int tex, int p, int g, int g2, const Contours *dev);
  void crisp(Contours &dev, bool rect);
  void fill_contours(const Contours &c, bool rect = false);
  void stroke_contours(const Contours &c);
  void thin_path(const Pt *pts, int n, bool closed);
  void stroke_path(const Pt *pts, int n, bool closed, const uint8_t *anchors = nullptr);
  const Pt *normalize(const Pt *pts, int n, const uint8_t *anchors);
  void one(const Pt *pts, int n, Contours &out);
  void rect_box(double a, double b, double c, double d, int64_t mode, double *x0, double *y0, double *x1, double *y1);
  void round_rect(double x0, double y0, double x1, double y1, const double *radii, int nr);
  void ellipse_box(double a, double b, double c, double d, int64_t mode, double *cx, double *cy, double *rx,
                   double *ry);
  void thin_ellipse(double cx, double cy, double rx, double ry, double sc);
  bool bez_pts(double x0, double y0, double x1, double y1, double x2, double y2, double x3, double y3,
               std::vector<Pt> &out);
  bool curve_seg(const Pt &p0, const Pt &p1, const Pt &p2, const Pt &p3, std::vector<Pt> &out);
  bool need_shape();
  bool flatten(size_t from, size_t to, std::vector<Pt> &pts, std::vector<uint8_t> &anchors);
  void poly(const Pt *pts, int n);
  double leading_();
  double width_of(const uint16_t *s, int a, int b);
  bool glyphs(const uint16_t *s, int a, int b, double x, double y, Contours &out);
  void text_paint(const Contours &c);

  double m_[6] = {1.0, 0.0, 0.0, 1.0, 0.0, 0.0};
  double mstack_[32][6];
  // P3D: the z scale of the flat state, and the 4x4 once a transform is 3D (runtime.Api.m3)
  double mz_ = 1.0;
  bool has_m3_ = false;
  double m3_[16];
  double mzstack_[32];
  bool m3has_stack_[32];
  double m3stack_[32][16];
  Contours dev3_;
  Mesh mesh_;
  std::vector<V3> v3_;
  void embed(double *out) const;
  void apply3(const double *t);
  void mv(double *out) const;
  bool flat3() const;
  bool use3d() const;
  bool need3d(const char *what);
  void fill3(const Contours &c, bool lit);
  void stroke3(const V3 *pts, int n, bool closed);
  void light_pos(double x, double y, double z, double *out);
  void light_dir(double dx, double dy, double dz, double *out);
  bool rgbf(double v1, double v2, double v3, double *out);
  bool shape_has_z() const;
  bool bez3(const V3 &p0, const V3 &p1, const V3 &p2, const V3 &p3, std::vector<V3> &out);
  void end_shape3d(bool close);
  int nm_ = 0;
  Style st_;
  Style sstack_[64];
  int ns_ = 0;
  double curve_tightness_ = 0.0;

  // beginShape state: vertices ('v', 'b', 'q', 'c') and the contours closed so far
  struct Item {
    char k;
    double v[6];
    double z[3] = {0.0, 0.0, 0.0};   // P3D: vertex/curve z, bezier z2..z4, quadratic z2, z3
    double n[3] = {0.0, 0.0, 1.0};   // P3D: the normal() current at this vertex
    bool zset = false;               // P3D bezier/quadratic carry their z's
  };
  struct SavedContour {
    bool hole;
    uint32_t a, b;             // range in ctr_pts_ / ctr_anc_
    uint32_t ia = 0, ib = 0;   // P3D: the raw items, range in ctr_items_
  };
  std::vector<Item> ctr_items_;
  bool shape_normals_ = false;
  bool flatten3(const std::vector<Item> &src, size_t from, size_t to, std::vector<V3> &pts, std::vector<V3> &nrm);
  bool shape_open_ = false;
  int64_t shape_kind_ = 20;
  std::vector<Item> items_;
  std::vector<SavedContour> ctrs_;
  std::vector<Pt> ctr_pts_;
  std::vector<uint8_t> ctr_anc_;

  // scratch (reused; cleared per use)
  Painter painter_;
  bool has_painter_ = false;
  Stroker stroker_;
  Contours poly_, dev_, stroke_, ring_;
  std::vector<Pt> norm_, pts_, pts2_, curve_, kpts_, kind_pts_;
  std::vector<uint8_t> anc_, off_known_;
  std::vector<Pt> off_;
  std::vector<double> ecoef_;      // thin ellipse Bernstein weights for ecoef_seg_
  int64_t ecoef_seg_ = -1;
  std::vector<std::pair<int, int>> lines_;
};

}  // namespace pde
