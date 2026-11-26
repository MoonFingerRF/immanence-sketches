#include "pde_fp.h"  // first: no fused multiply-add (see the header)
// The VM's natives: python/immanence/pde/builtins.py, call for call, over pde::Api (the port
// of runtime.py). Arguments arrive exactly as bcbuiltins.py pushed them: numbers for the drawing
// API as doubles, PApplet float maths as floats widened to double, strings/arrays as references.
#if defined(__clang__)
#pragma clang diagnostic ignored "-Wdeprecated-declarations"   // libc++: basic_string<uint16_t> (libstdc++ is fine)
#endif
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "pde_vm.h"

namespace pde {

// ---------------------------------------------------------------------------- number -> text
int jfloat_str(double v, bool single, char *out, size_t cap) {
  if (v != v) return snprintf(out, cap, "NaN");
  if (std::isinf(v)) return snprintf(out, cap, v > 0 ? "Infinity" : "-Infinity");
  if (v == 0) return snprintf(out, cap, std::signbit(v) ? "-0.0" : "0.0");
  int p = 0;
  const int top = single ? 9 : 17;
  char s[64];
  while (p < top) {
    snprintf(s, sizeof s, "%.*e", p, v);
    double back = strtod(s, nullptr);
    if ((single ? (double) (float) back : back) == v) break;
    p++;
  }
  snprintf(s, sizeof s, "%.*e", p, v);
  char *e = strchr(s, 'e');
  int ex = atoi(e + 1);
  *e = 0;
  const bool neg = s[0] == '-';
  char dg[48];
  int nd = 0;
  for (char *q = s + (neg ? 1 : 0); *q; q++)
    if (*q != '.') dg[nd++] = *q;
  while (nd > 1 && dg[nd - 1] == '0') nd--;
  if (nd == 1 && dg[0] == '0') nd = 1;
  std::string r = neg ? "-" : "";
  const double av = std::fabs(v);
  if (1e-3 <= av && av < 1e7) {
    if (ex >= 0) {
      for (int i = 0; i < ex + 1; i++) r += i < nd ? dg[i] : '0';
      r += '.';
      if (nd > ex + 1) r.append(dg + ex + 1, nd - ex - 1);
      else r += '0';
    } else {
      r += "0.";
      r.append((size_t) (-ex - 1), '0');
      r.append(dg, nd);
    }
  } else {
    r += dg[0];
    r += '.';
    if (nd > 1) r.append(dg + 1, nd - 1);
    else r += '0';
    r += 'E';
    r += std::to_string(ex);
  }
  return snprintf(out, cap, "%s", r.c_str());
}

int text_float(double v, char *out, size_t cap) {
  if (v != v) return snprintf(out, cap, "NaN");
  return snprintf(out, cap, "%s%.3f", v < 0 ? "-" : "", std::fabs(v));
}

// ---------------------------------------------------------------------------- Python str helpers
static bool py_isspace(uint16_t c) {
  return (c >= 9 && c <= 13) || (c >= 0x1C && c <= 0x20) || c == 0x85 || c == 0xA0 || c == 0x1680 ||
         (c >= 0x2000 && c <= 0x200A) || c == 0x2028 || c == 0x2029 || c == 0x202F || c == 0x205F || c == 0x3000;
}

using U16 = std::basic_string<uint16_t>;

static void py_upper(const uint16_t *s, uint32_t n, U16 &o) {
  o.clear();
  for (uint32_t i = 0; i < n; i++) {
    uint16_t c = s[i];
    if (c >= 'a' && c <= 'z') c -= 32;
    else if (c == 0xB5) c = 0x39C;
    else if (c == 0xDF) { o.push_back('S'); c = 'S'; }
    else if (c >= 0xE0 && c <= 0xFE && c != 0xF7) c -= 32;
    else if (c == 0xFF) c = 0x178;
    o.push_back(c);
  }
}

static void py_lower(const uint16_t *s, uint32_t n, U16 &o) {
  o.clear();
  for (uint32_t i = 0; i < n; i++) {
    uint16_t c = s[i];
    if (c >= 'A' && c <= 'Z') c += 32;
    else if (c >= 0xC0 && c <= 0xDE && c != 0xD7) c += 32;
    o.push_back(c);
  }
}

static int64_t py_find(const uint16_t *h, int64_t hn, const uint16_t *nd, int64_t nn, int64_t start, int64_t end) {
  if (end > hn) end = hn;
  if (start < 0) start = 0;
  if (start > end) return -1;
  for (int64_t i = start; i + nn <= end; i++)
    if (nn == 0 || memcmp(h + i, nd, (size_t) nn * 2) == 0) return i;
  return -1;
}

static int64_t py_rfind(const uint16_t *h, int64_t hn, const uint16_t *nd, int64_t nn, int64_t start, int64_t end) {
  if (end > hn) end = hn;
  if (start < 0) start = 0;
  if (start > end) return -1;
  for (int64_t i = end - nn; i >= start; i--)
    if (nn == 0 || memcmp(h + i, nd, (size_t) nn * 2) == 0) return i;
  return -1;
}

// Python int()/float() of text (ASCII grammar; underscores between digits as Python allows)
static bool strip_ws(const uint16_t *s, uint32_t n, std::string &o) {
  uint32_t a = 0, b = n;
  while (a < b && py_isspace(s[a])) a++;
  while (b > a && py_isspace(s[b - 1])) b--;
  o.clear();
  for (uint32_t i = a; i < b; i++) {
    if (s[i] > 127) return false;
    o.push_back((char) s[i]);
  }
  return true;
}

static bool digits_ok(const std::string &t, size_t a, size_t b, bool hex, std::string &out) {
  if (a >= b) return false;
  bool prev_digit = false;
  for (size_t i = a; i < b; i++) {
    char c = t[i];
    if (c == '_') {
      if (!prev_digit || i + 1 >= b) return false;
      prev_digit = false;
      continue;
    }
    if (!(hex ? isxdigit((unsigned char) c) : isdigit((unsigned char) c))) return false;
    out.push_back(c);
    prev_digit = true;
  }
  return prev_digit;
}

static bool py_int(const uint16_t *s, uint32_t n, int base, int32_t &out) {
  std::string t, d;
  if (!strip_ws(s, n, t)) return false;
  size_t i = 0;
  bool neg = false;
  if (i < t.size() && (t[i] == '+' || t[i] == '-')) neg = t[i++] == '-';
  if (base == 16 && i + 1 < t.size() && t[i] == '0' && (t[i + 1] == 'x' || t[i + 1] == 'X')) {
    i += 2;
    if (i < t.size() && t[i] == '_') i++;
  }
  if (!digits_ok(t, i, t.size(), base == 16, d)) return false;
  uint32_t v = 0;
  for (char c : d) v = v * (uint32_t) base + (uint32_t) (isdigit((unsigned char) c) ? c - '0' : (tolower(c) - 'a' + 10));
  out = (int32_t) (neg ? 0u - v : v);
  return true;
}

static bool py_float(const uint16_t *s, uint32_t n, double &out) {
  std::string t;
  if (!strip_ws(s, n, t)) return false;
  size_t i = 0;
  std::string c;
  if (i < t.size() && (t[i] == '+' || t[i] == '-')) c.push_back(t[i++]);
  std::string rest = t.substr(i), low;
  for (char ch : rest) low.push_back((char) tolower((unsigned char) ch));
  if (low == "inf" || low == "infinity") {
    out = c == "-" ? -INFINITY : INFINITY;
    return true;
  }
  if (low == "nan") {
    out = NAN;
    return true;
  }
  // [digits][.digits][e[sign]digits] with at least one digit in the mantissa
  size_t j = i, k;
  std::string m;
  k = j;
  while (k < t.size() && (isdigit((unsigned char) t[k]) || t[k] == '_')) k++;
  bool had = false;
  if (k > j) {
    if (!digits_ok(t, j, k, false, m)) return false;
    had = true;
  }
  j = k;
  if (j < t.size() && t[j] == '.') {
    m.push_back('.');
    j++;
    k = j;
    while (k < t.size() && (isdigit((unsigned char) t[k]) || t[k] == '_')) k++;
    if (k > j) {
      if (!digits_ok(t, j, k, false, m)) return false;
      had = true;
    }
    j = k;
  }
  if (!had) return false;
  if (j < t.size() && (t[j] == 'e' || t[j] == 'E')) {
    m.push_back('e');
    j++;
    if (j < t.size() && (t[j] == '+' || t[j] == '-')) m.push_back(t[j++]);
    k = j;
    while (k < t.size() && (isdigit((unsigned char) t[k]) || t[k] == '_')) k++;
    if (!digits_ok(t, j, k, false, m)) return false;
    j = k;
  }
  if (j != t.size()) return false;
  out = strtod((c + m).c_str(), nullptr);
  return true;
}

static inline int32_t d2i(double v) {
  if (v != v) return 0;
  if (v >= 2147483647.0) return 2147483647;
  if (v <= -2147483648.0) return INT32_MIN;
  return (int32_t) v;
}
static inline int64_t d2l(double v) {
  if (v != v) return 0;
  if (v >= 9.223372036854776e18) return INT64_MAX;
  if (v <= -9.223372036854776e18) return INT64_MIN;
  return (int64_t) v;
}

static double py_pow(double x, double y) {
  if (!std::isfinite(x) || !std::isfinite(y)) return std::pow(x, y);
  if (x == 0 && y < 0) return NAN;               // ValueError -> nan
  double r = std::pow(x, y);
  if (std::isnan(r)) return NAN;
  if (std::isinf(r)) return INFINITY;            // OverflowError -> inf
  return r;
}
static double py_log(double x) {
  if (x != x) return x;
  if (x <= 0) return NAN;                         // ValueError -> nan (Java would give -inf)
  return std::log(x);
}
static double py_trig(double (*fn)(double), double x) {
  if (x != x || std::isinf(x)) return NAN;
  return fn(x);
}

static const float DEG_TO_RAD_F = (float) ((double) (float) M_PI / 180.0);
static const float RAD_TO_DEG_F = (float) (180.0 / (double) (float) M_PI);

// ---------------------------------------------------------------------------- the natives
static const uint8_t ESZ[9] = {1, 1, 2, 2, 4, 8, 4, 8, 4};


struct Natives {
  Vm &vm;
  Api &api;
  Slot *a;
  int argc;
  uint8_t flags;
  Slot &ret;
  uint8_t &rt;
  U16 t1, t2;

