#include "pde_fp.h"  // first: no fused multiply-add (see the header)
// SPDX-License-Identifier: LGPL-2.1-only
// Follows Processing's own source (PGraphicsOpenGL, InGeometry and its shaders, Processing 4.5.2,
// GNU LGPL 2.1) through python/immanence/pde/three.py, to draw 3D exactly as Processing does.
// See THIRD-PARTY.md.
// runtime.Api's P3D half (and blendMode / filter), bit for bit with python/immanence/pde/runtime.py:
// the 3D matrix, camera and projection, lights and materials, box/sphere, 2D shapes and
// beginShape()/endShape() through the 3D pipeline (pde_three.cpp). Same build rules as pde_api.cpp.
#include <cmath>
#include <cstring>

#include "pde_api.h"

namespace pde {

static const char *const E_ARITH = "arithmetic error (ValueError)";

// ------------------------------------------------------------------ the matrix
void Api::embed(double *o) const {
  const double t[16] = {m_[0], m_[2], 0.0, m_[4], m_[1], m_[3], 0.0, m_[5], 0.0, 0.0, mz_, 0.0, 0.0, 0.0, 0.0, 1.0};
  std::memcpy(o, t, sizeof t);
}

void Api::apply3(const double *t) {
  if (!has_m3_) {
    embed(m3_);
    has_m3_ = true;
  }
  mmul4(m3_, t, m3_);
}

void Api::mv(double *out) const {
  double m[16];
  if (has_m3_) std::memcpy(m, m3_, sizeof m);
  else embed(m);
  mmul4(t3->cam, m, out);
}

bool Api::flat3() const { return !has_m3_ && t3->cam_default && t3->proj_default; }

bool Api::use3d() const {
  return t3 && (has_m3_ || !t3->cam_default || !t3->proj_default || t3->lighting || (t3->dirty && t3->depth_test));
}

bool Api::need3d(const char *what) {
  if (!p3d) {
    (void)what;
    raise(Fault::Runtime, "a 3D function needs size(240, 128, P3D)");
    return false;
  }
  return true;
}

void Api::rotate_x(double t) {
  if (!need3d("rotateX")) return;
  if (std::isinf(t)) {
    raise(Fault::Runtime, E_ARITH);
    return;
  }
  const double co = py_cos(t), si = py_sin(t);
  const double r[16] = {1.0, 0.0, 0.0, 0.0, 0.0, co, -si, 0.0, 0.0, si, co, 0.0, 0.0, 0.0, 0.0, 1.0};
  apply3(r);
}

void Api::rotate_y(double t) {
  if (!need3d("rotateY")) return;
  if (std::isinf(t)) {
    raise(Fault::Runtime, E_ARITH);
    return;
  }
  const double co = py_cos(t), si = py_sin(t);
  const double r[16] = {co, 0.0, si, 0.0, 0.0, 1.0, 0.0, 0.0, -si, 0.0, co, 0.0, 0.0, 0.0, 0.0, 1.0};
  apply3(r);
}

void Api::rotate_axis(double t, double x, double y, double z) {
  if (!need3d("rotate")) return;
  const double n2 = x * x + y * y + z * z;
  if (n2 < 1e-4) return;
  if (std::fabs(n2 - 1.0) > 1e-4) {
    const double n = std::sqrt(n2);
    x = x / n;
    y = y / n;
    z = z / n;
  }
  if (std::isinf(t)) {
    raise(Fault::Runtime, E_ARITH);
    return;
  }
  const double c = py_cos(t), s = py_sin(t);
  const double tt = 1.0 - c;
  const double r[16] = {tt * x * x + c, tt * x * y - s * z, tt * x * z + s * y, 0.0,
                        tt * x * y + s * z, tt * y * y + c, tt * y * z - s * x, 0.0,
                        tt * x * z - s * y, tt * y * z + s * x, tt * z * z + c, 0.0,
                        0.0, 0.0, 0.0, 1.0};
  apply3(r);
}

void Api::apply_matrix(const double *v, int n) {
  if (n == 6) {
    const double n00 = v[0], n01 = v[1], n02 = v[2], n10 = v[3], n11 = v[4], n12 = v[5];
    if (has_m3_) {
      const double t[16] = {n00, n01, 0.0, n02, n10, n11, 0.0, n12, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0};
      apply3(t);
      return;
    }
    const double a = m_[0], b = m_[1], c = m_[2], d = m_[3], e = m_[4], f = m_[5];
    m_[0] = a * n00 + c * n10;
    m_[1] = b * n00 + d * n10;
    m_[2] = a * n01 + c * n11;
    m_[3] = b * n01 + d * n11;
    m_[4] = a * n02 + c * n12 + e;
    m_[5] = b * n02 + d * n12 + f;
    return;
  }
  if (!need3d("applyMatrix")) return;
  apply3(v);
}

void Api::camera(const double *v, int n) {
  if (!need3d("camera")) return;
  double a[9] = {W / 2.0, H / 2.0, CAM_Z, W / 2.0, H / 2.0, 0.0, 0.0, 1.0, 0.0};
  if (n == 9) std::memcpy(a, v, sizeof a);
  double m[16];
  look_at(a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], m);
  t3->set_camera(m);
  const double dx = a[0] - a[3], dy = a[1] - a[4], dz = a[2] - a[5];
  t3->eye_dist = std::sqrt(dx * dx + dy * dy + dz * dz);
  reset_matrix();
}

