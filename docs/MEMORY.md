# Memory

The VM needs two kinds of memory:

1. **The arena**, a buffer you give it (`pde::Vm vm(arena, bytes)`). It holds every string, array
   and object the sketch makes. It is split in two halves for a copying garbage collector, so a
   64 KB arena gives the sketch 32 KB of live data. PSRAM is fine (and is what the reference
   device uses).
2. **The VM itself**, from the normal heap:
   * the `pde::Vm` object, about 72 KB in one block on an ESP32 (it holds the 30 KB canvas);
   * the operand stack and locals, `vm.limits.stack_slots` × 9 bytes in two blocks
     (8,192 slots by default: 64 KB + 8 KB; the no-PSRAM example uses 2,048 slots, 18 KB in all;
     a sketch that needs a deeper stack than you give it stops with a fault);
   * the globals and a copy of the program (a few KB);
   * for P3D sketches only, a 120 KB depth buffer in one block, made when the sketch calls
     `size(240, 128, P3D)`.

   So about 150 KB with the default stack, or about 95 KB with 2,048 slots, plus 120 KB for P3D.
   The big blocks are probed before they are allocated: when one does not fit, `load()` returns
   false or the sketch stops with a `memory` fault, instead of the chip aborting.

## How big an arena?

Measured on the 2,228 pieces of immanencepure.xyz: the smallest power-of-two arena that draws the
same first 240 frames as a 1.5 MB arena.

| Arena | Pieces that fit | Cumulative |
|---|---|---|
| 64 KB | 1,281 | 57 % |
| 128 KB | 20 | 58 % |
| 256 KB | 11 | 59 % |
| 512 KB | 623 | 87 % |
| 1 MB | 293 | 100 % |

The site API gives each piece's measured arena (`arena_bytes`). A sketch whose live data grows
past its arena stops with a `memory` fault; restart it with a bigger arena if you have one.
`immanence::DEFAULT_ARENA` (1.5 MB) runs everything.

## Boards

* **ESP32-S3 / ESP32 with PSRAM** (e.g. 8 MB): put a 1.5 MB arena in PSRAM and every piece runs.
* **ESP32 without PSRAM**: with Wi-Fi off the largest free block is about 110 KB. Allocate the
  `pde::Vm` first (with `stack_slots = 2048`), then an 80–96 KB arena from what is left: that runs
  the many pieces that need little memory (the examples' pieces use under 2 KB), but not P3D.
  Choose pieces by `arena_bytes`.
* Any other C++17 target with ~200 KB of RAM can run the small pieces; the VM has no platform
  dependencies (host builds run on Linux, macOS and WebAssembly).

## Time

A frame is bounded by `vm.limits.steps` (default 4,000,000 VM steps). On an ESP32-S3 at 240 MHz
most pieces take tens to a few hundred milliseconds per frame (doubles are software-emulated on
the ESP32, and many pieces are double-heavy), so animations run at a few frames per second. That
is the intended pace for slow displays (e-paper, memory LCDs); on a fast TFT simply draw each
frame when it is ready. Set `vm.now_ms` and `vm.limits.ms` to bound a frame by wall-clock time
too, and `vm.yield` to let other tasks run during a long frame.
