#pragma once
// immanence-sketches: run Processing sketches, compiled to bytecode, on any C++17 target.
//
//   #include <immanence.h>
//   uint8_t *arena = ...;                     // 64 KB .. 1.5 MB (see docs/MEMORY.md); PSRAM is fine
//   pde::Vm vm(arena, arena_bytes);
//   vm.now_ms = my_millis;                    // optional: a per-frame wall-clock limit
//   vm.api.now_s = epoch_seconds;             // optional: what hour()/minute()/day() read
//   if (!vm.load(program, program_len)) { /* vm.fault_msg() says why */ }
//   while (vm.next_frame()) {                 // setup() the first time, then one draw() per call
//     show(vm.grey());                        // 240 x 128 bytes, row-major, 0 = black, 255 = white
//     if (vm.done()) break;                   // a still sketch (no draw(), or noLoop()) is finished
//   }
//   // vm.fault() != pde::Fault::None: the sketch broke a limit or failed; vm.fault_msg() says how.
//
// The helpers below put the grey canvas on real displays; docs/DISPLAYS.md shows more.
#include <cstddef>
#include <cstdint>

#include "pde_vm.h"

namespace immanence {

constexpr int WIDTH = pde::W;    // 240
constexpr int HEIGHT = pde::H;   // 128
constexpr int BYTECODE_VERSION = pde::BC_VERSION;
constexpr size_t DEFAULT_ARENA = 1536 * 1024;   // enough for every piece on immanencepure.xyz

// The reference 1-bit look: a 4x4 ordered dither, ink where ((255 - g) * 16 + 127) / 255 is above
// the Bayer threshold. True = ink (black).
bool ink_at(const uint8_t *grey, int x, int y);

// The whole canvas as packed 1-bit rows (WIDTH / 8 bytes per row, HEIGHT rows, out must hold
// WIDTH * HEIGHT / 8 = 3840 bytes). msb_first: pixel 0 is bit 7 of byte 0 (SSD1306 page layouts
// differ: see examples/). ink_is_one: 1 = ink (black) or 1 = paper (white, e.g. OLED "on").
void to_1bit(const uint8_t *grey, uint8_t *out, bool msb_first = true, bool ink_is_one = true);

// Grey to RGB565 for colour TFTs.
inline uint16_t rgb565(uint8_t g) { return (uint16_t) (((g >> 3) << 11) | ((g >> 2) << 5) | (g >> 3)); }

// Nearest-neighbour fit of the canvas into a w x h box: the largest scale (integer when it fits,
// fractional otherwise) that keeps the aspect, centred. Calls put(x, y, grey) for every box pixel.
template <typename Put>
void fit(const uint8_t *grey, int w, int h, Put put, uint8_t border = 0) {
  // scale = min(w / WIDTH, h / HEIGHT), in 16.16 fixed point
  uint32_t sx = ((uint32_t) w << 16) / WIDTH, sy = ((uint32_t) h << 16) / HEIGHT;
  uint32_t s = sx < sy ? sx : sy;
  if (s >= 65536) s &= ~0xFFFFu;   // integer scale when the box allows it: crisp pixels
  int dw = (int) ((WIDTH * (uint64_t) s) >> 16), dh = (int) ((HEIGHT * (uint64_t) s) >> 16);
  int ox = (w - dw) / 2, oy = (h - dh) / 2;
  for (int y = 0; y < h; y++) {
    for (int x = 0; x < w; x++) {
      int cx = x - ox, cy = y - oy;
      if (cx < 0 || cy < 0 || cx >= dw || cy >= dh) { put(x, y, border); continue; }
      int gx = (int) (((uint64_t) cx << 16) / s), gy = (int) (((uint64_t) cy << 16) / s);
      put(x, y, grey[gy * WIDTH + gx]);
    }
  }
}

}  // namespace immanence
