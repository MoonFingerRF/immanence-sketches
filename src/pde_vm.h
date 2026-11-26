#pragma once
// A small VM for the bytecode the compiler (python/immanence) makes from Processing sketches
// (python/immanence/pde/bytecode.py; the format is in docs/BYTECODE.md).
//
//   Vm vm(arena, arena_bytes);          // fixed arena (PSRAM on an ESP32), split in two halves
//   vm.load(program, len)               // parse + verify; false on a malformed program
//   vm.next_frame()                     // setup() the first time, then one draw(): true = a frame
//   vm.grey()                           // 240x128 grey bytes (0 = black), as the reference's Api.grey()
//
// Safe by construction: every index in the program is checked at load, the operand stack depth of
// every instruction is verified (as the compiler computed it), references are bounds-checked when used,
// and a frame is bounded by steps, a wall-clock deadline, call depth and the arena. Any breach is a
// fault for that frame (fault(), fault_msg()): the sketch stops and the caller decides what to show.
// No I/O: the program reaches only its own values and pde::Api.
// Plain C++17 (host-tested: host/ + python/tests); build with -ffp-contract=off (see pde_fp.h).
#include <cstddef>
#include <cstdint>
#include <vector>

#include "pde_api.h"
#include "pde_spec.h"

namespace pde {

union Slot {
  int32_t i;
  int64_t l;
  float f;
  double d;
  uint32_t r;
};

struct VmLimits {
  uint32_t steps = 4000000;      // per frame (setup has its own)
  uint32_t ms = 2000;            // wall clock per frame (needs a clock: Vm::now_ms)
  int depth = 64;                // user calls
  uint32_t stack_slots = 8192;   // operand stack + locals, all frames
};

class Vm {
 public:
  Vm(uint8_t *arena, size_t bytes);
  bool load(const uint8_t *prog, size_t len);   // false: a malformed program (fault_msg says why)
  bool next_frame();                            // false: stopped (still, noLoop) or a fault
  const uint8_t *grey() const { return api.canvas.buf; }
  bool done() const { return done_; }
  Fault fault() const { return fault_; }
  const char *fault_msg() const { return fault_msg_; }
  int frame() const { return frame_; }          // frames produced (== frameCount of the last draw)
  bool uses_clock() const { return flags_ & 1; }
  uint32_t last_steps() const { return last_steps_; }
  size_t heap_used() const { return top_; }
  size_t heap_half() const { return half_; }

  Api api;
  VmLimits limits;
  uint32_t (*now_ms)() = nullptr;               // for the deadline; null = steps only
  void (*yield)() = nullptr;                    // called about every 30 ms of work (so a
                                                // low-priority task can let the idle task run)

  // ---- used by the natives (pde_natives.cpp)
  void set_fault(Fault f, const char *msg);
  uint32_t alloc(uint8_t type, uint8_t kind, uint32_t len, size_t payload);   // 0 on failure (fault set)
  uint32_t new_string(const uint16_t *s, size_t n);
  uint32_t new_string_ascii(const char *s, size_t n);
  uint32_t new_array(uint8_t kind, int32_t n);
  bool str(uint32_t ref, const uint16_t *&s, uint32_t &n);        // false: not a string (fault set)
  bool arr(uint32_t ref, uint8_t kind, uint8_t *&data, uint32_t &n);  // kind 0xFF: any
  uint8_t arr_kind(uint32_t ref);
  uint8_t *obj_ptr(uint32_t ref);
  uint32_t pixels = 0;                          // the sketch's pixels[] (a root)
#ifdef PDE_PROFILE
  // host estimates of a device's frame time: instructions and time per native
  uint64_t prof_ops = 0;
  uint64_t prof_native_ns[N_COUNT_] = {};
  uint64_t prof_native_calls[N_COUNT_] = {};
#endif

 private:
  struct Func {
    uint8_t nparams, has_this, returns;
    uint16_t nslots, maxstack;
    uint32_t off, len;
  };
  struct Frame {
    uint16_t fn;
    uint32_t pc;
    uint32_t base;       // first local slot
    uint32_t sp;         // operand stack pointer (absolute slot index) at the call
    bool counted;        // counts toward the user call depth
  };
  bool run(int fn, bool returns_value);
  bool verify(int fn);
  bool gc(size_t need);
  uint32_t copy_obj(uint32_t ref);
  bool fail_load(const char *msg);

  // program
  std::vector<uint8_t> prog_;
  const uint8_t *code_ = nullptr;
  size_t code_len_ = 0;
  std::vector<Func> funcs_;
  std::vector<uint8_t> gflav_;                  // global slot flavours: I L F D R
  std::vector<std::vector<uint8_t>> cflav_;     // per class field flavours
  std::vector<int64_t> longs_;
  std::vector<double> doubles_;
  std::vector<uint32_t> strings_;               // const-area refs
  std::vector<uint8_t> const_area_;
  int16_t entry_[4] = {-1, -1, -1, -1};
  uint8_t flags_ = 0;

  // state
  std::vector<Slot> globals_;
  std::vector<Slot> stack_;
  std::vector<uint8_t> tags_;                   // 1 = the slot holds a reference (GC roots)
  std::vector<Frame> frames_;
  uint8_t *arena_;
  size_t bytes_, half_;
  uint8_t *cur_, *other_;
  size_t top_ = 8;
  bool started_ = false, done_ = false, loaded_ = false;
  int frame_ = 0;
  Fault fault_ = Fault::None;
  const char *fault_msg_ = "";
  uint32_t steps_ = 0, next_check_ = 4096, last_steps_ = 0, deadline_ = 0;
  int depth_ = 0;
  uint64_t charged_seen_ = 0;
  size_t gc_top_ = 8;
  uint32_t sp_hi_ = 0;                          // live stack slots (GC roots) while running
  std::vector<uint32_t> troots_;                // references a native/op holds across an allocation
  uint32_t gcs_ = 0;
  uint32_t last_yield_ = 0;
  bool check_budget();
  uint32_t build_multi(uint8_t kind, const int32_t *sizes, int nd, int level);
 public:
  uint32_t gcs() const { return gcs_; }
  // the sketch's globals, for the host parity runner (host/vm_run.cpp --globals)
  size_t nglobals() const { return globals_.size(); }
  Slot global(size_t i) const { return globals_[i]; }
  char gflavour(size_t i) const { return (char) gflav_[i]; }
  std::vector<uint32_t> &temp_roots() { return troots_; }
  friend struct Natives;
};

// object layout in the arena: 8-byte header then the payload (8-aligned)
enum ObjType : uint8_t { T_STR = 1, T_ARR = 2, T_OBJ = 3, T_FONT = 4, T_FWD = 0xFF };
constexpr uint32_t CONST_REF = 0x80000000u;   // refs into the program's constant strings

// the natives (pde_natives.cpp): args are the argc slots on the stack (still there: GC roots)
bool call_native(Vm &vm, uint16_t id, int argc, uint8_t flags, Slot *args, uint8_t *tags, Slot &ret, uint8_t &ret_tag);

// strings as the reference makes them (compiler.py jstr / jfloat_str, builtins.py nf and text)
int jfloat_str(double v, bool single, char *out, size_t cap);
int text_float(double v, char *out, size_t cap);

uint32_t crc32(const uint8_t *data, size_t n, uint32_t crc = 0);

}  // namespace pde
