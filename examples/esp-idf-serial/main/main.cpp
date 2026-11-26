// immanence-sketches: the serial self-test.
//
// Runs each embedded piece for its golden frames (30 for an animated piece, 1 for a still one) and
// compares the crc32 of every 240x128 grey frame with the crc the reference draws. A board (or an
// emulator) that prints PASS draws bit for bit like the reference. No display needed.
//
// Output, per piece:   F <n> <crc> MATCH|DIFF <ms>
// then a summary line  PASS (or FAIL) and one frame as ASCII art (one character per 2x4 block).
#include <cinttypes>
#include <cstdio>
#include <cstring>
#include <new>

#include "esp_chip_info.h"
#include "esp_heap_caps.h"
#include "esp_memory_utils.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include <immanence.h>

#include "interference.h"
#include "quiet_field.h"
#include "ridgelines.h"
#include "the_wave.h"
#include "three_bodies.h"

namespace {

struct Piece {
  const char *name;
  const uint8_t *prog;
  size_t len;
  const uint32_t *golden;
  int golden_n;
};

const Piece PIECES[] = {
    {"interference", interference, interference_len, interference_golden, interference_golden_n},
    {"three bodies", three_bodies, three_bodies_len, three_bodies_golden, three_bodies_golden_n},
    {"ridgelines", ridgelines, ridgelines_len, ridgelines_golden, ridgelines_golden_n},
    {"quiet field", quiet_field, quiet_field_len, quiet_field_golden, quiet_field_golden_n},
    {"the wave", the_wave, the_wave_len, the_wave_golden, the_wave_golden_n},
};
constexpr int N_PIECES = sizeof(PIECES) / sizeof(PIECES[0]);
constexpr int ASCII_PIECE = 0;   // which piece's last frame to print as ASCII art

uint32_t millis32() { return (uint32_t) (esp_timer_get_time() / 1000); }
void let_idle_run() { vTaskDelay(1); }   // keeps the task watchdog fed during long frames

void print_heap(const char *when) {
  printf("heap %s: free %u (internal %u, largest internal block %u, psram %u)\n", when,
         (unsigned) esp_get_free_heap_size(), (unsigned) heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
         (unsigned) heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),
         (unsigned) heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
}

// The arena: 1.5 MB from PSRAM when the board has it, else the largest of a few internal sizes
// that leaves room for the VM object (~40 KB) and a P3D depth buffer (~125 KB).
uint8_t *alloc_arena(size_t &bytes) {
  static const size_t PSRAM_SIZES[] = {immanence::DEFAULT_ARENA, 1024 * 1024, 512 * 1024};
  if (heap_caps_get_total_size(MALLOC_CAP_SPIRAM) > 0) {
    for (size_t n : PSRAM_SIZES) {
      if (auto *p = (uint8_t *) heap_caps_malloc(n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT)) {
        bytes = n;
        return p;
      }
    }
  }
  static const size_t INTERNAL_SIZES[] = {64 * 1024, 48 * 1024, 32 * 1024};
  for (size_t n : INTERNAL_SIZES) {
    if (auto *p = (uint8_t *) heap_caps_malloc(n, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)) {
      bytes = n;
      return p;
    }
  }
  bytes = 0;
  return nullptr;
}

void print_ascii(const uint8_t *grey) {
  static const char RAMP[] = "@%#*+=-:. ";   // dark .. light (0 = black)
  constexpr int NR = sizeof(RAMP) - 2;
  char line[immanence::WIDTH / 2 + 1];
  for (int by = 0; by < immanence::HEIGHT; by += 4) {
    for (int bx = 0; bx < immanence::WIDTH; bx += 2) {
      int sum = 0;
      for (int y = by; y < by + 4; y++) sum += grey[y * immanence::WIDTH + bx] + grey[y * immanence::WIDTH + bx + 1];
      line[bx / 2] = RAMP[(sum / 8) * NR / 255];
    }
    line[immanence::WIDTH / 2] = 0;
    printf("|%s|\n", line);
  }
}

enum class Result { Match, Differ, NoMemory, Broken };

bool memory_msg(const char *msg) { return strstr(msg, "memory") != nullptr; }

// Runs one piece for its golden frames and compares every frame with the reference.
// Match: every frame matched. Differ: a frame differed (the build is not exact). NoMemory: the
// board could not give the piece what it needs (a `memory` fault, or a load refused for lack of
// memory): not a correctness failure. Broken: anything else that stopped it early.
// `stack_slots` 0 keeps the VM's default. Keeps a copy of the last frame in `keep` when given.
Result run_piece(const Piece &pc, uint8_t *arena, size_t arena_bytes, uint32_t stack_slots, uint8_t *keep) {
  pde::Vm *vm = new (std::nothrow) pde::Vm(arena, arena_bytes);
  if (!vm) {
    printf("no memory for the VM object (%u bytes)\n", (unsigned) sizeof(pde::Vm));
    return Result::NoMemory;
  }
  vm->now_ms = millis32;
  vm->yield = let_idle_run;
  // The frame deadline exists to stop runaway sketches on a live display. This is a correctness
  // test that may run under an emulator, so give it far more wall clock than a real board needs.
  vm->limits.ms = 120000;
  if (stack_slots) vm->limits.stack_slots = stack_slots;
  if (!vm->load(pc.prog, pc.len)) {
    printf("load failed: %s\n", vm->fault_msg());
    Result r = memory_msg(vm->fault_msg()) ? Result::NoMemory : Result::Broken;
    delete vm;
    return r;
  }
  print_heap("after load");
  int diffs = 0, n = 0;
  while (n < pc.golden_n) {
    int64_t t0 = esp_timer_get_time();
    if (!vm->next_frame()) break;
    int64_t t1 = esp_timer_get_time();
    uint32_t crc = pde::crc32(vm->grey(), immanence::WIDTH * immanence::HEIGHT);
    bool ok = crc == pc.golden[n];
    if (!ok) diffs++;
    n++;
    printf("F %d %08" PRIx32 " %s %d ms\n", n, crc, ok ? "MATCH" : "DIFF", (int) ((t1 - t0) / 1000));
    if (vm->done()) break;
  }
  Result r = diffs ? Result::Differ : Result::Match;
  if (vm->fault() != pde::Fault::None) {
    printf("fault after frame %d (%s): %s\n", n, vm->fault() == pde::Fault::Memory ? "memory" : "other",
           vm->fault_msg());
    if (!diffs) r = vm->fault() == pde::Fault::Memory ? Result::NoMemory : Result::Broken;
  } else if (n < pc.golden_n) {
    printf("stopped after %d of %d frames\n", n, pc.golden_n);
    if (!diffs) r = Result::Broken;
  }
  printf("arena: %u of %u bytes per half in use, %u collections\n", (unsigned) vm->heap_used(),
         (unsigned) vm->heap_half(), (unsigned) vm->gcs());
  if (keep) memcpy(keep, vm->grey(), immanence::WIDTH * immanence::HEIGHT);
  delete vm;
  return r;
}

}  // namespace

