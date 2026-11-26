// The VM as a WebAssembly module, for browsers and Node: the same C++ sources (../src) as the
// ESP32 build, with the same flags, so a page draws exactly what a device draws. This is the
// engine immanencepure.xyz runs in its Web Workers. Build: wasm/build.sh.
//
// One Vm per instance, addressed by index, so a page can keep a small pool of them alive and let
// far-away tiles go. Everything is C, no std::string, because emscripten only hands JS the
// exported symbols.
//
//   idx = gvm_create(arena_bytes)   // -1 on failure; arena is the VM heap (see docs/MEMORY.md)
//   gvm_load(idx, ptr, len)         // false = a malformed program (gvm_fault_msg says why)
//   gvm_set_clock(idx, epoch, tz)   // what hour()/minute()/day() read (seconds since 1970, UTC offset)
//   gvm_seed(idx, seed)             // before the first frame: per-instance random()/noise() seed
//   gvm_next_frame(idx)             // setup() the first time, then one draw()
//   gvm_grey(idx)                   // -> WASM pointer to 240*128 grey bytes (0 = black)
//   gvm_frame(idx)                  // frameCount of the last draw
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <vector>

#include "pde_vm.h"

#ifdef __EMSCRIPTEN__
#include <emscripten.h>
#define GVM_EXPORT extern "C" EMSCRIPTEN_KEEPALIVE
#else
#define GVM_EXPORT extern "C"
#endif

namespace {

struct Instance {
  pde::Vm *vm = nullptr;
  uint8_t *arena = nullptr;
  size_t bytes = 0;
};

constexpr int MAX_INSTANCES = 512;
Instance g_inst[MAX_INSTANCES];

inline pde::Vm *vm_of(int idx) {
  if (idx < 0 || idx >= MAX_INSTANCES) return nullptr;
  return g_inst[idx].vm;
}

}  // namespace

GVM_EXPORT void gvm_reset(int idx);   // defined below; gvm_create and gvm_load both use it

GVM_EXPORT int gvm_max_instances() { return MAX_INSTANCES; }

GVM_EXPORT int gvm_create(int arena_bytes) {
  if (arena_bytes < 64 * 1024) arena_bytes = 64 * 1024;
  for (int i = 0; i < MAX_INSTANCES; i++) {
    if (g_inst[i].vm) continue;
    uint8_t *arena = (uint8_t *) malloc((size_t) arena_bytes);
    if (!arena) return -1;
    g_inst[i].arena = arena;
    g_inst[i].bytes = (size_t) arena_bytes;
    g_inst[i].vm = nullptr;
    gvm_reset(i);
    if (!g_inst[i].vm) { free(arena); return -1; }
    return i;
  }
  return -1;
}

GVM_EXPORT void gvm_destroy(int idx) {
  pde::Vm *vm = vm_of(idx);
  if (!vm) return;
  delete vm;
  free(g_inst[idx].arena);
  g_inst[idx] = Instance{};
}

// A fresh Vm on the same arena. Vm::load() is NOT a reset -- it re-parses a program into a VM that
// still holds the previous one's done_/fault_/heap, so reusing an instance silently corrupts the
// next program (devices avoid this by constructing a new Vm per program).
// gvm_reset is that, without throwing the arena away. gvm_load calls it, so no caller can get this
// wrong: loading a program always begins from a clean VM.
GVM_EXPORT void gvm_reset(int idx) {
  if (idx < 0 || idx >= MAX_INSTANCES || !g_inst[idx].arena) return;
  delete g_inst[idx].vm;
  pde::Vm *vm = new pde::Vm(g_inst[idx].arena, g_inst[idx].bytes);
  // The host runner's own limits (host/vm_run.cpp): the step budget decides the frame, not the
  // machine's load, so a slow computer and a fast one produce the same pixels.
  vm->limits.ms = 60000;
  vm->now_ms = nullptr;
  g_inst[idx].vm = vm;
}

GVM_EXPORT int gvm_load(int idx, const uint8_t *prog, int len) {
  if (idx < 0 || idx >= MAX_INSTANCES || !g_inst[idx].arena || !prog || len <= 0) return 0;
  gvm_reset(idx);
  return g_inst[idx].vm->load(prog, (size_t) len) ? 1 : 0;
}

// The clock a piece that calls second()/hour()/day() reads (golden frames are drawn at a fixed one).
GVM_EXPORT void gvm_set_clock(int idx, double epoch_s, int tz_s) {
  pde::Vm *vm = vm_of(idx);
  if (!vm) return;
  vm->api.now_s = (int64_t) epoch_s;
  vm->api.tz_s = (int32_t) tz_s;
}

// Per-instance entropy for a piece that never seeds itself (no randomSeed()/noiseSeed() in its
// source): two copies of the same piece then never draw the same grains. Pieces that DO seed
// themselves are left alone -- their seed is part of the artwork. Must be called before the first
// gvm_next_frame (setup() runs there); no-op afterwards.
GVM_EXPORT void gvm_seed(int idx, uint32_t seed) {
  pde::Vm *vm = vm_of(idx);
  if (!vm) return;
  vm->api.rand.seed(seed);
  vm->api.rand.noise_seed(seed);
}

GVM_EXPORT int gvm_next_frame(int idx) {
  pde::Vm *vm = vm_of(idx);
  if (!vm) return 0;
  return vm->next_frame() ? 1 : 0;
}

GVM_EXPORT uint8_t *gvm_grey(int idx) {
  pde::Vm *vm = vm_of(idx);
  return vm ? (uint8_t *) vm->grey() : nullptr;
}

GVM_EXPORT int gvm_frame(int idx) {
  pde::Vm *vm = vm_of(idx);
  return vm ? vm->frame() : -1;
}

GVM_EXPORT int gvm_done(int idx) {
  pde::Vm *vm = vm_of(idx);
  return vm && vm->done() ? 1 : 0;
}

GVM_EXPORT int gvm_fault(int idx) {
  pde::Vm *vm = vm_of(idx);
  return vm ? (int) vm->fault() : -1;
}

GVM_EXPORT const char *gvm_fault_msg(int idx) {
  pde::Vm *vm = vm_of(idx);
  return vm ? vm->fault_msg() : "";
}

GVM_EXPORT int gvm_last_steps(int idx) {
  pde::Vm *vm = vm_of(idx);
  return vm ? (int) vm->last_steps() : 0;
}

GVM_EXPORT int gvm_heap_used(int idx) {
  pde::Vm *vm = vm_of(idx);
  return vm ? (int) vm->heap_used() : 0;
}
