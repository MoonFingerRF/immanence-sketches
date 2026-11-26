#include "pde_fp.h"  // first: no fused multiply-add (see the header)
// noise(), noiseDetail(), noiseSeed(): Processing's Perlin noise, bit for bit in Java float.
// SPDX-License-Identifier: LGPL-2.1-only
// This file follows Processing's PApplet.noise (processing/core, Processing 4.5.2, LGPL 2.1),
// through the reference interpreter's python/immanence/pde/pnoise.py. See THIRD-PARTY.md.
#include "pde_random.h"

#include <cmath>

#include "pde_font.h"

namespace pde {

void Randomness::noise_seed(uint64_t s) {
  perlin_rnd.set_seed(s);
  have_perlin_ = false;
}

void Randomness::noise_detail(int64_t lod, bool has_falloff, double fo) {
  if (lod > 0) octaves = (int)(lod < 16 ? lod : 16);
  if (has_falloff && fo > 0) falloff = (float)fo;
}

void Randomness::make_perlin() {
  for (int i = 0; i < 4096; i++) perlin_[i] = (float)perlin_rnd.next_float();
  have_perlin_ = true;
}

// The common case (every coordinate below 2^31) in float: each Python step rounds a double
// computed from float operands to float, which is exactly float arithmetic (the operands are
// floats and double has more than 2*24+2 bits), and xf stays a float-exact fraction.
double Randomness::noise(double x, double y, double z, FaultRef fr) {
  if (!have_perlin_) make_perlin();
  float fx = (float)x, fy = (float)y, fz = (float)z;
  if (fx != fx || fy != fy || fz != fz) return 0.0;
  fx = std::fabs(fx);
  fy = std::fabs(fy);
  fz = std::fabs(fz);
  if (!(fx < 2147483648.0f && fy < 2147483648.0f && fz < 2147483648.0f))
    return noise_slow(fx, fy, fz, fr);
  uint32_t xi = (uint32_t)fx, yi = (uint32_t)fy, zi = (uint32_t)fz;
  float xf = fx - (float)xi, yf = fy - (float)yi, zf = fz - (float)zi;
  float r = 0.0f, ampl = 0.5f;
  const float *p = perlin_;
  for (int o = 0; o < octaves; o++) {
    uint32_t of = xi + (yi << 4) + (zi << 8);
    float rxf = NOISE_FSC[(int)(xf * 360.0f) % 720];
    float ryf = NOISE_FSC[(int)(yf * 360.0f) % 720];
    float n1 = p[of & 4095];
    n1 = n1 + rxf * (p[(of + 1) & 4095] - n1);
    float n2 = p[(of + 16) & 4095];
    n2 = n2 + rxf * (p[(of + 17) & 4095] - n2);
    n1 = n1 + ryf * (n2 - n1);
    of += 256;
    n2 = p[of & 4095];
    n2 = n2 + rxf * (p[(of + 1) & 4095] - n2);
    float n3 = p[(of + 16) & 4095];
    n3 = n3 + rxf * (p[(of + 17) & 4095] - n3);
    n2 = n2 + ryf * (n3 - n2);
    float rzf = NOISE_FSC[(int)(zf * 360.0f) % 720];
    n1 = n1 + rzf * (n2 - n1);
    r = r + n1 * ampl;
    ampl = ampl * falloff;
    xi <<= 1;
    xf *= 2;
    yi <<= 1;
    yf *= 2;
    zi <<= 1;
    zf *= 2;
    if (xf >= 1.0f) {
      xi += 1;
      xf -= 1;
    }
    if (yf >= 1.0f) {
      yi += 1;
      yf -= 1;
    }
    if (zf >= 1.0f) {
      zi += 1;
      zf -= 1;
    }
  }
  return r;
}

// f32(v) of a double as struct.pack('<f') + int(): +-inf / NaN raise (OverflowError/ValueError)
static bool fsc_index(double v, int *out, FaultRef fr) {
  float y = (float)v;
  if (!std::isfinite(y)) {
    fr.raise(Fault::Runtime, "arithmetic error (OverflowError)");
    return false;
  }
  *out = (int)std::fmod((double)y, 720.0);   // y >= 0, an integer part of any size
  return true;
}

// Any coordinate >= 2^31: xi saturates, xf = f32(x - 2^31) and the fractions are doubles.
double Randomness::noise_slow(double x, double y, double z, FaultRef fr) {
  const double B = 2147483648.0;
  uint32_t xi = x < B ? (uint32_t)x : 2147483647u, yi = y < B ? (uint32_t)y : 2147483647u,
           zi = z < B ? (uint32_t)z : 2147483647u;
  double xf = x < B ? x - xi : f32(x - B), yf = y < B ? y - yi : f32(y - B), zf = z < B ? z - zi : f32(z - B);
  float r = 0.0f, ampl = 0.5f;
  const float *p = perlin_;
  for (int o = 0; o < octaves; o++) {
    uint32_t of = xi + (yi << 4) + (zi << 8);
    int ix, iy, iz;
    if (!fsc_index(xf * 360, &ix, fr) || !fsc_index(yf * 360, &iy, fr)) return 0.0;
    float rxf = NOISE_FSC[ix], ryf = NOISE_FSC[iy];
    float n1 = p[of & 4095];
    n1 = n1 + rxf * (p[(of + 1) & 4095] - n1);
    float n2 = p[(of + 16) & 4095];
    n2 = n2 + rxf * (p[(of + 17) & 4095] - n2);
    n1 = n1 + ryf * (n2 - n1);
    of += 256;
    n2 = p[of & 4095];
    n2 = n2 + rxf * (p[(of + 1) & 4095] - n2);
    float n3 = p[(of + 16) & 4095];
    n3 = n3 + rxf * (p[(of + 17) & 4095] - n3);
    n2 = n2 + ryf * (n3 - n2);
    if (!fsc_index(zf * 360, &iz, fr)) return 0.0;
    float rzf = NOISE_FSC[iz];
    n1 = n1 + rzf * (n2 - n1);
    r = r + n1 * ampl;
    ampl = ampl * falloff;
    xi <<= 1;
    xf *= 2;
    yi <<= 1;
    yf *= 2;
    zi <<= 1;
    zf *= 2;
    if (xf >= 1.0) {
      xi += 1;
      xf -= 1;
    }
    if (yf >= 1.0) {
      yi += 1;
      yf -= 1;
    }
    if (zf >= 1.0) {
      zi += 1;
      zf -= 1;
    }
  }
  return r;
}

}  // namespace pde