extern "C" void app_main(void) {
  esp_chip_info_t chip;
  esp_chip_info(&chip);
  printf("\nimmanence-sketches serial self-test: %s rev %d.%d, %d core(s), bytecode v%d\n", CONFIG_IDF_TARGET,
         chip.revision / 100, chip.revision % 100, chip.cores, immanence::BYTECODE_VERSION);
  print_heap("at start");
  printf("VM object: %u bytes (holds the 240x128 canvas)\n", (unsigned) sizeof(pde::Vm));

  size_t arena_bytes = 0;
  uint8_t *arena = alloc_arena(arena_bytes);
  if (!arena) {
    printf("FAIL: no memory for an arena\n");
    return;
  }
  printf("arena: %u bytes in %s\n", (unsigned) arena_bytes,
         esp_ptr_external_ram(arena) ? "PSRAM" : "internal RAM");
  print_heap("after the arena");

  // The operand stack is 8-byte slots in one block: 8192 (the default) is 64 KB, more than a
  // PSRAM-less ESP32 has left beside the arena and the VM object. These pieces need far less.
  const uint32_t stack_slots = heap_caps_get_total_size(MALLOC_CAP_SPIRAM) == 0 ? 2048 : 0;
  static uint8_t ascii_frame[immanence::WIDTH * immanence::HEIGHT];
  int matched = 0, failed = 0, no_memory = 0;
  for (int i = 0; i < N_PIECES; i++) {
    const Piece &pc = PIECES[i];
    printf("\n== %s (%u bytes of bytecode, %d golden frames)\n", pc.name, (unsigned) pc.len, pc.golden_n);
    Result r = run_piece(pc, arena, arena_bytes, stack_slots, i == ASCII_PIECE ? ascii_frame : nullptr);
    if (r == Result::Match) {
      matched++;
    } else if (r == Result::NoMemory) {
      no_memory++;
      printf("piece %s: does not fit this board's memory\n", pc.name);
    } else {
      failed++;
      printf("piece %s: %s\n", pc.name, r == Result::Differ ? "FRAMES DIFFER" : "did not run");
    }
  }

  // What a memory fault looks like, on any board: a piece whose data outgrows a tiny arena, and a
  // load that asks for an operand stack no board has. Both must fail cleanly.
  printf("\n== memory faults, on purpose\n");
  printf("-- three bodies in a 2 KB arena (it needs about 1.5 KB per half):\n");
  bool ok_small = run_piece(PIECES[1], arena, 2048, stack_slots, nullptr) == Result::NoMemory;
  printf("-- three bodies with limits.stack_slots = 64 M (a 512 MB stack):\n");
  bool ok_stack = run_piece(PIECES[1], arena, arena_bytes, 64u << 20, nullptr) == Result::NoMemory;
  printf("-- memory faults: %s\n", ok_small && ok_stack ? "reported cleanly, as expected" : "UNEXPECTED");
  if (!ok_small || !ok_stack) failed++;

  print_heap("at the end");
  printf("\n%s: %d of %d pieces match the reference frame for frame", failed ? "FAIL" : "PASS", matched, N_PIECES);
  if (no_memory) printf(", %d did not fit in memory", no_memory);
  printf("\n");
  printf("\n%s, last frame:\n", PIECES[ASCII_PIECE].name);
  print_ascii(ascii_frame);
  printf("done\n");
}
