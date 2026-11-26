// Host runner for the VM: runs a compiled program (python/immanence) for N frames and prints
// one line per frame: "F <frameCount> <crc32 of the grey canvas> <steps> <us>", then
// "END done|running|fault <kind> <message>". Used by host/parity.py and python/immanence (check) against the reference interpreter.
//   vm_run prog.bin frames [--clock <epoch> <tz offset s>] [--dump out.grey] [--arena <bytes>]
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

#include "../src/pde_vm.h"

static const char *FAULTS[] = {"none", "runtime", "memory", "size", "budget", "deadline", "depth"};

int main(int argc, char **argv) {
  if (argc < 3) {
    fprintf(stderr, "usage: vm_run prog.bin frames [--clock epoch tz] [--dump out] [--arena bytes]\n");
    return 2;
  }
  FILE *f = fopen(argv[1], "rb");
  if (!f) return 2;
  std::vector<uint8_t> prog;
  uint8_t buf[4096];
  size_t n;
  while ((n = fread(buf, 1, sizeof buf, f)) > 0) prog.insert(prog.end(), buf, buf + n);
  fclose(f);
  int frames = atoi(argv[2]);
  long long epoch = 0;
  int tz = 0;
  const char *dump = nullptr;
  bool globals = false;
  size_t arena_bytes = 1536 * 1024;
  for (int i = 3; i < argc; i++) {
    if (!strcmp(argv[i], "--clock") && i + 2 < argc) {
      epoch = atoll(argv[++i]);
      tz = atoi(argv[++i]);
    } else if (!strcmp(argv[i], "--dump") && i + 1 < argc) {
      dump = argv[++i];
    } else if (!strcmp(argv[i], "--globals")) {
      globals = true;
    } else if (!strcmp(argv[i], "--arena") && i + 1 < argc) {
      arena_bytes = (size_t) atoll(argv[++i]);
    }
  }
  std::vector<uint8_t> arena(arena_bytes);
  static pde::Vm *vmp;
  pde::Vm *vm = new pde::Vm(arena.data(), arena.size());
  vmp = vm;
  vm->api.now_s = epoch;
  vm->api.tz_s = tz;
  vm->now_ms = []() -> uint32_t {
    return (uint32_t) std::chrono::duration_cast<std::chrono::milliseconds>(
               std::chrono::steady_clock::now().time_since_epoch()).count();
  };
  vm->limits.ms = 60000;          // parity runs: the step budget decides, not the laptop's load
  if (!vm->load(prog.data(), prog.size())) {
    printf("END bad_program %s\n", vm->fault_msg());
    return 0;
  }
  FILE *out = dump ? fopen(dump, "wb") : nullptr;
  for (int i = 0; i < frames; i++) {
    auto t0 = std::chrono::steady_clock::now();
    bool ok = vm->next_frame();
    auto us = std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now() - t0).count();
    if (!ok) break;
    printf("F %d %08x %u %lld\n", vm->api.frame_count, pde::crc32(vm->grey(), pde::W * pde::H), vm->last_steps(),
           (long long) us);
    if (out) fwrite(vm->grey(), 1, pde::W * pde::H, out);
    if (vm->done()) break;
  }
  if (out) fclose(out);
  if (vm->fault() != pde::Fault::None)
    printf("END fault %s %s\n", FAULTS[(int) vm->fault()], vm->fault_msg());
  else
    printf("END %s heap=%zu gcs=%u\n", vm->done() ? "done" : "running", vm->heap_used(), vm->gcs());
#ifdef PDE_PROFILE
  printf("P ops %llu frames %d\n", (unsigned long long) vm->prof_ops, vm->frame());
  for (int k = 0; k < pde::N_COUNT_; k++)
    if (vm->prof_native_calls[k])
      printf("N %d %llu %llu\n", k, (unsigned long long) vm->prof_native_calls[k],
             (unsigned long long) vm->prof_native_ns[k]);
#endif
  if (globals) {
    for (size_t i = 0; i < vm->nglobals(); i++) {
      pde::Slot s = vm->global(i);
      switch (vm->gflavour(i)) {
        case 'I': printf("G %zu I %d\n", i, s.i); break;
        case 'L': printf("G %zu L %lld\n", i, (long long) s.l); break;
        case 'F': { uint32_t u; memcpy(&u, &s.f, 4); printf("G %zu F %08x\n", i, u); break; }
        case 'D': { uint64_t u; memcpy(&u, &s.d, 8); printf("G %zu D %016llx\n", i, (unsigned long long) u); break; }
        default: {
          const uint16_t *p;
          uint32_t n;
          if (s.r == 0) printf("G %zu R null\n", i);
          else if (vm->str(s.r, p, n)) {
            printf("G %zu S ", i);
            for (uint32_t k = 0; k < n; k++) printf("%04x", p[k]);
            printf("\n");
          } else {
            printf("G %zu R obj\n", i);
          }
        }
      }
    }
  }
  (void) vmp;
  return 0;
}
