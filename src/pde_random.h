// Processing's randomness, bit for bit with the reference interpreter's python/immanence/pde/jrandom.py:
// java.util.Random, PApplet.random/randomGaussian, and PApplet.noise in Java float.
#pragma once
#include <cstdint>

#include "pde_pyops.h"

namespace pde {

class JavaRandom {
 public:
  explicit JavaRandom(uint64_t seed_bits = 0) { set_seed(seed_bits); }
  // setSeed(long): `seed_bits` are the low 64 bits of the seed (pyint_bits); only 48 matter
  void set_seed(uint64_t seed_bits);
  int32_t next(int bits);            // bits <= 32; (bits == 32 is signed, as Java's)
  double next_float();
  double next_double();
  double next_gaussian();            // uses std::sqrt / std::log (libm: see pde_api.h notes)
  uint64_t seed = 0;
  bool have_gauss = false;
  double gauss = 0.0;
};

class Randomness {
 public:
  Randomness();
  // every entry point mirrors a Randomness method; `fr` receives faults (the Python raises)
  void seed(uint64_t s_bits) { rnd.set_seed(s_bits); }
  double random(double high, FaultRef fr);
  double random2(double low, double high, FaultRef fr);
  double gaussian() { return f32(rnd.next_gaussian()); }
  double next_double() { return rnd.next_double(); }   // Math.random()
  void noise_seed(uint64_t s_bits);
  void noise_detail(int64_t lod, bool has_falloff, double falloff);
  double noise(double x, double y, double z, FaultRef fr);

  JavaRandom rnd, perlin_rnd;
  int octaves = 4;
  float falloff = 0.5f;
  static constexpr int RETRIES = 64;   // jrandom.py RETRIES

 private:
  bool have_perlin_ = false;
  float perlin_[4096];
  void make_perlin();
  double noise_slow(double x, double y, double z, FaultRef fr);
};

}  // namespace pde