void Api::perspective(const double *v, int n) {
  if (!need3d("perspective")) return;
  double m[16];
  if (n == 0) default_projection(m);
  else if (!perspective_m(v[0], v[1], v[2], v[3], m, fr())) return;
  t3->set_projection(m, false);
}

void Api::ortho(const double *v, int n) {
  if (!need3d("ortho")) return;
  double a[6] = {-W / 2.0, W / 2.0, -H / 2.0, H / 2.0, 0.0, 0.0};
  if (n >= 4) std::memcpy(a, v, 4 * sizeof(double));
  if (n == 6) {
    a[4] = v[4];
    a[5] = v[5];
  } else {
    a[4] = 0.0;
    a[5] = t3->eye_dist * 10.0;
  }
  double m[16];
  if (!ortho_m(a[0], a[1], a[2], a[3], a[4], a[5], m, fr())) return;
  t3->set_projection(m, true);
}

void Api::frustum(const double *v) {
  if (!need3d("frustum")) return;
  double m[16];
  if (!frustum_m(v[0], v[1], v[2], v[3], v[4], v[5], m, fr())) return;
  t3->set_projection(m, false);
}

double Api::screen_z(double x, double y, double z) {
  if (!need3d("screenZ")) return 0.0;
  double m[16], sx, sy, sz;
  mv(m);
  t3->screen(m, x, y, z, &sx, &sy, &sz);
  return f32(sz);
}

// ------------------------------------------------------------------ lights and materials
bool Api::rgbf(double v1, double v2, double v3, double *o) {
  const double mx = st_.cmax[0], my = st_.cmax[1], mzz = st_.cmax[2];
  double x = mx != 0.0 ? pymin(pymax(v1, 0.0), mx) / mx : 0.0;
  double y = my != 0.0 ? pymin(pymax(v2, 0.0), my) / my : 0.0;
  double z = mzz != 0.0 ? pymin(pymax(v3, 0.0), mzz) / mzz : 0.0;
  if (st_.cmode == 3) {
    if (y == 0) {
      o[0] = o[1] = o[2] = z;
      return true;
    }
    int64_t ix;
    if (!to_int(x, &ix)) return false;
    const double which = (x - (double)ix) * 6.0;
    int64_t iw;
    if (!to_int(which, &iw)) return false;
    const double f = which - (double)iw;
    const double p = z * (1.0 - y), q = z * (1.0 - y * f), t = z * (1.0 - (y * (1.0 - f)));
    const double tab[6][3] = {{z, t, p}, {q, z, p}, {p, z, t}, {p, q, z}, {t, p, z}, {z, p, q}};
    const int k = (int)floormod(iw, 6);
    o[0] = tab[k][0];
    o[1] = tab[k][1];
    o[2] = tab[k][2];
    return true;
  }
  o[0] = x;
  o[1] = y;
  o[2] = z;
  return true;
}

void Api::light_pos(double x, double y, double z, double *o) {
  double m[16];
  mv(m);
  o[0] = m[0] * x + m[1] * y + m[2] * z + m[3];
  o[1] = m[4] * x + m[5] * y + m[6] * z + m[7];
  o[2] = m[8] * x + m[9] * y + m[10] * z + m[11];
}

void Api::light_dir(double dx, double dy, double dz, double *o) {
  double m[16], ni[9];
  mv(m);
  o[0] = o[1] = o[2] = 0.0;
  if (!inv3(m, ni)) return;
  const double nx = dx * ni[0] + dy * ni[3] + dz * ni[6];
  const double ny = dx * ni[1] + dy * ni[4] + dz * ni[7];
  const double nz = dx * ni[2] + dy * ni[5] + dz * ni[8];
  const double d = std::sqrt(nx * nx + ny * ny + nz * nz);
  if (d > 0.0) {
    o[0] = nx / d;
    o[1] = ny / d;
    o[2] = nz / d;
  }
}

static const double Z3[3] = {0.0, 0.0, 0.0};
static const double NOSPOT[2] = {0.0, 0.0};
static const double FALL1[3] = {1.0, 0.0, 0.0};

void Api::lights() {
  if (!p3d) return;
  Three &t = *t3;
  t.nlights = 0;
  t.fall[0] = 1.0;
  t.fall[1] = t.fall[2] = 0.0;
  t.lspec[0] = t.lspec[1] = t.lspec[2] = 0.0;
  t.lighting = true;
  const double half[3] = {0.5, 0.5, 0.5};
  double pos[3], dir[3];
  light_pos(0.0, 0.0, 0.0, pos);
  if (!t.add_light(pos, 1.0, Z3, half, Z3, Z3, t.fall, NOSPOT)) return;
  light_dir(0.0, 0.0, -1.0, dir);
  t.add_light(Z3, 0.0, dir, Z3, half, t.lspec, FALL1, NOSPOT);
}

