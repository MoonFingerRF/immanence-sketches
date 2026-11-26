#include "pde_fp.h"  // first: no fused multiply-add (see the header)
#include "immanence.h"

namespace immanence {

static const uint8_t BAYER4[4][4] = {{0, 8, 2, 10}, {12, 4, 14, 6}, {3, 11, 1, 9}, {15, 7, 13, 5}};

bool ink_at(const uint8_t *grey, int x, int y) {
  int level = ((255 - grey[y * WIDTH + x]) * 16 + 127) / 255;
  return level > BAYER4[y & 3][x & 3];
}

void to_1bit(const uint8_t *grey, uint8_t *out, bool msb_first, bool ink_is_one) {
  for (int y = 0; y < HEIGHT; y++) {
    for (int bx = 0; bx < WIDTH / 8; bx++) {
      uint8_t b = 0;
      for (int k = 0; k < 8; k++) {
        bool on = ink_at(grey, bx * 8 + k, y) == ink_is_one;
        if (on) b |= msb_first ? (uint8_t) (0x80 >> k) : (uint8_t) (1 << k);
      }
      out[y * (WIDTH / 8) + bx] = b;
    }
  }
}

}  // namespace immanence