  double D(int i) const { return a[i].d; }
  bool S(int i, const uint16_t *&s, uint32_t &n) { return vm.str(a[i].r, s, n); }
  // a string argument that Python reads with str(x) / (x or ""): null -> the given text
  void S_or(int i, const char *dflt, U16 &o) {
    const uint16_t *s;
    uint32_t n;
    o.clear();
    if (a[i].r == 0) {
      for (const char *q = dflt; *q; q++) o.push_back((uint8_t) *q);
      return;
    }
    if (vm.str(a[i].r, s, n)) o.assign(s, n);
  }
  void RI(int32_t v) { ret.i = v; rt = 0; }
  void RL(int64_t v) { ret.l = v; rt = 0; }
  void RF(double v) { ret.f = (float) v; rt = 0; }
  void RD(double v) { ret.d = v; rt = 0; }
  bool RS(const U16 &s) {
    uint32_t r = vm.new_string(s.data(), s.size());
    ret.r = r;
    rt = 1;
    return r != 0;
  }
  bool RA(const char *s) {
    uint32_t r = vm.new_string_ascii(s, strlen(s));
    ret.r = r;
    rt = 1;
    return r != 0;
  }
  bool fail(Fault f, const char *m) {
    vm.set_fault(f, m);
    return false;
  }
  void colour_args(double *v, bool *ints) {
    for (int i = 0; i < argc; i++) {
      v[i] = a[i].d;
      ints[i] = (flags >> i) & 1;
    }
  }