void Api::no_lights() {
  if (!t3) return;
  t3->lighting = false;
  t3->nlights = 0;
}

void Api::ambient_light(const double *v, int n) {
  if (!need3d("ambientLight")) return;
  double pos[3], rgb[3];
  light_pos(n == 6 ? v[3] : 0.0, n == 6 ? v[4] : 0.0, n == 6 ? v[5] : 0.0, pos);
  if (!rgbf(v[0], v[1], v[2], rgb)) return;
  t3->add_light(pos, 1.0, Z3, rgb, Z3, Z3, t3->fall, NOSPOT);
}

void Api::directional_light(const double *v) {
  if (!need3d("directionalLight")) return;
  double dir[3], rgb[3];
  light_dir(v[3], v[4], v[5], dir);
  if (!rgbf(v[0], v[1], v[2], rgb)) return;
  t3->add_light(Z3, 0.0, dir, Z3, rgb, t3->lspec, FALL1, NOSPOT);
}

void Api::point_light(const double *v) {
  if (!need3d("pointLight")) return;
  double pos[3], rgb[3];
  light_pos(v[3], v[4], v[5], pos);
  if (!rgbf(v[0], v[1], v[2], rgb)) return;
  t3->add_light(pos, 1.0, Z3, Z3, rgb, t3->lspec, t3->fall, NOSPOT);
}

void Api::spot_light(const double *v) {
  if (!need3d("spotLight")) return;
  double pos[3], dir[3], rgb[3];
  light_pos(v[3], v[4], v[5], pos);
  light_dir(v[6], v[7], v[8], dir);
  if (!rgbf(v[0], v[1], v[2], rgb)) return;
  if (std::isinf(v[9])) {
    raise(Fault::Runtime, E_ARITH);
    return;
  }
  const double spot[2] = {pymax(0.0, py_cos(v[9])), v[10]};
  t3->add_light(pos, 1.0, dir, Z3, rgb, t3->lspec, t3->fall, spot);
}

void Api::light_falloff(double c, double l, double q) {
  if (!need3d("lightFalloff")) return;
  t3->fall[0] = c;
  t3->fall[1] = l;
  t3->fall[2] = q;
}

void Api::light_specular(double r, double g, double b) {
  if (!need3d("lightSpecular")) return;
  rgbf(r, g, b, t3->lspec);
}

void Api::ambient(const double *a, int n, const bool *ints) {
  if (!need3d("ambient")) return;
  int32_t c = calc(a, n, ints);
  if (!ok()) return;
  st_.amb_c = c;
  st_.amb_set = true;
}

void Api::specular(const double *a, int n, const bool *ints) {
  if (!need3d("specular")) return;
  int32_t c = calc(a, n, ints);
  if (!ok()) return;
  st_.spec_c = c;
}

void Api::emissive(const double *a, int n, const bool *ints) {
  if (!need3d("emissive")) return;
  int32_t c = calc(a, n, ints);
  if (!ok()) return;
  st_.emis_c = c;
}

void Api::shininess(double s) {
  if (!need3d("shininess")) return;
  st_.shine = s;
}

void Api::normal(double x, double y, double z) {
  if (!need3d("normal")) return;
  t3->normal[0] = x;
  t3->normal[1] = y;
  t3->normal[2] = z;
  if (shape_open_) shape_normals_ = true;
}

void Api::hint(double which) {
  if (!t3) return;
  int64_t w;
  if (!to_int(which, &w)) return;
  if (w == 2 || w == -2) t3->depth_test = w < 0;
  else if (w == 5 || w == -5) t3->depth_mask = w < 0;
}

// ------------------------------------------------------------------ box and sphere
void Api::box(double w, bool has_hd, double h, double d) {
  if (!need3d("box")) return;
  if (!has_hd) h = d = w;
  const double nx = ((h > 0) != (d > 0)) ? -1.0 : 1.0;
  const double ny = ((w > 0) != (d > 0)) ? -1.0 : 1.0;
  const double nz = ((w > 0) != (h > 0)) ? -1.0 : 1.0;
  const double x1 = -w / 2.0, x2 = w / 2.0, y1 = -h / 2.0, y2 = h / 2.0, z1 = -d / 2.0, z2 = d / 2.0;
  const V3 faces[6][5] = {
      {{0.0, 0.0, -nz}, {x1, y1, z1}, {x1, y2, z1}, {x2, y2, z1}, {x2, y1, z1}},
      {{0.0, 0.0, nz}, {x1, y2, z2}, {x1, y1, z2}, {x2, y1, z2}, {x2, y2, z2}},
      {{nx, 0.0, 0.0}, {x2, y1, z1}, {x2, y2, z1}, {x2, y2, z2}, {x2, y1, z2}},
      {{-nx, 0.0, 0.0}, {x1, y2, z1}, {x1, y1, z1}, {x1, y1, z2}, {x1, y2, z2}},
      {{0.0, -ny, 0.0}, {x2, y1, z1}, {x2, y1, z2}, {x1, y1, z2}, {x1, y1, z1}},
      {{0.0, ny, 0.0}, {x1, y2, z1}, {x1, y2, z2}, {x2, y2, z2}, {x2, y2, z1}},
  };
  Mesh &m = mesh_;
  m.clear();
  for (int f = 0; f < 6; f++) {
    const uint32_t k = (uint32_t)m.verts.size();
    for (int i = 1; i <= 4; i++) {
      m.verts.push_back(faces[f][i]);
      m.norms.push_back(faces[f][0]);
    }
    for (uint32_t i = 0; i < 4; i++) m.idx.push_back(k + i);
    m.contour_end();
    m.face_end();
    m.edge(k, k + 1);
    m.edge(k + 1, k + 2);
    m.edge(k + 2, k + 3);
    m.edge(k + 3, k);
  }
  double M[16];
  mv(M);
  t3->mesh(M, m, true, true, true, false);
}

