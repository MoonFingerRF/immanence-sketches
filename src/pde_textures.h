// Textures: 1-bit pen patterns for filled shapes (an extension to Processing), bit for bit
// with python/immanence/pde/textures.py and python/immanence/ink.py (the nine old textures).
#pragma once
#include <cstdint>

#include "pde_pyops.h"

namespace pde {

enum Tex : int8_t {
  TEX_NONE = -1,
  TEX_FLAT, TEX_NOISE, TEX_GATED, TEX_CLOUD, TEX_HATCH, TEX_LINES, TEX_VLINES, TEX_CROSS, TEX_DOTS,  // OLD
  TEX_CHECKER, TEX_BRICK, TEX_WEAVE, TEX_WAVY, TEX_STIPPLE, TEX_HALFTONE,                          // NEW
  TEX_VGRADIENT, TEX_HGRADIENT, TEX_RADIAL,                                                          // GRADIENTS
  TEX_COUNT
};
extern const char *const TEX_NAMES[TEX_COUNT];
inline bool tex_is_gradient(int t) { return t >= TEX_VGRADIENT; }
int tex_default_p(int t);
inline int level_of(int g) { return ((255 - g) * 16 + 127) / 255; }

// ink.py primitives
uint32_t mix32(uint32_t x);
uint32_t h4(uint32_t a, uint32_t b, uint32_t c, uint32_t d);
// textures.inked: is the layer-local pixel (xl, yl) ink? (not for gradients)
bool inked(int tex, int p, int level, uint32_t seed, int64_t xl, int64_t yl);

// What a textured fill paints: 0 (ink) or 255 (paper) per pixel. textures.Painter, computed per
// span instead of per 240-pixel row (every pixel is independent, so the bytes are the same).
struct Painter {
  int tex = TEX_FLAT, p = 6, level = 0, level2 = 0;
  uint32_t seed = 0;          // already & 0xFFFF
  int64_t ox = 0, oy = 0;     // layer shift (reduced modulo the texture's period when huge)
  bool has_box = false;
  double bx0 = 0, by0 = 0, bx1 = 0, by1 = 0;   // gradient box (ints as doubles)

  // `ox_d`, `oy_d`: floor(m[4] + 0.5), floor(m[5] + 0.5) as exact doubles (finite)
  void init(int tex, int p, int level, int level2, int64_t seed_bits, double ox_d, double oy_d);
  // false when Painter.row(y) raises in Python: a radial gradient whose squared distances
  // overflow (float ** 2 -> OverflowError), which only a box ~1e154 px away can cause
  bool row_ok(int y) const;
  // out[x] for x in [x0, x1) of row y (0 <= x0 <= x1 <= W, 0 <= y < H)
  void span(int y, int x0, int x1, uint8_t *out) const;
  uint8_t pixel(int x, int y) const {
    uint8_t row[W];
    span(y, x, x + 1, row);
    return row[x];
  }
};

}  // namespace pde