  bool run(uint16_t id);
  bool arrays(uint16_t id);
  bool strings(uint16_t id);
};

bool call_native(Vm &vm, uint16_t id, int argc, uint8_t flags, Slot *args, uint8_t *tags, Slot &ret, uint8_t &ret_tag) {
  (void) tags;
  Natives n{vm, vm.api, args, argc, flags, ret, ret_tag, {}, {}};
  ret.l = 0;
  ret_tag = 0;
  return n.run(id);
}

bool Natives::run(uint16_t id) {
  double v[16];
  bool ints[16];
  switch (id) {
    // ---- structure
    case N_size: api.size(D(0), D(1), (flags & 1) != 0); return true;
    case N_smooth: api.smooth(); return true;
    case N_no_smooth: api.no_smooth(); return true;
    case N_no_loop: api.no_loop(); return true;
    case N_loop: api.loop(); return true;
    case N_redraw: api.do_redraw(); return true;
    case N_exit: api.exit(); return true;
    case N_millis: RI(api.millis()); return true;
    case N_clock: RI(api.clock(flags)); return true;
    // ---- colour
    case N_background: colour_args(v, ints); api.background(v, argc, ints); return true;
    case N_fill: colour_args(v, ints); api.fill(v, argc, ints); return true;
    case N_stroke: colour_args(v, ints); api.stroke(v, argc, ints); return true;
    case N_color: colour_args(v, ints); RI(api.color(v, argc, ints)); return true;
    case N_no_fill: api.no_fill(); return true;
    case N_no_stroke: api.no_stroke(); return true;
    case N_clear: api.clear(); return true;
    case N_stroke_weight: api.stroke_weight(D(0)); return true;
    case N_stroke_cap: api.stroke_cap(D(0)); return true;
    case N_stroke_join: api.stroke_join(D(0)); return true;
    case N_color_mode: {
      for (int i = 1; i < argc; i++) v[i - 1] = a[i].d;
      api.color_mode(D(0), v, argc - 1);
      return true;
    }
    case N_red: RF(api.red(D(0))); return true;
    case N_green: RF(api.green(D(0))); return true;
    case N_blue: RF(api.blue(D(0))); return true;
    case N_alpha: RF(api.alpha(D(0))); return true;
    case N_brightness: RF(api.brightness(D(0))); return true;
    case N_hue: RF(api.hue(D(0))); return true;
    case N_saturation: RF(api.saturation(D(0))); return true;
    case N_lerp_color: RI(api.lerp_color(D(0), D(1), D(2))); return true;
    // ---- textures
    case N_texture:
    case N_stroke_texture: {
      S_or(0, "", t1);
      bool has = flags & 1;
      if (id == N_texture) api.texture(t1.data(), (int) t1.size(), has, has ? D(1) : 0.0);
      else api.stroke_texture(t1.data(), (int) t1.size(), has, has ? D(1) : 0.0);
      return true;
    }
    case N_no_texture: api.no_texture(); return true;
    case N_no_stroke_texture: api.no_stroke_texture(); return true;
    case N_texture_seed: api.texture_seed(D(0)); return true;
    case N_background_texture: {
      const bool has_extra = flags & 0x80;
      const int nn = argc - 1 - (has_extra ? 1 : 0);
      for (int i = 0; i < nn; i++) {
        v[i] = a[i].d;
        ints[i] = (flags >> i) & 1;
      }
      S_or(nn, "", t1);
      api.background_texture(v, nn, ints, t1.data(), (int) t1.size(), has_extra, has_extra ? D(nn + 1) : 0.0);
      return true;
    }
    // ---- shapes
    case N_point: api.point_z(D(0), D(1), argc == 3, argc == 3 ? D(2) : 0.0); return true;
    case N_line:
      if (argc == 6) api.line3(D(0), D(1), D(2), D(3), D(4), D(5));
      else api.line(D(0), D(1), D(2), D(3));
      return true;
    case N_rect: {
      for (int i = 4; i < argc; i++) v[i - 4] = a[i].d;
      api.rect(D(0), D(1), D(2), D(3), v, argc - 4);
      return true;
    }
    case N_square: api.square(D(0), D(1), D(2)); return true;
    case N_ellipse: api.ellipse(D(0), D(1), D(2), D(3)); return true;
    case N_circle: api.circle(D(0), D(1), D(2)); return true;
    case N_arc: api.arc(D(0), D(1), D(2), D(3), D(4), D(5), argc == 7, argc == 7 ? D(6) : 0.0); return true;
    case N_triangle: api.triangle(D(0), D(1), D(2), D(3), D(4), D(5)); return true;
    case N_quad: api.quad(D(0), D(1), D(2), D(3), D(4), D(5), D(6), D(7)); return true;
    case N_bezier: api.bezier(D(0), D(1), D(2), D(3), D(4), D(5), D(6), D(7)); return true;
    case N_curve: api.curve(D(0), D(1), D(2), D(3), D(4), D(5), D(6), D(7)); return true;
    case N_begin_shape: api.begin_shape(argc == 1, argc == 1 ? D(0) : 0.0); return true;
    case N_end_shape: api.end_shape(argc == 1, argc == 1 ? D(0) : 0.0); return true;
    case N_vertex: api.vertex3(D(0), D(1), argc == 3, argc == 3 ? D(2) : 0.0); return true;
    case N_bezier_vertex:
    case N_quadratic_vertex: {
      for (int i = 0; i < argc; i++) v[i] = a[i].d;
      if (id == N_bezier_vertex) api.bezier_vertex(v, argc);
      else api.quadratic_vertex(v, argc);
      return true;
    }
    case N_curve_vertex: api.curve_vertex3(D(0), D(1), argc == 3 ? D(2) : 0.0); return true;
    case N_begin_contour: api.begin_contour(); return true;
    case N_end_contour: api.end_contour(); return true;
    case N_rect_mode: api.rect_mode(D(0)); return true;
    case N_ellipse_mode: api.ellipse_mode(D(0)); return true;
    case N_curve_tightness: api.curve_tightness(D(0)); return true;
    case N_bezier_point: RF(api.bezier_point(D(0), D(1), D(2), D(3), D(4))); return true;
    case N_bezier_tangent: RF(api.bezier_tangent(D(0), D(1), D(2), D(3), D(4))); return true;
    case N_curve_point: RF(api.curve_point(D(0), D(1), D(2), D(3), D(4))); return true;
    // ---- matrix
    case N_push_matrix: api.push_matrix(); return true;
    case N_pop_matrix: api.pop_matrix(); return true;
    case N_push: api.push(); return true;
    case N_pop: api.pop(); return true;
    case N_push_style: api.push_style(); return true;
    case N_pop_style: api.pop_style(); return true;
    case N_translate: api.translate(D(0), D(1), argc == 3 ? D(2) : 0.0); return true;
    case N_rotate: api.rotate(D(0)); return true;
    case N_scale: api.scale(D(0), argc >= 2, argc >= 2 ? D(1) : 0.0, argc == 3, argc == 3 ? D(2) : 0.0); return true;
    case N_shear_x: api.shear_x(D(0)); return true;
    case N_shear_y: api.shear_y(D(0)); return true;
    case N_reset_matrix: api.reset_matrix(); return true;
    case N_screen_x: RF(api.screen_x(D(0), D(1), argc == 3, argc == 3 ? D(2) : 0.0)); return true;
    case N_screen_y: RF(api.screen_y(D(0), D(1), argc == 3, argc == 3 ? D(2) : 0.0)); return true;
    // ---- P3D
    case N_rotate_x: api.rotate_x(D(0)); return true;
    case N_rotate_y: api.rotate_y(D(0)); return true;
    case N_rotate_axis: api.rotate_axis(D(0), D(1), D(2), D(3)); return true;
    case N_apply_matrix:
    case N_camera:
    case N_perspective:
    case N_ortho:
    case N_frustum:
    case N_ambient_light:
    case N_directional_light:
    case N_point_light:
    case N_spot_light: {
      for (int i = 0; i < argc && i < 16; i++) v[i] = a[i].d;
      if (id == N_apply_matrix) api.apply_matrix(v, argc);
      else if (id == N_camera) api.camera(v, argc);
      else if (id == N_perspective) api.perspective(v, argc);
      else if (id == N_ortho) api.ortho(v, argc);
      else if (id == N_frustum) api.frustum(v);
      else if (id == N_ambient_light) api.ambient_light(v, argc);
      else if (id == N_directional_light) api.directional_light(v);
      else if (id == N_point_light) api.point_light(v);
      else api.spot_light(v);
      return true;
    }
    case N_screen_z: RF(api.screen_z(D(0), D(1), argc == 3 ? D(2) : 0.0)); return true;
    case N_lights: api.lights(); return true;
    case N_no_lights: api.no_lights(); return true;
    case N_light_falloff: api.light_falloff(D(0), D(1), D(2)); return true;
    case N_light_specular: api.light_specular(D(0), D(1), D(2)); return true;
    case N_ambient: colour_args(v, ints); api.ambient(v, argc, ints); return true;
    case N_specular: colour_args(v, ints); api.specular(v, argc, ints); return true;
    case N_emissive: colour_args(v, ints); api.emissive(v, argc, ints); return true;
    case N_shininess: api.shininess(D(0)); return true;
    case N_normal: api.normal(D(0), D(1), D(2)); return true;
    case N_hint: api.hint(D(0)); return true;
    case N_box: api.box(D(0), argc == 3, argc == 3 ? D(1) : 0.0, argc == 3 ? D(2) : 0.0); return true;
    case N_sphere_detail: api.sphere_detail(D(0), argc == 2, argc == 2 ? D(1) : 0.0); return true;
    case N_sphere: api.sphere(D(0)); return true;
    case N_text_z: {
      S_or(0, "None", t1);
      api.text_z(t1.data(), (int) t1.size(), D(1), D(2), D(3));
      return true;
    }
    case N_blend_mode: api.blend_mode(D(0)); return true;
    case N_filter: api.filter(D(0), argc == 2, argc == 2 ? D(1) : 0.0); return true;
    // ---- text
    case N_text:
    case N_text_box: {
      S_or(0, "None", t1);                      // Api.text: str(s)
      if (id == N_text) api.text(t1.data(), (int) t1.size(), D(1), D(2));
      else api.text_box(t1.data(), (int) t1.size(), D(1), D(2), D(3), D(4));
      return true;
    }
    case N_text_size: api.text_size(D(0)); return true;
    case N_text_align: api.text_align(D(0), argc == 2, argc == 2 ? D(1) : 0.0); return true;
    case N_text_leading: api.text_leading(D(0)); return true;
    case N_text_width: S_or(0, "None", t1); RF(api.text_width(t1.data(), (int) t1.size())); return true;
    case N_text_ascent: RF(api.text_ascent()); return true;
    case N_text_descent: RF(api.text_descent()); return true;
    case N_create_font: {
      uint32_t r = vm.alloc(T_FONT, 0, 0, 8);
      if (!r) return false;
      double sz = D(0);                        // PFont(float(size))
      memcpy(vm.obj_ptr(r) + 8, &sz, 8);
      ret.r = r;
      rt = 1;
      return true;
    }
    case N_text_font: {
      uint8_t *h = vm.obj_ptr(a[0].r);
      if (!h) return fail(Fault::Runtime, "textFont() got a null font");
      if (h[0] != T_FONT) return fail(Fault::Runtime, "not a font");
      double sz;
      memcpy(&sz, h + 8, 8);
      api.text_font(argc == 2 ? D(1) : sz);
      return true;
    }
    // ---- pixels (the array lives in the VM heap)
    case N_load_pixels: {
      uint32_t r = vm.new_array(K_INT, W * H);
      if (!r) return false;
      uint8_t *data;
      uint32_t n;
      vm.arr(r, K_INT, data, n);
      api.load_pixels((int32_t *) data, vm.pixels == 0);   // (arena payloads are 8-aligned)
      vm.pixels = r;
      return true;
    }
    case N_update_pixels: {
      if (!vm.pixels) return true;
      uint8_t *data;
      uint32_t n;
      if (!vm.arr(vm.pixels, K_INT, data, n) || n != (uint32_t) (W * H)) return false;
      api.update_pixels((const int32_t *) data);
      return true;
    }
    case N_get: RI(api.get(D(0), D(1))); return true;
    case N_set: api.set(D(0), D(1), D(2)); return true;
    // ---- randomness
    case N_random: RF(api.random(D(0))); return true;
    case N_random2: RF(api.random2(D(0), D(1))); return true;
    case N_random_seed: api.random_seed(D(0)); return true;
    case N_random_gaussian: RF(api.random_gaussian()); return true;
    case N_noise:
      api.charged += 4;
      RF(api.noise(D(0), argc > 1 ? D(1) : 0.0, argc > 2 ? D(2) : 0.0));
      return true;
    case N_noise_seed: api.noise_seed(D(0)); return true;
    case N_noise_detail: api.noise_detail(D(0), argc == 2, argc == 2 ? D(1) : 0.0); return true;
    // ---- PApplet maths (float in, float out)
    case N_sin: RF(py_trig(py_sin, D(0))); return true;
    case N_cos: RF(py_trig(py_cos, D(0))); return true;
    case N_tan: RF(std::tan(D(0))); return true;
    case N_asin: RF(std::asin(D(0))); return true;
    case N_acos: RF(std::acos(D(0))); return true;
    case N_atan: RF(std::atan(D(0))); return true;
    case N_sqrt: RF(std::sqrt(D(0))); return true;
    case N_exp: RF(std::exp(D(0))); return true;
    case N_log: RF(py_log(D(0))); return true;
    case N_degrees: RF(D(0) * (double) RAD_TO_DEG_F); return true;
    case N_radians: RF(D(0) * (double) DEG_TO_RAD_F); return true;
    case N_atan2: RF(std::atan2(D(0), D(1))); return true;
    case N_pow: RF(py_pow(D(0), D(1))); return true;
    case N_absi: RI((int32_t) (a[0].i < 0 ? 0u - (uint32_t) a[0].i : (uint32_t) a[0].i)); return true;
    case N_absf: RF(std::fabs(D(0))); return true;
    case N_mini: case N_maxi: {
      int32_t r = a[0].i;
      for (int i = 1; i < argc; i++) r = id == N_mini ? (a[i].i < r ? a[i].i : r) : (a[i].i > r ? a[i].i : r);
      RI(r);
      return true;
    }
    case N_minf: case N_maxf: {
      double r = D(0);
      for (int i = 1; i < argc; i++) r = id == N_minf ? (D(i) < r ? D(i) : r) : (D(i) > r ? D(i) : r);
      RF(r);
      return true;
    }
    case N_constraini: {
      int32_t x = a[0].i, lo = a[1].i, hi = a[2].i;
      RI(x < lo ? lo : x > hi ? hi : x);
      return true;
    }
    case N_constrainf: {
      double x = D(0), lo = D(1), hi = D(2);
      RF(x < lo ? lo : x > hi ? hi : x);
      return true;
    }
    case N_map: {
      float x = (float) D(0), a1 = (float) D(1), a2 = (float) D(2), b1 = (float) D(3), b2 = (float) D(4);
      float q = (x - a1) / (a2 - a1);
      float m = (b2 - b1) * q;
      RF(b1 + m);
      return true;
    }
    case N_lerp: {
      float x = (float) D(0), y = (float) D(1), t = (float) D(2);
      float d = y - x;
      float m = d * t;
      RF(x + m);
      return true;
    }
    case N_norm: {
      float x = (float) D(0), lo = (float) D(1), hi = (float) D(2);
      float p = x - lo, q = hi - lo;
      RF(p / q);
      return true;
    }
    case N_dist: case N_mag: {
      float acc;
      if (id == N_dist) {
        const int k = argc / 2;
        float d0 = (float) D(k) - (float) D(0);
        acc = d0 * d0;
        for (int i = 1; i < k; i++) {
          float di = (float) D(k + i) - (float) D(i);
          float sq = di * di;
          acc = acc + sq;
        }
      } else {
        float x0 = (float) D(0);
        acc = x0 * x0;
        for (int i = 1; i < argc; i++) {
          float xi = (float) D(i);
          float sq = xi * xi;
          acc = acc + sq;
        }
      }
      RF(std::sqrt((double) acc));
      return true;
    }
    case N_sq: { float x = (float) D(0); float r = x * x; RF(r); return true; }
    case N_floor: RI(D(0) == D(0) ? d2i(std::floor(D(0))) : 0); return true;
    case N_ceil: RI(D(0) == D(0) ? d2i(std::ceil(D(0))) : 0); return true;
    case N_round: RI(D(0) == D(0) ? d2i(std::floor(D(0) + 0.5)) : 0); return true;
    // ---- java.lang.Math (double)
    case N_m_sin: RD(py_trig(py_sin, D(0))); return true;
    case N_m_cos: RD(py_trig(py_cos, D(0))); return true;
    case N_m_tan: RD(std::tan(D(0))); return true;
    case N_m_asin: RD(std::asin(D(0))); return true;
    case N_m_acos: RD(std::acos(D(0))); return true;
    case N_m_atan: RD(std::atan(D(0))); return true;
    case N_m_sqrt: RD(std::sqrt(D(0))); return true;
    case N_m_exp: RD(std::exp(D(0))); return true;
    case N_m_log: RD(py_log(D(0))); return true;
    case N_m_floor: RD(std::floor(D(0))); return true;
    case N_m_ceil: RD(std::ceil(D(0))); return true;
    case N_m_toradians: RD(D(0) * (M_PI / 180.0)); return true;
    case N_m_todegrees: RD(D(0) * (180.0 / M_PI)); return true;
    case N_m_cbrt: RD(std::copysign(py_pow(std::fabs(D(0)), 1.0 / 3.0), D(0))); return true;
    case N_m_log10: RD(D(0) != D(0) ? D(0) : D(0) <= 0 ? NAN : std::log10(D(0))); return true;
    case N_m_signum: RD(D(0) != D(0) ? D(0) : (double) ((D(0) > 0) - (D(0) < 0))); return true;
    case N_m_rint:
      if (!std::isfinite(D(0))) return fail(Fault::Runtime, "arithmetic error (round of a non-number)");
      RD(std::nearbyint(D(0)));
      return true;
    case N_m_pow: RD(py_pow(D(0), D(1))); return true;
    case N_m_atan2: RD(std::atan2(D(0), D(1))); return true;
    case N_m_hypot: RD(py_hypot(D(0), D(1))); return true;
    case N_m_roundd: RL(D(0) == D(0) ? d2l(std::floor(D(0) + 0.5)) : 0); return true;
    case N_m_random: RD(api.rand.next_double()); return true;
    case N_m_absl: RL((int64_t) (a[0].l < 0 ? 0ull - (uint64_t) a[0].l : (uint64_t) a[0].l)); return true;
    case N_m_absd: RD(std::fabs(D(0))); return true;
    case N_m_minl: RL(a[1].l < a[0].l ? a[1].l : a[0].l); return true;
    case N_m_maxl: RL(a[1].l > a[0].l ? a[1].l : a[0].l); return true;
    case N_m_mind: RD(D(1) < D(0) ? D(1) : D(0)); return true;
    case N_m_maxd: RD(D(1) > D(0) ? D(1) : D(0)); return true;
    default: break;
  }
  if (id >= N_parse_int && id <= N_char_test) return strings(id);
  return arrays(id);
}

bool Natives::strings(uint16_t id) {
  const uint16_t *s, *p;
  uint32_t n, pn;
  char buf[96];
  switch (id) {
    case N_parse_int: {
      int32_t v;
      if (a[0].r == 0) { RI(0); return true; }
      if (!S(0, s, n)) return false;
      if (py_int(s, n, 10, v)) { RI(v); return true; }
      double d;
      RI(py_float(s, n, d) ? d2i(d) : 0);
      return true;
    }
    case N_parse_float: {
      double d;
      if (a[0].r == 0) { RF(NAN); return true; }
      if (!S(0, s, n)) return false;
      RF(py_float(s, n, d) ? d : NAN);
      return true;
    }
    case N_parse_bool: {
      S_or(0, "", t1);
      py_lower(t1.data(), (uint32_t) t1.size(), t2);
      static const uint16_t tru[4] = {'t', 'r', 'u', 'e'};
      RI(t2.size() == 4 && memcmp(t2.data(), tru, 8) == 0);
      return true;
    }
    case N_nf: {
      const int kind = flags & 3;
      const bool isint = flags & 4;
      double x = D(0);
      std::string out;
      if (argc == 1 && !isint) {
        jfloat_str(x, true, buf, sizeof buf);
        out = buf;
      } else {
        // _nf_num(v, left, right)
        double left = argc > 1 ? D(1) : 0.0;
        const bool neg = x < 0;
        double av = std::fabs(x);
        auto pint = [&](double d, bool &ok) -> int64_t {    // Python int(): NaN/inf raise
          ok = std::isfinite(d);
          return ok ? (int64_t) d : 0;
        };
        bool ok = true;
        int64_t lw = pint(left, ok);
        if (!ok) return fail(Fault::Runtime, "arithmetic error (nf)");
        if (lw < 0) lw = 0;
        if (lw > 64) lw = 64;
        std::string s1;
        if (argc < 3) {
          if (!std::isfinite(av)) return fail(Fault::Runtime, "arithmetic error (nf)");
          snprintf(buf, sizeof buf, "%.0f", std::trunc(av));
          s1 = buf;
          while ((int64_t) s1.size() < lw) s1.insert(s1.begin(), '0');
        } else {
          int64_t rw = pint(D(2), ok);
          if (!ok) return fail(Fault::Runtime, "arithmetic error (nf)");
          if (rw < 0) rw = 0;
          if (rw > 40) rw = 40;
          snprintf(buf, sizeof buf, "%.*f", (int) rw, av);
          std::string t = buf;
          size_t dot = t.find('.');
          std::string ip = dot == std::string::npos ? t : t.substr(0, dot);
          std::string fp = dot == std::string::npos ? "" : t.substr(dot + 1);
          while ((int64_t) ip.size() < lw) ip.insert(ip.begin(), '0');
          s1 = ip + (fp.empty() ? "" : "." + fp);
        }
        out = (neg ? "-" : "") + s1;
      }
      if (kind == 1 && x >= 0) out = " " + out;
      else if (kind == 2 && x >= 0) out = "+" + out;
      else if (kind == 3) {
        bool neg = !out.empty() && out[0] == '-';
        std::string body = out;
        while (!body.empty() && body[0] == '-') body.erase(body.begin());
        size_t dot = body.find('.');
        std::string ip = dot == std::string::npos ? body : body.substr(0, dot);
        std::string rest = dot == std::string::npos ? "" : body.substr(dot);
        bool digits = !ip.empty() && std::all_of(ip.begin(), ip.end(), [](char c) { return c >= '0' && c <= '9'; });
        if (digits) {
          size_t k = 0;
          while (k + 1 < ip.size() && ip[k] == '0') k++;     // int(ip) drops leading zeros
          ip = ip.substr(k);
          std::string g;
          int cnt = 0;
          for (int i = (int) ip.size() - 1; i >= 0; i--) {
            g.insert(g.begin(), ip[i]);
            if (++cnt % 3 == 0 && i > 0) g.insert(g.begin(), ',');
          }
          ip = g;
        }
        out = (neg ? "-" : "") + ip + rest;
      }
      return RA(out.c_str());
    }
    case N_hex: {
      // f"{v & 0xFFFFFFFF:08X}"[-k:], k = the digits argument when given (0 keeps all 8), else the width
      int k = argc > 1 ? a[1].i : flags;
      snprintf(buf, sizeof buf, "%08X", (unsigned) (uint32_t) a[0].i);
      std::string h = buf, o;
      if (k == 0) o = h;
      else if (k > 0) o = h.substr(k >= 8 ? 0 : 8 - k);
      else o = (-k >= 8) ? "" : h.substr(-k);
      return RA(o.c_str());
    }
    case N_unhex: {
      int32_t v;
      if (a[0].r == 0) { RI(0); return true; }
      if (!S(0, s, n)) return false;
      RI(py_int(s, n, 16, v) ? v : 0);
      return true;
    }
    case N_s_length: if (!S(0, s, n)) return false; RI((int32_t) n); return true;
    case N_s_charat: {
      if (!S(0, s, n)) return false;
      int32_t i = a[1].i;
      if (i < 0 || (uint32_t) i >= n) return fail(Fault::Runtime, "charAt() outside the string");
      RI(s[i]);
      return true;
    }
    case N_s_substring: {
      if (!S(0, s, n)) return false;
      int64_t x = a[1].i, y = argc == 3 ? a[2].i : n;
      if (x < 0 || y > n || x > y) return fail(Fault::Runtime, "substring() outside the string");
      t1.assign(s + x, (size_t) (y - x));
      return RS(t1);
    }
    case N_s_indexof:
    case N_s_lastindexof: {
      if (!S(0, s, n)) return false;
      U16 needle;
      if (flags & 1) needle.push_back((uint16_t) a[1].i);
      else {
        if (!S(1, p, pn)) return false;
        needle.assign(p, pn);
      }
      if (!S(0, s, n)) return false;
      int64_t nn = (int64_t) needle.size();
      if (argc == 3) {
        int64_t st = a[2].i < 0 ? 0 : a[2].i;
        RI((int32_t) (id == N_s_indexof ? py_find(s, n, needle.data(), nn, st, n)
                                        : py_rfind(s, n, needle.data(), nn, 0, st + nn)));
      } else {
        RI((int32_t) (id == N_s_indexof ? py_find(s, n, needle.data(), nn, 0, n)
                                        : py_rfind(s, n, needle.data(), nn, 0, n)));
      }
      return true;
    }
    case N_s_upper: if (!S(0, s, n)) return false; py_upper(s, n, t1); return RS(t1);
    case N_s_lower: if (!S(0, s, n)) return false; py_lower(s, n, t1); return RS(t1);
    case N_s_trim: {
      if (!S(0, s, n)) return false;
      uint32_t x = 0, y = n;
      while (x < y && py_isspace(s[x])) x++;
      while (y > x && py_isspace(s[y - 1])) y--;
      t1.assign(s + x, y - x);
      return RS(t1);
    }
    case N_ptrim: {                             // PApplet.trim: strip(" \t\n\r\f ")
      if (a[0].r == 0) { t1.clear(); return RS(t1); }
      if (!S(0, s, n)) return false;
      auto ws = [](uint16_t c) { return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' || c == 0xA0; };
      uint32_t x = 0, y = n;
      while (x < y && ws(s[x])) x++;
      while (y > x && ws(s[y - 1])) y--;
      t1.assign(s + x, y - x);
      return RS(t1);
    }
    case N_s_isempty: if (!S(0, s, n)) return false; RI(n == 0); return true;
    case N_s_hashcode: {
      if (!S(0, s, n)) return false;
      uint32_t h = 0;
      for (uint32_t i = 0; i < n; i++) h = 31 * h + s[i];
      RI((int32_t) h);
      return true;
    }
    case N_s_equals: {
      if (!S(0, s, n)) return false;
      if (a[1].r == 0) { RI(0); return true; }
      if (!S(1, p, pn)) return false;
      RI(n == pn && memcmp(s, p, (size_t) n * 2) == 0);
      return true;
    }
    case N_s_equalsic: {                        // (arg or "").lower() == recv.lower(); arg first
      S_or(0, "", t1);
      if (!S(1, s, n)) return false;
      U16 x, y;
      py_lower(t1.data(), (uint32_t) t1.size(), x);
      py_lower(s, n, y);
      RI(x == y);
      return true;
    }
    case N_s_startswith: case N_s_endswith: case N_s_contains: {
      if (!S(0, s, n) || !S(1, p, pn)) return false;
      if (!S(0, s, n)) return false;
      bool r;
      if (id == N_s_startswith) r = pn <= n && memcmp(s, p, (size_t) pn * 2) == 0;
      else if (id == N_s_endswith) r = pn <= n && memcmp(s + n - pn, p, (size_t) pn * 2) == 0;
      else r = py_find(s, n, p, pn, 0, n) >= 0;
      RI(r);
      return true;
    }
    case N_s_compareto: {
      if (!S(0, s, n) || !S(1, p, pn)) return false;
      if (!S(0, s, n)) return false;
      uint32_t k = n < pn ? n : pn;
      for (uint32_t i = 0; i < k; i++)
        if (s[i] != p[i]) { RI((int32_t) s[i] - (int32_t) p[i]); return true; }
      RI((int32_t) n - (int32_t) pn);
      return true;
    }
    case N_s_replace: {
      U16 old, nw;
      if (flags & 1) {
        old.push_back((uint16_t) a[1].i);
        nw.push_back((uint16_t) a[2].i);
      } else {
        if (!S(1, p, pn)) return false;
        old.assign(p, pn);
        if (!S(2, p, pn)) return false;
        nw.assign(p, pn);
      }
      if (!S(0, s, n)) return false;
      t1.clear();
      if (old.empty()) {
        for (uint32_t i = 0; i < n; i++) {
          t1 += nw;
          t1.push_back(s[i]);
        }
        t1 += nw;
      } else {
        uint32_t i = 0;
        while (i < n) {
          if (i + old.size() <= n && memcmp(s + i, old.data(), old.size() * 2) == 0) {
            t1 += nw;
            i += (uint32_t) old.size();
          } else {
            t1.push_back(s[i++]);
          }
          if (t1.size() > 65536) break;
        }
      }
      if (t1.size() > 65536) return fail(Fault::Memory, "a string grew past 64 K characters");
      return RS(t1);
    }
    case N_s_repeat: {
      if (!S(0, s, n)) return false;
      int32_t k = a[1].i;
      if (k < 0 || (uint64_t) n * (uint64_t) k > 65536) return fail(Fault::Memory, "repeat() makes too long a string");
      t1.clear();
      for (int32_t i = 0; i < k; i++) t1.append(s, n);
      return RS(t1);
    }
    case N_s_fromchars: {
      uint8_t *data;
      if (!vm.arr(a[0].r, K_CHAR, data, n)) return false;
      t1.assign((const uint16_t *) data, n);
      return RS(t1);
    }
    case N_s_tochars: {
      if (!S(0, s, n)) return false;
      uint32_t r = vm.new_array(K_CHAR, (int32_t) n);
      if (!r) return false;
      if (!S(0, s, n)) return false;
      uint8_t *data;
      vm.arr(r, K_CHAR, data, pn);
      memcpy(data, s, (size_t) n * 2);
      ret.r = r;
      rt = 1;
      return true;
    }
    case N_char_test: {
      uint16_t c = (uint16_t) a[0].i;
      bool r = false;
      switch (flags) {
        case 0: r = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || c == 0xAA || c == 0xB5 || c == 0xBA ||
                    (c >= 0xC0 && c <= 0xFF && c != 0xD7 && c != 0xF7) || (c >= 0x100 && c <= 0x2AF); break;
        case 1: r = (c >= '0' && c <= '9') || c == 0xB2 || c == 0xB3 || c == 0xB9; break;
        case 2: r = py_isspace(c); break;
        case 3: r = (c >= 'A' && c <= 'Z') || (c >= 0xC0 && c <= 0xDE && c != 0xD7); break;
        default: r = (c >= 'a' && c <= 'z') || c == 0xAA || c == 0xB5 || c == 0xBA || (c >= 0xDF && c <= 0xFF && c != 0xF7); break;
      }
      RI(r);
      return true;
    }
    default: break;
  }
  return fail(Fault::Runtime, "unknown native");
}

bool Natives::arrays(uint16_t id) {
  uint8_t *d, *e;
  uint32_t n, m;
  const uint8_t kind = flags;
  auto out = [&](uint32_t len) -> uint8_t * {    // a new array of `kind` (the arguments may move)
    uint32_t r = vm.new_array(kind > 8 ? K_REF : kind, (int32_t) len);
    if (!r) return nullptr;
    ret.r = r;
    rt = 1;
    uint8_t *p;
    uint32_t k;
    vm.arr(r, 0xFF, p, k);
    return p;
  };
  switch (id) {
    case N_min_arri: case N_max_arri: case N_min_arrf: case N_max_arrf: {
      const bool is_int = id == N_min_arri || id == N_max_arri;
      const bool mn = id == N_min_arri || id == N_min_arrf;
      if (!vm.arr(a[0].r, is_int ? K_INT : K_FLOAT, d, n)) return false;
      if (n == 0) return fail(Fault::Runtime, mn ? "min() of an empty array" : "max() of an empty array");
      if (is_int) {
        int32_t r, x;
        memcpy(&r, d, 4);
        for (uint32_t i = 1; i < n; i++) {
          memcpy(&x, d + 4 * i, 4);
          if (mn ? x < r : x > r) r = x;
        }
        RI(r);
      } else {
        float r, x;
        memcpy(&r, d, 4);
        for (uint32_t i = 1; i < n; i++) {
          memcpy(&x, d + 4 * i, 4);
          if (mn ? x < r : x > r) r = x;
        }
        RF(r);
      }
      return true;
    }
    case N_a_append: {
      if (!vm.arr(a[0].r, kind, d, n)) return false;
      uint8_t *o = out(n + 1);
      if (!o) return false;
      vm.arr(a[0].r, kind, d, n);
      const size_t es = ESZ[kind];
      memcpy(o, d, (size_t) n * es);
      const Slot &v = a[1];
      switch (kind) {
        case K_BOOLEAN: o[n] = v.i ? 1 : 0; break;
        case K_BYTE: o[n] = (uint8_t) v.i; break;
        case K_SHORT: case K_CHAR: { uint16_t x = (uint16_t) v.i; memcpy(o + 2 * n, &x, 2); break; }
        case K_INT: memcpy(o + 4 * (size_t) n, &v.i, 4); break;
        case K_LONG: memcpy(o + 8 * (size_t) n, &v.l, 8); break;
        case K_FLOAT: memcpy(o + 4 * (size_t) n, &v.f, 4); break;
        case K_DOUBLE: memcpy(o + 8 * (size_t) n, &v.d, 8); break;
        default: memcpy(o + 4 * (size_t) n, &v.r, 4); break;
      }
      return true;
    }
    case N_a_shorten: case N_a_reverse: case N_a_clone: {
      if (!vm.arr(a[0].r, kind, d, n)) return false;
      uint32_t len = id == N_a_shorten ? (n ? n - 1 : 0) : n;
      uint8_t *o = out(len);
      if (!o) return false;
      vm.arr(a[0].r, kind, d, n);
      const size_t es = ESZ[kind];
      if (id == N_a_reverse)
        for (uint32_t i = 0; i < n; i++) memcpy(o + es * i, d + es * (n - 1 - i), es);
      else
        memcpy(o, d, len * es);
      return true;
    }
    case N_a_expand: {
      if (!vm.arr(a[0].r, kind, d, n)) return false;
      int64_t len = argc > 1 ? a[1].i : (n * 2 > 1 ? (int64_t) n * 2 : 1);
      if (len > 4000000 || len < 0) return fail(Fault::Memory, "expand() to a huge array");
      uint8_t *o = out((uint32_t) len);
      if (!o) return false;
      vm.arr(a[0].r, kind, d, n);
      memcpy(o, d, (size_t) std::min<int64_t>(len, n) * ESZ[kind]);
      return true;
    }
    case N_a_subset: {
      if (!vm.arr(a[0].r, kind, d, n)) return false;
      int64_t st = a[1].i, cnt = argc > 2 ? a[2].i : (int64_t) n - st;
      if (st < 0 || cnt < 0 || st + cnt > n) return fail(Fault::Runtime, "subset() outside the array");
      uint8_t *o = out((uint32_t) cnt);
      if (!o) return false;
      vm.arr(a[0].r, kind, d, n);
      memcpy(o, d + (size_t) st * ESZ[kind], (size_t) cnt * ESZ[kind]);
      return true;
    }
    case N_a_concat: {
      if (!vm.arr(a[0].r, kind, d, n) || !vm.arr(a[1].r, kind, e, m)) return false;
      uint8_t *o = out(n + m);
      if (!o) return false;
      vm.arr(a[0].r, kind, d, n);
      vm.arr(a[1].r, kind, e, m);
      memcpy(o, d, (size_t) n * ESZ[kind]);
      memcpy(o + (size_t) n * ESZ[kind], e, (size_t) m * ESZ[kind]);
      return true;
    }
    case N_a_sort: {
      if (!vm.arr(a[0].r, kind, d, n)) return false;
      uint8_t *o = out(n);
      if (!o) return false;
      vm.arr(a[0].r, kind, d, n);
      const size_t es = ESZ[kind];
      memcpy(o, d, n * es);
      api.charged += n * 2;
      switch (kind) {
        case K_BYTE: std::stable_sort((int8_t *) o, (int8_t *) o + n); break;
        case K_SHORT: std::stable_sort((int16_t *) o, (int16_t *) o + n); break;
        case K_CHAR: std::stable_sort((uint16_t *) o, (uint16_t *) o + n); break;
        case K_INT: std::stable_sort((int32_t *) o, (int32_t *) o + n); break;
        case K_LONG: std::stable_sort((int64_t *) o, (int64_t *) o + n); break;
        case K_FLOAT: {
          std::vector<double> t(n);
          for (uint32_t i = 0; i < n; i++) { float x; memcpy(&x, o + 4 * i, 4); t[i] = x; }
          { auto lt = [](double x, double y) { return x < y; }; PySort<double, decltype(lt)> ps; ps.sort(t.data(), (int) n, lt); }
          for (uint32_t i = 0; i < n; i++) { float x = (float) t[i]; memcpy(o + 4 * i, &x, 4); }
          break;
        }
        case K_DOUBLE: {
          std::vector<double> t(n);
          memcpy(t.data(), o, n * 8);
          { auto lt = [](double x, double y) { return x < y; }; PySort<double, decltype(lt)> ps; ps.sort(t.data(), (int) n, lt); }
          memcpy(o, t.data(), n * 8);
          break;
        }
        default: {           // String[]: by code units; a null in it is Python's TypeError
          std::vector<uint32_t> refs(n);
          memcpy(refs.data(), o, n * 4);
          for (uint32_t r : refs)
            if (!r) return fail(Fault::Runtime, "sort() of an array with null in it");
          bool bad = false;
          std::stable_sort(refs.begin(), refs.end(), [&](uint32_t x, uint32_t y) {
            const uint16_t *sx, *sy;
            uint32_t nx, ny;
            if (!vm.str(x, sx, nx) || !vm.str(y, sy, ny)) { bad = true; return false; }
            uint32_t k = nx < ny ? nx : ny;
            for (uint32_t i = 0; i < k; i++)
              if (sx[i] != sy[i]) return sx[i] < sy[i];
            return nx < ny;
          });
          if (bad) return false;
          memcpy(o, refs.data(), n * 4);
          break;
        }
      }
      return true;
    }
    case N_a_copy: {
      const bool five = argc == 5;
      uint32_t sr = a[0].r, dr = five ? a[2].r : a[1].r;
      uint8_t ks = vm.arr_kind(sr), kd = vm.arr_kind(dr);
      if (ks == 0xFF || kd == 0xFF) return fail(Fault::Runtime, "arrayCopy() of a null array");
      if (ks != kd) return fail(Fault::Runtime, "arrayCopy() between arrays of different types");
      vm.arr(sr, ks, d, n);
      vm.arr(dr, kd, e, m);
      int64_t sp = 0, dp = 0, cnt;
      if (argc == 2) cnt = std::min(n, m);
      else if (argc == 3) {
        cnt = a[2].i;
        if (cnt > n || cnt > m || cnt < 0) return fail(Fault::Runtime, "arrayCopy() outside the array");
      } else {
        sp = a[1].i;
        dp = a[3].i;
        cnt = a[4].i;
        if (sp < 0 || dp < 0 || cnt < 0 || sp + cnt > n || dp + cnt > m)
          return fail(Fault::Runtime, "arrayCopy() outside the array");
      }
      api.charged += (uint64_t) cnt;
      memmove(e + (size_t) dp * ESZ[kd], d + (size_t) sp * ESZ[ks], (size_t) cnt * ESZ[ks]);
      return true;
    }
    case N_a_toint: {
      const uint8_t src = kind;
      if (!vm.arr(a[0].r, src, d, n)) return false;
      uint32_t r = vm.new_array(K_INT, (int32_t) n);
      if (!r) return false;
      vm.arr(a[0].r, src, d, n);
      uint8_t *o;
      vm.arr(r, K_INT, o, m);
      for (uint32_t i = 0; i < n; i++) {
        int32_t x = 0;
        switch (src) {
          case K_BYTE: x = ((int8_t *) d)[i]; break;
          case K_SHORT: { int16_t t; memcpy(&t, d + 2 * i, 2); x = t; break; }
          case K_CHAR: { uint16_t t; memcpy(&t, d + 2 * i, 2); x = t; break; }
          case K_INT: memcpy(&x, d + 4 * i, 4); break;
          case K_LONG: { int64_t t; memcpy(&t, d + 8 * i, 8); x = t >= 2147483647 ? 2147483647 : t <= INT32_MIN ? INT32_MIN : (int32_t) t; break; }
          case K_FLOAT: { float t; memcpy(&t, d + 4 * i, 4); x = d2i(t); break; }
          case K_DOUBLE: { double t; memcpy(&t, d + 8 * i, 8); x = d2i(t); break; }
          default: break;
        }
        memcpy(o + 4 * i, &x, 4);
      }
      ret.r = r;
      rt = 1;
      return true;
    }
    default: break;
  }
  return fail(Fault::Runtime, "unknown native");
}

}  // namespace pde