void Api::sphere_detail(double u, bool has_v, double v) {
  if (!need3d("sphereDetail")) return;
  int64_t iu, iv;
  if (!to_int(u, &iu)) return;
  if (has_v) {
    if (!to_int(v, &iv)) return;
  } else {
    iv = iu;
  }
  iu = imax(3, imin(iu, 200));
  iv = imax(2, imin(iv, 200));
  if (iu != t3->su || iv != t3->sv) t3->sphere_tables((int)iu, (int)iv);
}

void Api::sphere(double r) {
  if (!need3d("sphere")) return;
  Three &t = *t3;
  if (t.su < 3 || t.sv < 2) {
    sphere_detail(30, false, 0.0);
    if (!ok()) return;
  }
  const int du = t.su, dv = t.sv;
  Mesh &m = mesh_;
  m.clear();
  auto vert = [&](double x, double y, double z, double nx, double ny, double nz) {
    m.verts.push_back({x, y, z});
    m.norms.push_back({nx, ny, nz});
  };
  auto tri = [&](uint32_t a, uint32_t b, uint32_t c) {
    m.idx.push_back(a);
    m.idx.push_back(b);
    m.idx.push_back(c);
    m.contour_end();
    m.face_end();
  };
  const std::vector<float> &sx = t.stx, &sy = t.sty, &sz = t.stz;
  for (int i = 0; i < du; i++) vert(0.0, r, 0.0, 0.0, 1.0, 0.0);
  for (int i = 0; i < du; i++) vert(r * sx[i], r * sy[i], r * sz[i], sx[i], sy[i], sz[i]);
  vert(r * sx[0], r * sy[0], r * sz[0], sx[0], sy[0], sz[0]);
  uint32_t vert0 = du;
  for (int i = 0; i < du; i++) {
    const uint32_t i1 = vert0 + i, i0 = vert0 + i - du;
    tri(i1, i0, i1 + 1);
    m.edge(i0, i1);
    m.edge(i1, i1 + 1);
  }
  int offset = 0;
  for (int j = 2; j < dv; j++) {
    offset += du;
    vert0 = (uint32_t)m.verts.size();
    for (int i = 0; i < du; i++) {
      const int k = offset + i;
      vert(r * sx[k], r * sy[k], r * sz[k], sx[k], sy[k], sz[k]);
    }
    const uint32_t vert1 = (uint32_t)m.verts.size();
    vert(r * sx[offset], r * sy[offset], r * sz[offset], sx[offset], sy[offset], sz[offset]);
    for (int i = 0; i < du; i++) {
      const uint32_t i1 = vert0 + i, i0 = vert0 + i - du - 1;
      tri(i1, i0, i0 + 1);
      tri(i1, i0 + 1, i1 + 1);
      m.edge(i0, i1);
      m.edge(i1, i1 + 1);
      m.edge(i0 + 1, i1);
    }
    tri(vert1, vert1 - du, vert1 - 1);
  }
  for (int i = 0; i < du; i++) vert(0.0, -r, 0.0, 0.0, -1.0, 0.0);
  for (int i = 0; i < du; i++) {
    const uint32_t i0 = vert0 + i, i1 = vert0 + i + du + 1;
    tri(i1, i0, i0 + 1);
    m.edge(i0, i1);
  }
  double M[16];
  mv(M);
  t.mesh(M, m, true, true, true, false);
}

// ------------------------------------------------------------------ 2D shapes in 3D
void Api::fill3(const Contours &c, bool lit) {
  Mesh &m = mesh_;
  m.clear();
  for (int k = 0; k < c.count(); k++) {
    const uint32_t n = c.size(k);
    if (n < 2) continue;
    const Pt *p = c.at(k);
    for (uint32_t i = 0; i < n; i++) {
      m.idx.push_back((uint32_t)m.verts.size());
      m.verts.push_back({p[i].x, p[i].y, 0.0});
    }
    m.contour_end();
  }
  if (m.cend.empty()) return;
  m.face_end();
  // newell() of the first contour: only its z matters
  double nz = 0.0;
  const uint32_t n0 = m.cend[0];
  for (uint32_t i = 0; i < n0; i++) {
    const V3 &a = m.verts[m.idx[i]], &b = m.verts[m.idx[(i + 1) % n0]];
    nz += (a.x - b.x) * (a.y + b.y);
  }
  m.face_norms.push_back({0.0, 0.0, nz >= 0.0 ? 1.0 : -1.0});
  double M[16];
  mv(M);
  t3->mesh(M, m, lit, true, false, !lit);
}

