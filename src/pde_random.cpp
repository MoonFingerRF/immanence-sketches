#include "pde_fp.h"  // first: no fused multiply-add (see the header)
#include "pde_random.h"

#include <cmath>

#include "pde_font.h"

namespace pde {

static constexpr uint64_t MUL = 0x5DEECE66DULL, MASK48 = (1ULL << 48) - 1;

void JavaRandom::set_seed(uint64_t s) {
  seed = (s ^ MUL) & MASK48;
  have_gauss = false;
}

int32_t JavaRandom::next(int bits) {
  seed = (seed * MUL + 0xB) & MASK48;
  return (int32_t)(uint32_t)(seed >> (48 - bits));
}

double JavaRandom::next_float() { return (double)(uint32_t)next(24) / (double)(1 << 24); }

double JavaRandom::next_double() {
  uint64_t a = (uint32_t)next(26), b = (uint32_t)next(27);
  return (double)((a << 27) + b) * (1.0 / (double)(1ULL << 53));
}

double JavaRandom::next_gaussian() {
  if (have_gauss) {
    have_gauss = false;
    return gauss;
  }
  double v1, v2, s;
  for (;;) {
    v1 = 2 * next_double() - 1;
    v2 = 2 * next_double() - 1;
    s = v1 * v1 + v2 * v2;
    if (0 < s && s < 1) break;
  }
  double mul = std::sqrt(-2 * std::log(s) / s);
  gauss = v2 * mul;
  have_gauss = true;
  return v1 * mul;
}

Randomness::Randomness() : rnd(0), perlin_rnd(0) {}

// runtime jrandom.py Randomness.random/random2: redraws bounded (RETRIES), random(+-inf) is one
// draw and NaN (Processing would redraw ~2^24 times and end with NaN)
double Randomness::random(double high, FaultRef fr) {
  (void) fr;
  if (high == 0 || high != high) return 0.0;
  if (std::isinf(high)) {
    rnd.next_float();
    return NAN;
  }
  double v = high;
  for (int i = 0; i < RETRIES; i++) {
    v = f32(rnd.next_float() * high);
    if (v != high) return v;
  }
  return v;
}

double Randomness::random2(double low, double high, FaultRef fr) {
  if (low >= high) return low;
  double diff = f32(high - low);
  double v = high;
  for (int i = 0; i < RETRIES; i++) {
    v = f32(random(diff, fr) + low);
    if (v != high) return v;
  }
  return v;
}

}  // namespace pde
