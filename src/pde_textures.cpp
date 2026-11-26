#include "pde_fp.h"  // first: no fused multiply-add (see the header)
#include "pde_textures.h"

#include <cmath>

#include "pde_font.h"

namespace pde {

const char *const TEX_NAMES[TEX_COUNT] = {
    "flat", "noise", "gated", "cloud", "hatch", "lines", "vlines", "cross", "dots",
    "checker", "brick", "weave", "wavy", "stipple", "halftone", "vgradient", "hgradient", "radial"};

static const uint8_t BAYER4[4][4] = {{0, 8, 2, 10}, {12, 4, 14, 6}, {3, 11, 1, 9}, {15, 7, 13, 5}};

int tex_default_p(int t) {
  switch (t) {
    case TEX_GATED: case TEX_CLOUD: return 12;
    case TEX_CHECKER: case TEX_WEAVE: case TEX_WAVY: return 8;
    case TEX_BRICK: return 6;
    case TEX_STIPPLE: return 1;
    case TEX_HALFTONE: return 6;
    default: return 6;
  }
}

uint32_t mix32(uint32_t x) {
  x ^= x >> 16;
  x *= 0x7FEB352Du;
  x ^= x >> 15;
  x *= 0x846CA68Bu;
  x ^= x >> 16;
  return x;
}

uint32_t h4(uint32_t a, uint32_t b, uint32_t c, uint32_t d) {
  uint32_t h = mix32(a + 0x9E3779B9u);
  h = mix32(h ^ b);
  h = mix32(h ^ c);
  return mix32(h ^ d);
}

static inline bool bayer(int level, int64_t xl, int64_t yl) { return level > BAYER4[yl & 3][xl & 3]; }

static int value_noise(int64_t xl, int64_t yl, int s, uint32_t seed) {
  int64_t gx = floordiv(xl, s), gy = floordiv(yl, s);
  int64_t fx = xl - gx * s, fy = yl - gy * s;
  int64_t v00 = h4(seed, (uint32_t)gx, (uint32_t)gy, 0x51ED) & 255;
  int64_t v10 = h4(seed, (uint32_t)(gx + 1), (uint32_t)gy, 0x51ED) & 255;
  int64_t v01 = h4(seed, (uint32_t)gx, (uint32_t)(gy + 1), 0x51ED) & 255;
  int64_t v11 = h4(seed, (uint32_t)(gx + 1), (uint32_t)(gy + 1), 0x51ED) & 255;
  return (int)((v00 * (s - fx) * (s - fy) + v10 * fx * (s - fy) + v01 * (s - fx) * fy + v11 * fx * fy) / (s * s));
}

static bool motif(int tex, int p, int64_t xl, int64_t yl) {
  switch (tex) {
    case TEX_CHECKER: return ((floordiv(xl, p) + floordiv(yl, p)) & 1) == 0;
    case TEX_BRICK: {
      int64_t row = floordiv(yl, p);
      return floormod(yl, p) == 0 || floormod(xl + ((row & 1) ? p : 0), 2 * p) == 0;
    }
    case TEX_WEAVE: {
      int q = p / 3 > 2 ? p / 3 : 2;
      return (((floordiv(xl, p) + floordiv(yl, p)) & 1) == 0) ? floormod(yl, q) == 0 : floormod(xl, q) == 0;
    }
    case TEX_WAVY: {
      int amp = p / 3 > 1 ? p / 3 : 1;
      int64_t off = ((int64_t)amp * SIN1024[floormod(floordiv(xl * 90, p), 360)] + 512) >> 10;
      return floormod(yl + off, p) == 0;
    }
  }
  return false;
}

bool inked(int tex, int p, int level, uint32_t seed, int64_t xl, int64_t yl) {
  if (level <= 0) return false;
  switch (tex) {
    case TEX_FLAT: return bayer(level, xl, yl);
    case TEX_NOISE: return (int)(h4(seed, (uint32_t)xl, (uint32_t)yl, 0) & 15) < level;
    case TEX_GATED:
      return value_noise(xl, yl, p, seed) >= 128 &&
             (int)(h4(seed ^ 0x5BD1E995u, (uint32_t)xl, (uint32_t)yl, 0) & 15) < level;
    case TEX_CLOUD: return ((value_noise(xl, yl, p, seed) * level) >> 8) > BAYER4[yl & 3][xl & 3];
    case TEX_HATCH: case TEX_LINES: case TEX_VLINES: case TEX_CROSS: case TEX_DOTS: {
      int64_t w = ((int64_t)level * p + 15) >> 4;
      if (tex == TEX_HATCH) return floormod(xl + yl, p) < w;
      if (tex == TEX_LINES) return floormod(yl, p) < w;
      if (tex == TEX_VLINES) return floormod(xl, p) < w;
      if (tex == TEX_CROSS) return floormod(xl + yl, p) < w || floormod(xl - yl, p) < w;
      int64_t dx = 2 * floormod(xl, p) + 1 - p, dy = 2 * floormod(yl, p) + 1 - p;
      return (dx * dx + dy * dy) * 16 < (int64_t)level * 2 * p * p;
    }
    case TEX_CHECKER: case TEX_BRICK: case TEX_WEAVE: case TEX_WAVY:
      if (motif(tex, p, xl, yl)) return level >= 8 || bayer(level * 2, xl, yl);
      return level > 8 && bayer((level - 8) * 2, xl, yl);
    case TEX_STIPPLE: {
      int k = p > 1 ? p : 1;
      int64_t sx = floordiv(xl, k) + (((int64_t)seed * 7) & 15), sy = floordiv(yl, k) + (((int64_t)seed * 11) & 15);
      return level * 16 > BLUE16[(sy & 15) * 16 + (sx & 15)];
    }
    case TEX_HALFTONE: {
      int64_t u = xl + yl, v = xl - yl;
      int64_t dx = 2 * floormod(u, p) + 1 - p, dy = 2 * floormod(v, p) + 1 - p;
      return (dx * dx + dy * dy) * 16 < (int64_t)level * 2 * p * p;
    }
  }
  return bayer(level, xl, yl);
}

// A shift of any size, reduced modulo a period of the pattern (x and y alike): 2^32 for the hashes
// (of the pixel and of its lattice cell, so times p), 4 for Bayer, and the motif periods.
static int64_t reduce_shift(double v, int p) {
  if (std::fabs(v) <= 1099511627776.0) return (int64_t)v;   // 2^40: plenty of headroom
  int q = p / 3 > 2 ? p / 3 : 2;
  double m = 4294967296.0 * 64.0 * (double)p * (double)q;     // <= 2^49, exact
  double r = std::fmod(v, m);
  if (r < 0) r += m;
  return (int64_t)r;
}

void Painter::init(int t, int pp, int lv, int lv2, int64_t seed_bits, double ox_d, double oy_d) {
  tex = t;
  p = pp;
  level = lv;
  level2 = lv2;
  seed = (uint32_t)(seed_bits & 0xFFFF);
  ox = reduce_shift(ox_d, p > 0 ? p : 1);
  oy = reduce_shift(oy_d, p > 0 ? p : 1);
}

bool Painter::row_ok(int y) const {
  if (tex != TEX_RADIAL) return true;
  double gx0 = 0, gy0 = 0, gx1 = W, gy1 = 128;
  if (has_box) {
    gx0 = bx0;
    gy0 = by0;
    gx1 = bx1;
    gy1 = by1;
  }
  double cx = (gx0 + gx1 - 1) / 2, cy = (gy0 + gy1 - 1) / 2;
  return !std::isinf(std::pow((double)y - cy, 2.0)) && !std::isinf(std::pow(0.0 - cx, 2.0)) &&
         !std::isinf(std::pow((double)(W - 1) - cx, 2.0));
}

void Painter::span(int y, int x0, int x1, uint8_t *out) const {
  if (x1 <= x0) return;
  const int64_t yl = (int64_t)y - oy;
  const uint8_t *b = BAYER4[yl & 3];
  if (tex_is_gradient(tex)) {
    double gx0 = 0, gy0 = 0, gx1 = W, gy1 = 128;
    if (has_box) {
      gx0 = bx0;
      gy0 = by0;
      gx1 = bx1;
      gy1 = by1;
    }
    const int l1 = level, l2 = level2;
    double bw = pymax(1.0, gx1 - gx0 - 1), bh = pymax(1.0, gy1 - gy0 - 1);
    if (tex == TEX_VGRADIENT) {
      double t = pymin(1.0, pymax(0.0, ((double)y - gy0) / bh));
      int lv = (int)(l1 + (l2 - l1) * t + 0.5);
      for (int x = x0; x < x1; x++) out[x] = lv > b[((int64_t)x - ox) & 3] ? 0 : 255;
      return;
    }
    double cx = (gx0 + gx1 - 1) / 2, cy = (gy0 + gy1 - 1) / 2;
    double r = pymax(1.0, pymax(gx1 - gx0, gy1 - gy0) / 2);
    double dy2 = std::pow((double)y - cy, 2.0);
    for (int x = x0; x < x1; x++) {
      double t;
      if (tex == TEX_HGRADIENT) t = pymin(1.0, pymax(0.0, ((double)x - gx0) / bw));
      else t = pymin(1.0, std::pow(std::pow((double)x - cx, 2.0) + dy2, 0.5) / r);
      int lv = (int)(l1 + (l2 - l1) * t + 0.5);
      out[x] = lv > b[((int64_t)x - ox) & 3] ? 0 : 255;
    }
    return;
  }
  if (level <= 0) {
    for (int x = x0; x < x1; x++) out[x] = 255;
    return;
  }
  if (level >= 16 && (tex == TEX_FLAT || tex == TEX_NOISE || tex == TEX_CHECKER || tex == TEX_BRICK ||
                      tex == TEX_WEAVE || tex == TEX_WAVY || tex == TEX_STIPPLE)) {
    for (int x = x0; x < x1; x++) out[x] = 0;
    return;
  }
  if (tex == TEX_FLAT) {
    for (int x = x0; x < x1; x++) out[x] = level > b[((int64_t)x - ox) & 3] ? 0 : 255;
    return;
  }
  if (tex == TEX_CLOUD || tex == TEX_GATED) {
    // textures._value_row: the lattice hashed once per cell
    const int s = p;
    const int64_t gy = floordiv(yl, s), fy = yl - gy * s, ss = (int64_t)s * s;
    int64_t cgx = INT64_MIN;
    int64_t v00 = 0, v01 = 0, v10 = 0, v11 = 0;
    for (int x = x0; x < x1; x++) {
      int64_t xl = (int64_t)x - ox;
      int64_t gx = floordiv(xl, s), fx = xl - gx * s;
      if (gx != cgx) {
        if (gx == cgx + 1) {
          v00 = v10;
          v01 = v11;
        } else {
          v00 = h4(seed, (uint32_t)gx, (uint32_t)gy, 0x51ED) & 255;
          v01 = h4(seed, (uint32_t)gx, (uint32_t)(gy + 1), 0x51ED) & 255;
        }
        v10 = h4(seed, (uint32_t)(gx + 1), (uint32_t)gy, 0x51ED) & 255;
        v11 = h4(seed, (uint32_t)(gx + 1), (uint32_t)(gy + 1), 0x51ED) & 255;
        cgx = gx;
      }
      int64_t vn = (v00 * (s - fx) * (s - fy) + v10 * fx * (s - fy) + v01 * (s - fx) * fy + v11 * fx * fy) / ss;
      bool hit;
      if (tex == TEX_CLOUD) hit = ((vn * level) >> 8) > b[xl & 3];
      else hit = vn >= 128 && (int)(h4(seed ^ 0x5BD1E995u, (uint32_t)xl, (uint32_t)yl, 0) & 15) < level;
      out[x] = hit ? 0 : 255;
    }
    return;
  }
  for (int x = x0; x < x1; x++) out[x] = inked(tex, p, level, seed, (int64_t)x - ox, yl) ? 0 : 255;
}

}  // namespace pde