void Api::stroke3(const V3 *pts, int n, bool closed) {
  double M[16];
  mv(M);
  if (n == 1) {
    t3->points(M, pts, 1);
    return;
  }
  Mesh &m = mesh_;
  m.clear();
  m.verts.assign(pts, pts + n);
  for (int i = 0; i + 1 < n; i++) m.edge(i, i + 1);
  if (closed && n > 2) m.edge(n - 1, 0);
  t3->mesh(M, m, true, false, true, false);
}

// ------------------------------------------------------------------ beginShape() in 3D
bool Api::shape_has_z() const {
  auto has = [](const Item &it) {
    if ((it.k == 'v' || it.k == 'c') && it.z[0] != 0.0) return true;
    if (it.k == 'b' && it.zset && (it.z[0] != 0.0 || it.z[1] != 0.0 || it.z[2] != 0.0)) return true;
    if (it.k == 'q' && it.zset && (it.z[0] != 0.0 || it.z[1] != 0.0)) return true;
    return false;
  };
  for (const Item &it : items_)
    if (has(it)) return true;
  for (const Item &it : ctr_items_)
    if (has(it)) return true;
  return false;
}

bool Api::bez3(const V3 &p0, const V3 &p1, const V3 &p2, const V3 &p3, std::vector<V3> &out) {
  auto dist = [](const V3 &a, const V3 &b) {
    const double dx = b.x - a.x, dy = b.y - a.y, dz = b.z - a.z;
    return std::sqrt(dx * dx + dy * dy + dz * dz);
  };
  const double ln = dist(p0, p1) + dist(p1, p2) + dist(p2, p3);
  int64_t v;
  if (!to_int(ln * mscale() / 3, &v)) return false;
  const int n = (int)imax(4, imin(64, v + (v < INT64_MAX ? 1 : 0)));
  if (out.size() + (size_t)n > MAX_POINTS) {
    raise(Fault::Memory, "a shape has too many points for the VM");
    return false;
  }
  for (int i = 1; i <= n; i++) {
    const double t = (double)i / n, u = 1 - t;
    const double a = u * u * u, b = 3 * u * u * t, c = 3 * u * t * t, d = t * t * t;
    out.push_back({a * p0.x + b * p1.x + c * p2.x + d * p3.x, a * p0.y + b * p1.y + c * p2.y + d * p3.y,
                   a * p0.z + b * p1.z + c * p2.z + d * p3.z});
  }
  return true;
}

bool Api::flatten3(const std::vector<Item> &src, size_t from, size_t to, std::vector<V3> &pts,
                   std::vector<V3> &nrm) {
  const size_t base = pts.size();
  std::vector<Item> curve;
  std::vector<V3> seg;
  auto flush = [&]() {
    if (curve.size() >= 4) {
      const double s = (1 - curve_tightness_) / 2;
      pts.push_back({curve[1].v[0], curve[1].v[1], curve[1].z[0]});
      nrm.push_back({curve[1].n[0], curve[1].n[1], curve[1].n[2]});
      for (size_t i = 0; i + 3 < curve.size(); i++) {
        const Item &q0 = curve[i], &q1 = curve[i + 1], &q2 = curve[i + 2], &q3 = curve[i + 3];
        const V3 a0 = {q0.v[0], q0.v[1], q0.z[0]}, a1 = {q1.v[0], q1.v[1], q1.z[0]},
                 a2 = {q2.v[0], q2.v[1], q2.z[0]}, a3 = {q3.v[0], q3.v[1], q3.z[0]};
        const V3 c1 = {a1.x + s * (a2.x - a0.x) / 3, a1.y + s * (a2.y - a0.y) / 3, a1.z + s * (a2.z - a0.z) / 3};
        const V3 c2 = {a2.x - s * (a3.x - a1.x) / 3, a2.y - s * (a3.y - a1.y) / 3, a2.z - s * (a3.z - a1.z) / 3};
        seg.clear();
        if (!bez3(a1, c1, c2, a2, seg)) return false;
        for (const V3 &p : seg) {
          pts.push_back(p);
          nrm.push_back({q2.n[0], q2.n[1], q2.n[2]});
        }
      }
    }
    curve.clear();
    return true;
  };
  for (size_t j = from; j < to; j++) {
    const Item &it = src[j];
    if (it.k == 'c') {
      curve.push_back(it);
      continue;
    }
    if (!flush()) return false;
    if (it.k == 'v') {
      pts.push_back({it.v[0], it.v[1], it.z[0]});
      nrm.push_back({it.n[0], it.n[1], it.n[2]});
    } else if ((it.k == 'b' || it.k == 'q') && pts.size() > base) {
      const V3 p0 = pts.back();
      const V3 n0 = nrm.back();
      seg.clear();
      if (it.k == 'b') {
        const double z1 = it.zset ? it.z[0] : 0.0, z2 = it.zset ? it.z[1] : 0.0, z3 = it.zset ? it.z[2] : 0.0;
        if (!bez3(p0, {it.v[0], it.v[1], z1}, {it.v[2], it.v[3], z2}, {it.v[4], it.v[5], z3}, seg)) return false;
      } else {
        const double cz = it.zset ? it.z[0] : 0.0, z3 = it.zset ? it.z[1] : 0.0;
        const double cx = it.v[0], cy = it.v[1], x3 = it.v[2], y3 = it.v[3];
        const double k = 2.0 / 3.0;
        const V3 c1 = {p0.x + k * (cx - p0.x), p0.y + k * (cy - p0.y), p0.z + k * (cz - p0.z)};
        const V3 c2 = {x3 + k * (cx - x3), y3 + k * (cy - y3), z3 + k * (cz - z3)};
        if (!bez3(p0, c1, c2, {x3, y3, z3}, seg)) return false;
      }
      for (const V3 &p : seg) {
        pts.push_back(p);
        nrm.push_back(n0);
      }
    }
    if (pts.size() > MAX_POINTS) {
      raise(Fault::Memory, "a shape has too many points for the VM");
      return false;
    }
  }
  return flush();
}

static V3 tri_normal(const V3 &p0, const V3 &p1, const V3 &p2) {
  const double ax = p2.x - p1.x, ay = p2.y - p1.y, az = p2.z - p1.z;
  const double bx = p0.x - p1.x, by = p0.y - p1.y, bz = p0.z - p1.z;
  const double nx = ay * bz - by * az, ny = az * bx - bz * ax, nz = ax * by - bx * ay;
  const double d = std::sqrt(nx * nx + ny * ny + nz * nz);
  if (d == 0.0) return {0.0, 0.0, 0.0};
  return {nx / d, ny / d, nz / d};
}

void Api::end_shape3d(bool close) {
  const int64_t kind = shape_kind_;
  const bool autonorm = !shape_normals_;
  double M[16];
  mv(M);
  Mesh &m = mesh_;
  m.clear();
  if (kind == 3 || kind == 5 || kind == 9 || kind == 10 || kind == 11 || kind == 17 || kind == 18) {
    std::vector<V3> rec;
    for (const Item &it : items_)
      if (it.k == 'v' || it.k == 'c') {
        m.verts.push_back({it.v[0], it.v[1], it.z[0]});
        rec.push_back({it.n[0], it.n[1], it.n[2]});
      }
    const int n = (int)m.verts.size();
    if (kind == 3) {
      t3->points(M, m.verts.data(), n);
      return;
    }
    if (kind == 5) {
      for (int i = 0; i + 1 < n; i += 2) m.edge(i, i + 1);
      t3->mesh(M, m, true, false, true, false);
      return;
    }
    std::vector<uint32_t> tris;
    auto face = [&](std::initializer_list<uint32_t> f) {
      for (uint32_t i : f) m.idx.push_back(i);
      m.contour_end();
      m.face_end();
    };
    auto t3n = [&](uint32_t a, uint32_t b, uint32_t c) {
      tris.push_back(a);
      tris.push_back(b);
      tris.push_back(c);
    };
    if (kind == 9) {
      for (int i = 0; i < n / 3; i++) {
        const uint32_t a = 3 * i, b = a + 1, c = a + 2;
        face({a, b, c});
        m.edge(a, b);
        m.edge(b, c);
        m.edge(c, a);
        t3n(a, b, c);
      }
    } else if (kind == 11) {
      for (int i = 1; i < n - 1; i++) {
        face({0u, (uint32_t)i, (uint32_t)i + 1});
        m.edge(0, i);
        m.edge(i, i + 1);
        m.edge(i + 1, 0);
        t3n(0, i, i + 1);
      }
    } else if (kind == 10) {
      for (int i = 1; i < n - 1; i++) {
        const uint32_t u = i;
        if (i % 2 == 0) face({u, u - 1, u + 1});
        else face({u, u + 1, u - 1});
        const uint32_t e1 = i % 2 == 0 ? u - 1 : u + 1, e2 = i % 2 == 0 ? u + 1 : u - 1;
        m.edge(u, e1);
        m.edge(e1, e2);
        m.edge(e2, u);
        if (i % 2 == 1) t3n(u - 1, u, u + 1);
        else t3n(u + 1, u, u - 1);
      }
    } else if (kind == 17) {
      for (int q = 0; q < n / 4; q++) {
        const uint32_t a = 4 * q, b = a + 1, c = a + 2, d = a + 3;
        face({a, b, c, d});
        m.edge(a, b);
        m.edge(b, c);
        m.edge(c, d);
        m.edge(d, a);
        t3n(a, b, c);
        t3n(c, d, a);
      }
    } else {
      for (int q = 1; q < n / 2; q++) {
        const uint32_t a = 2 * (q - 1), b = a + 1, c = 2 * q + 1, d = 2 * q;
        face({b, c, d, a});
        m.edge(a, b);
        m.edge(b, c);
        m.edge(c, d);
        m.edge(d, a);
        t3n(a, b, d);
        t3n(d, b, c);
      }
    }
    if (autonorm) {
      m.norms.assign(n, V3{0.0, 0.0, 1.0});
      for (size_t k = 0; k + 2 < tris.size(); k += 3) {
        const V3 nn = tri_normal(m.verts[tris[k]], m.verts[tris[k + 1]], m.verts[tris[k + 2]]);
        m.norms[tris[k]] = m.norms[tris[k + 1]] = m.norms[tris[k + 2]] = nn;
      }
    } else {
      m.norms = rec;
    }
    t3->mesh(M, m, true, true, true, false);
    return;
  }
  // POLYGON: one outline, holes from beginContour()/endContour()
  std::vector<V3> &verts = m.verts, &norms = m.norms;
  for (const SavedContour &c : ctrs_)
    if (!c.hole && !flatten3(ctr_items_, c.ia, c.ib, verts, norms)) return;
  if (!flatten3(items_, 0, items_.size(), verts, norms)) return;
  std::vector<std::vector<V3>> hp, hn;
  for (const SavedContour &c : ctrs_) {
    if (!c.hole) continue;
    hp.emplace_back();
    hn.emplace_back();
    if (!flatten3(ctr_items_, c.ia, c.ib, hp.back(), hn.back())) return;
  }
  const int n = (int)verts.size();
  bool holes_in = false;
  if (n >= 3) {
    double nx = 0.0, ny = 0.0, nz = 0.0;
    for (int i = 0; i < n; i++) {
      const V3 &a = verts[i], &b = verts[(i + 1) % n];
      nx += (a.y - b.y) * (a.z + b.z);
      ny += (a.z - b.z) * (a.x + b.x);
      nz += (a.x - b.x) * (a.y + b.y);
    }
    const double nl = std::sqrt(nx * nx + ny * ny + nz * nz);
    if (nl > 0.0) {
      const double ux = nx / nl, uy = ny / nl, uz = nz / nl;
      const double x0 = verts[0].x, y0 = verts[0].y, z0 = verts[0].z;
      double ext = 0.0;
      for (int i = 0; i < n; i++) {
        double e = std::fabs(verts[i].x - x0);
        const double e2 = std::fabs(verts[i].y - y0), e3 = std::fabs(verts[i].z - z0);
        if (e2 > e) e = e2;
        if (e3 > e) e = e3;
        if (i == 0 || e > ext) ext = e;
      }
      bool planar = true;
      for (int i = 0; i < n && planar; i++) {
        const double dd = std::fabs((verts[i].x - x0) * ux + (verts[i].y - y0) * uy + (verts[i].z - z0) * uz);
        if (!(dd <= 1e-6 * ext + 1e-9)) planar = false;
      }
      if (planar) {
        for (int i = 0; i < n; i++) m.idx.push_back(i);
        m.contour_end();
        for (size_t h = 0; h < hp.size(); h++) {
          const uint32_t k = (uint32_t)verts.size();
          verts.insert(verts.end(), hp[h].begin(), hp[h].end());
          norms.insert(norms.end(), hn[h].begin(), hn[h].end());
          for (uint32_t i = 0; i < hp[h].size(); i++) m.idx.push_back(k + i);
          m.contour_end();
        }
        holes_in = true;
        m.face_end();
        if (autonorm) norms.assign(verts.size(), V3{ux, uy, uz});
      } else {
        std::vector<uint32_t> tris;
        for (int k = 1; k < n - 1; k++) {
          m.idx.push_back(0);
          m.idx.push_back(k);
          m.idx.push_back(k + 1);
          m.contour_end();
          m.face_end();
        }
        if (autonorm) {
          norms.assign(n, V3{0.0, 0.0, 1.0});
          for (int k = 1; k < n - 1; k++) {
            const V3 nn = tri_normal(verts[0], verts[k], verts[k + 1]);
            norms[0] = norms[k] = norms[k + 1] = nn;
          }
        }
      }
    }
  }
  for (int i = 0; i + 1 < n; i++) m.edge(i, i + 1);
  if (close && n > 2) m.edge(n - 1, 0);
  uint32_t k = n;
  for (size_t h = 0; h < hp.size(); h++) {
    const uint32_t mm = (uint32_t)hp[h].size();
    if (!holes_in) {
      verts.insert(verts.end(), hp[h].begin(), hp[h].end());
      norms.insert(norms.end(), hn[h].begin(), hn[h].end());
    }
    if (mm > 1)
      for (uint32_t i = 0; i < mm; i++) m.edge(k + i, k + (i + 1) % mm);
    k += mm;
  }
  t3->mesh(M, m, true, true, true, false);
}

// ------------------------------------------------------------------ blendMode() and filter()
void Api::blend_mode(double md) {
  int64_t m;
  if (!to_int(md, &m)) return;
  if (!(m == 0 || m == 1 || m == 2 || m == 4 || m == 8 || m == 16 || m == 32 || m == 64 || m == 128 || m == 256)) {
    raise(Fault::Runtime, "blendMode() takes BLEND, ADD, SUBTRACT, LIGHTEST, DARKEST, DIFFERENCE, EXCLUSION, "
                          "MULTIPLY, SCREEN or REPLACE");
    return;
  }
  st_.blend = (int)m;
  canvas.blend = (int)m;
}

void Api::filter(double kind_d, bool has_param, double param) {
  int64_t kind;
  if (!to_int(kind_d, &kind)) return;
  uint8_t *buf = canvas.buf;
  const int n = W * H;
  charged += n >> 2;
  uint8_t tab[256];
  auto apply = [&]() {
    for (int i = 0; i < n; i++) buf[i] = tab[buf[i]];
  };
  if (kind == 16) {
    int64_t t;
    if (!to_int(f32(has_param ? param : 0.5) * 255, &t)) return;
    for (int v = 0; v < 256; v++) tab[v] = v < t ? 0 : 255;
    apply();
  } else if (kind == 12 || kind == 14) {
    if (has_param) raise(Fault::Runtime, "filter(GRAY) and filter(OPAQUE) take no number");
  } else if (kind == 13) {
    if (has_param) {
      raise(Fault::Runtime, "Use filter(INVERT) instead of filter(INVERT, param)");
      return;
    }
    for (int v = 0; v < 256; v++) tab[v] = (uint8_t)(255 - v);
    apply();
  } else if (kind == 15) {
    if (!has_param) {
      raise(Fault::Runtime, "Use filter(POSTERIZE, int levels) instead of filter(POSTERIZE)");
      return;
    }
    int64_t lv;
    if (!to_int(param, &lv)) return;
    if (lv < 2 || lv > 255) {
      raise(Fault::Runtime, "Levels must be between 2 and 255 for filter(POSTERIZE, levels)");
      return;
    }
    for (int v = 0; v < 256; v++) tab[v] = (uint8_t)((((v * lv) >> 8) * 255) / (lv - 1));
    apply();
  } else if (kind == 17 || kind == 18) {
    if (has_param) {
      raise(Fault::Runtime, "ERODE and DILATE take no number");
      return;
    }
    std::vector<uint8_t> src(buf, buf + n);
    for (int y = 0; y < H; y++) {
      const int b = y * W, up = y > 0 ? b - W : b, dn = y < H - 1 ? b + W : b;
      for (int x = 0; x < W; x++) {
        const int k = b + x, l = x > 0 ? k - 1 : k, r = x < W - 1 ? k + 1 : k;
        int v = src[k];
        const int c[4] = {src[l], src[r], src[up + x], src[dn + x]};
        for (int q = 0; q < 4; q++) v = kind == 17 ? (c[q] < v ? c[q] : v) : (c[q] > v ? c[q] : v);
        buf[k] = (uint8_t)v;
      }
    }
    charged += n;
  } else if (kind == 11) {
    double r = has_param ? param : 1.0;
    r = pymin(r, f32(f32(64.0) / 3.5));
    int64_t radius;
    if (!to_int(f32(r * 3.5), &radius)) return;
    radius = imax(1, imin(248, radius));
    const int R = (int)radius, size = (1 + R) << 1;
    std::vector<int64_t> kern(size, 0);
    int ri = R - 1;
    for (int i = 1; i < R; i++) {
      kern[R + i] = kern[ri] = (int64_t)ri * ri;
      ri--;
    }
    kern[R] = (int64_t)R * R;
    std::vector<uint8_t> src(buf, buf + n);
    std::vector<int64_t> tmp(n, 0);
    for (int y = 0; y < H; y++) {
      const int yi = y * W;
      for (int x = 0; x < W; x++) {
        int64_t c = 0, s = 0;
        int read = x - R, bk0 = 0;
        if (read < 0) {
          bk0 = -read;
          read = 0;
        }
        for (int i = bk0; i < size; i++) {
          if (read >= W) break;
          c += kern[i] * src[read + yi];
          s += kern[i];
          read++;
        }
        tmp[yi + x] = s ? floordiv(c, s) : c;
      }
    }
    int ym = -R;
    for (int y = 0; y < H; y++) {
      const int yi = y * W;
      for (int x = 0; x < W; x++) {
        int64_t c = 0, s = 0;
        int bk0, rr, read;
        if (ym < 0) {
          bk0 = rr = -ym;
          read = x;
        } else {
          bk0 = 0;
          rr = ym;
          read = x + ym * W;
        }
        for (int i = bk0; i < size; i++) {
          if (rr >= H) break;
          c += kern[i] * tmp[read];
          s += kern[i];
          rr++;
          read += W;
        }
        buf[yi + x] = (uint8_t)(s ? floordiv(c, s) : c);
      }
      ym++;
    }
    charged += (uint64_t)W * H * size >> 2;
  } else {
    raise(Fault::Runtime, "filter() takes THRESHOLD, GRAY, INVERT, POSTERIZE, BLUR, ERODE, DILATE or OPAQUE");
  }
}

}  // namespace pde
