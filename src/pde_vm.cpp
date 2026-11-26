#include "pde_fp.h"  // first: no fused multiply-add (see the header)
#include "pde_vm.h"
#include "pde_mem.h"

#include <cmath>
#include <cstdio>
#include <cstring>
#ifdef PDE_PROFILE
#include <chrono>
#endif

namespace pde {

// ---------------------------------------------------------------------------- little helpers
static inline uint16_t rd16(const uint8_t *p) { return (uint16_t) (p[0] | (p[1] << 8)); }
static inline uint32_t rd32(const uint8_t *p) {
  return (uint32_t) p[0] | ((uint32_t) p[1] << 8) | ((uint32_t) p[2] << 16) | ((uint32_t) p[3] << 24);
}
static inline int32_t rdi32(const uint8_t *p) { return (int32_t) rd32(p); }
static inline float rdf32(const uint8_t *p) {
  uint32_t u = rd32(p);
  float f;
  memcpy(&f, &u, 4);
  return f;
}
static inline size_t align8(size_t n) { return (n + 7) & ~(size_t) 7; }

static const uint8_t ELEM_SIZE[9] = {1, 1, 2, 2, 4, 8, 4, 8, 4};

static inline int32_t d2i(double v) {
  if (v != v) return 0;
  if (v >= 2147483647.0) return 2147483647;
  if (v <= -2147483648.0) return INT32_MIN;
  return (int32_t) v;
}
static inline int64_t d2l(double v) {
  if (v != v) return 0;
  if (v >= 9.223372036854776e18) return INT64_MAX;
  if (v <= -9.223372036854776e18) return INT64_MIN;
  return (int64_t) v;
}

uint32_t crc32(const uint8_t *data, size_t n, uint32_t crc) {
  static uint32_t table[256];
  static bool ready = false;
  if (!ready) {
    for (uint32_t i = 0; i < 256; i++) {
      uint32_t c = i;
      for (int k = 0; k < 8; k++) c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
      table[i] = c;
    }
    ready = true;
  }
  crc = ~crc;
  for (size_t i = 0; i < n; i++) crc = table[(crc ^ data[i]) & 0xFF] ^ (crc >> 8);
  return ~crc;
}

// ---------------------------------------------------------------------------- construction, load
Vm::Vm(uint8_t *arena, size_t bytes) : arena_(arena), bytes_(bytes) {
  half_ = (bytes / 2) & ~(size_t) 7;
  cur_ = arena_;
  other_ = arena_ + half_;
}

bool Vm::fail_load(const char *msg) {
  fault_ = Fault::Runtime;
  fault_msg_ = msg;
  loaded_ = false;
  return false;
}

void Vm::set_fault(Fault f, const char *msg) {
  if (fault_ == Fault::None) {
    fault_ = f;
    fault_msg_ = msg;
  }
}

bool Vm::load(const uint8_t *prog, size_t len) {
  prog_.assign(prog, prog + len);
  const uint8_t *p = prog_.data(), *end = p + len;
  auto need = [&](size_t n) { return (size_t) (end - p) >= n; };
  if (!need(16) || memcmp(p, "PDEB", 4) != 0) return fail_load("not a program (no PDEB header)");
  if (p[4] != BC_VERSION) return fail_load("program version");
  flags_ = p[5];
  p += 8;
  for (int i = 0; i < 4; i++) entry_[i] = (int16_t) rd16(p + 2 * i);
  p += 8;
  if (!need(2)) return fail_load("truncated");
  uint16_t ng = rd16(p);
  p += 2;
  if (!need(ng)) return fail_load("truncated globals");
  gflav_.assign(p, p + ng);
  p += ng;
  if (!need(2)) return fail_load("truncated");
  uint16_t nc = rd16(p);
  p += 2;
  cflav_.clear();
  for (int c = 0; c < nc; c++) {
    if (!need(2)) return fail_load("truncated classes");
    uint16_t nf = rd16(p);
    p += 2;
    if (!need(nf)) return fail_load("truncated classes");
    cflav_.emplace_back(p, p + nf);
    p += nf;
  }
  if (!need(2)) return fail_load("truncated");
  uint16_t nl = rd16(p);
  p += 2;
  if (!need((size_t) nl * 8)) return fail_load("truncated longs");
  longs_.resize(nl);
  for (int i = 0; i < nl; i++, p += 8) memcpy(&longs_[i], p, 8);
  if (!need(2)) return fail_load("truncated");
  uint16_t nd = rd16(p);
  p += 2;
  if (!need((size_t) nd * 8)) return fail_load("truncated doubles");
  doubles_.resize(nd);
  for (int i = 0; i < nd; i++, p += 8) memcpy(&doubles_[i], p, 8);
  if (!need(2)) return fail_load("truncated");
  uint16_t ns = rd16(p);
  p += 2;
  const_area_.clear();
  const_area_.resize(8);
  strings_.clear();
  for (int i = 0; i < ns; i++) {
    if (!need(2)) return fail_load("truncated strings");
    uint16_t n = rd16(p);
    p += 2;
    if (!need((size_t) n * 2)) return fail_load("truncated strings");
    size_t off = const_area_.size();
    const_area_.resize(off + 8 + align8((size_t) n * 2));
    uint8_t *h = &const_area_[off];
    h[0] = T_STR;
    h[1] = 0;
    h[2] = h[3] = 0;
    uint32_t n32 = n;
    memcpy(h + 4, &n32, 4);
    memcpy(h + 8, p, (size_t) n * 2);
    p += (size_t) n * 2;
    strings_.push_back(CONST_REF | (uint32_t) off);
  }
  if (!need(2)) return fail_load("truncated");
  uint16_t nfn = rd16(p);
  p += 2;
  funcs_.resize(nfn);
  for (int i = 0; i < nfn; i++) {
    if (!need(13)) return fail_load("truncated functions");
    Func &f = funcs_[i];
    f.nparams = p[0];
    f.has_this = p[1];
    f.nslots = rd16(p + 2);
    f.maxstack = rd16(p + 4);
    f.returns = p[6];
    f.off = rd32(p + 7);
    f.len = rd16(p + 11);
    p += 13;
    if (f.nslots < 2 + f.nparams) return fail_load("function frame");
  }
  if (!need(4)) return fail_load("truncated");
  uint32_t csize = rd32(p);
  p += 4;
  if ((size_t) (end - p) != csize) return fail_load("code size");
  code_ = p;
  code_len_ = csize;
  for (auto &f : funcs_)
    if ((size_t) f.off + f.len > code_len_ || f.len == 0) return fail_load("function code range");
  for (int i = 0; i < 4; i++)
    if (entry_[i] >= (int) nfn || entry_[i] < -1) return fail_load("entry");
  for (int i = 0; i < nfn; i++)
    if (!verify(i)) return false;
  // state (the stack is the VM's biggest block: probe it so a small device fails the load, not the chip)
  if (!can_alloc((size_t) limits.stack_slots * sizeof(Slot)) || !can_alloc(limits.stack_slots)) {
    fail_load("not enough memory for the VM stack (lower limits.stack_slots)");
    fault_ = Fault::Memory;
    return false;
  }
  globals_.assign(ng, Slot{});
  for (auto &g : globals_) g.l = 0;
  stack_.assign(limits.stack_slots, Slot{});
  tags_.assign(limits.stack_slots, 0);
  frames_.clear();
  frames_.reserve(limits.depth * 2 + 4);
  top_ = 8;
  pixels = 0;
  started_ = done_ = false;
  frame_ = 0;
  fault_ = Fault::None;
  fault_msg_ = "";
  loaded_ = true;
  return true;
}

// The operand-stack depth of every instruction, as the compiler's assembler computed it: each reachable
// instruction has one depth, never below what it pops, never above maxstack; every operand is in range.
bool Vm::verify(int fi) {
  const Func &f = funcs_[fi];
  const uint8_t *c = code_ + f.off;
  const uint32_t n = f.len;
  std::vector<int16_t> depth(n, -1);
  std::vector<uint8_t> start(n, 0);
  // instruction boundaries
  for (uint32_t pc = 0; pc < n;) {
    uint8_t op = c[pc];
    if (op >= OP_COUNT_) return fail_load("bad opcode");
    start[pc] = 1;
    uint32_t len = 1;
    for (const char *q = OP_INFO[op].fmt; *q; q++) len += (*q == 'i' || *q == 'f') ? 4 : (*q == 'H') ? 2 : 1;
    if (pc + len > n) return fail_load("truncated instruction");
    pc += len;
  }
  std::vector<std::pair<uint32_t, int>> work{{0, 0}};
  while (!work.empty()) {
    uint32_t pc = work.back().first;
    int d = work.back().second;
    work.pop_back();
    while (true) {
      if (pc >= n) return fail_load("falls off the end");
      if (!start[pc]) return fail_load("jump into an instruction");
      if (depth[pc] >= 0) {
        if (depth[pc] != d) return fail_load("stack depth mismatch");
        break;
      }
      depth[pc] = (int16_t) d;
      uint8_t op = c[pc];
      const uint8_t *a = c + pc + 1;
      int pops = OP_INFO[op].pops, pushes = OP_INFO[op].pushes;
      uint32_t len = 1;
      for (const char *q = OP_INFO[op].fmt; *q; q++) len += (*q == 'i' || *q == 'f') ? 4 : (*q == 'H') ? 2 : 1;
      switch (op) {
        case OP_LOAD: case OP_STORE:
          if (rd16(a) >= f.nslots) return fail_load("local slot");
          break;
        case OP_IINC:
          if (rd16(a) >= f.nslots) return fail_load("local slot");
          break;
        case OP_GLOAD: case OP_GSTORE:
          if (rd16(a) >= gflav_.size()) return fail_load("global slot");
          break;
        case OP_LCONST: if (rd16(a) >= longs_.size()) return fail_load("long constant"); break;
        case OP_DCONST: if (rd16(a) >= doubles_.size()) return fail_load("double constant"); break;
        case OP_SCONST: if (rd16(a) >= strings_.size()) return fail_load("string constant"); break;
        case OP_ICMP: case OP_LCMP: case OP_FCMP: case OP_DCMP: if (a[0] > 5) return fail_load("compare"); break;
        case OP_ACMP: case OP_SCMP: if (a[0] > 1) return fail_load("compare"); break;
        case OP_CALL: case OP_CALLM: {
          uint16_t t = rd16(a);
          if (t >= funcs_.size()) return fail_load("call target");
          const Func &g = funcs_[t];
          if ((op == OP_CALLM) != ((g.has_this & 1) != 0)) return fail_load("call kind");
          pops = g.nparams + (op == OP_CALLM ? 1 : 0);
          pushes = g.returns ? 1 : 0;
          break;
        }
        case OP_NEWOBJ: if (rd16(a) >= cflav_.size()) return fail_load("class"); break;
        case OP_NEWARR: case OP_ALOAD: case OP_ASTORE: if (a[0] > 8) return fail_load("array kind"); break;
        case OP_NEWMULTI:
          if (a[0] > 8 || a[1] < 2 || a[1] > 8) return fail_load("array dims");
          pops = a[1];
          break;
        case OP_TOSTR: if (a[0] > 7) return fail_load("tostr kind"); break;
        case OP_NATIVE: {
          uint16_t id = rd16(a);
          if (id >= N_COUNT_) return fail_load("native");
          pops = a[2];
          pushes = NATIVE_RET[id] ? 1 : 0;
          break;
        }
        case OP_FAULT: if (a[0] >= 9) return fail_load("fault kind"); break;
        case OP_RET: if (f.returns) return fail_load("RET in a function with a value"); break;
        case OP_RETV: if (!f.returns) return fail_load("RETV in a void function"); break;
        default: break;
      }
      if (d < pops) return fail_load("stack underflow");
      d = d - pops + pushes;
      if (d > f.maxstack) return fail_load("stack overflow");
      if (op == OP_RET || op == OP_RETV || op == OP_FAULT) break;
      if (op == OP_JMP || op == OP_LOOP || op == OP_JZ || op == OP_JNZ) {
        uint16_t t = rd16(a);
        if (t >= n) return fail_load("jump target");
        if (op == OP_JMP || op == OP_LOOP) {
          pc = t;
          continue;
        }
        work.push_back({t, d});
      }
      pc += len;
    }
  }
  return true;
}

// ---------------------------------------------------------------------------- heap
uint8_t *Vm::obj_ptr(uint32_t ref) {
  if (ref == 0) return nullptr;
  if (ref & CONST_REF) {
    uint32_t off = ref & ~CONST_REF;
    if (off + 8 > const_area_.size() || (off & 7)) return nullptr;
    return &const_area_[off];
  }
  if (ref + 8 > top_ || (ref & 7)) return nullptr;
  return cur_ + ref;
}

uint32_t Vm::alloc(uint8_t type, uint8_t kind, uint32_t len, size_t payload) {
  size_t size = 8 + align8(payload);
  if (payload > half_ || top_ + size > half_) {
    if (!gc(size) || top_ + size > half_) {
      set_fault(Fault::Memory, "the sketch ran out of memory (arena)");
      return 0;
    }
  }
  uint32_t ref = (uint32_t) top_;
  uint8_t *h = cur_ + ref;
  h[0] = type;
  h[1] = kind;
  h[2] = h[3] = 0;
  memcpy(h + 4, &len, 4);
  memset(h + 8, 0, size - 8);
  top_ += size;
  api.charged += payload >> 6;
  return ref;
}

uint32_t Vm::new_string(const uint16_t *s, size_t n) {
  if (n > 65536) {
    set_fault(Fault::Memory, "a string grew past 64 K characters");
    return 0;
  }
  uint32_t r = alloc(T_STR, 0, (uint32_t) n, n * 2);
  if (r && n) memcpy(cur_ + r + 8, s, n * 2);
  return r;
}

uint32_t Vm::new_string_ascii(const char *s, size_t n) {
  uint32_t r = alloc(T_STR, 0, (uint32_t) n, n * 2);
  if (r) {
    uint16_t *d = (uint16_t *) (cur_ + r + 8);
    for (size_t i = 0; i < n; i++) d[i] = (uint8_t) s[i];
  }
  return r;
}

uint32_t Vm::new_array(uint8_t kind, int32_t n) {
  if (n < 0) {
    set_fault(Fault::Runtime, "negative array size");
    return 0;
  }
  if (n > 4000000) {
    set_fault(Fault::Memory, "an array is too big");
    return 0;
  }
  return alloc(T_ARR, kind, (uint32_t) n, (size_t) n * ELEM_SIZE[kind]);
}

bool Vm::str(uint32_t ref, const uint16_t *&s, uint32_t &n) {
  uint8_t *h = obj_ptr(ref);
  if (!h || h[0] != T_STR) {
    set_fault(Fault::Runtime, ref ? "not a string" : "a null String was used (NullPointerException)");
    return false;
  }
  memcpy(&n, h + 4, 4);
  s = (const uint16_t *) (h + 8);
  return true;
}

bool Vm::arr(uint32_t ref, uint8_t kind, uint8_t *&data, uint32_t &n) {
  uint8_t *h = obj_ptr(ref);
  if (!h || h[0] != T_ARR || (kind != 0xFF && h[1] != kind)) {
    set_fault(Fault::Runtime, ref ? "not an array of this type" : "the array is null");
    return false;
  }
  memcpy(&n, h + 4, 4);
  data = h + 8;
  return true;
}

uint8_t Vm::arr_kind(uint32_t ref) {
  uint8_t *h = obj_ptr(ref);
  return (h && h[0] == T_ARR) ? h[1] : 0xFF;
}

static size_t obj_size(const uint8_t *h) {
  uint32_t n;
  memcpy(&n, h + 4, 4);
  switch (h[0]) {
    case T_STR: return 8 + align8((size_t) n * 2);
    case T_ARR: return 8 + align8((size_t) n * ELEM_SIZE[h[1] <= 8 ? h[1] : 8]);
    case T_OBJ: return 8 + (size_t) n * 8;
    case T_FONT: return 16;
    default: return 0;
  }
}

// Cheney copy into the other half. Roots: globals holding references, tagged stack slots,
// pixels[]. Everything reachable is copied; references inside copied objects are then fixed.
uint32_t Vm::copy_obj(uint32_t ref) {
  if (ref == 0 || (ref & CONST_REF)) return ref;
  if (ref + 8 > top_ || (ref & 7)) return ref;          // not a valid reference: leave it (never followed)
  uint8_t *h = cur_ + ref;
  if (h[0] == T_FWD) {
    uint32_t to;
    memcpy(&to, h + 4, 4);
    return to;
  }
  size_t size = obj_size(h);
  if (size == 0 || ref + size > top_) return ref;
  uint32_t to = (uint32_t) gc_top_;
  memcpy(other_ + to, h, size);
  gc_top_ += size;
  h[0] = T_FWD;
  memcpy(h + 4, &to, 4);
  return to;
}

bool Vm::gc(size_t need) {
  gc_top_ = 8;
  for (size_t i = 0; i < globals_.size(); i++)
    if (gflav_[i] == 'R') globals_[i].r = copy_obj(globals_[i].r);
  for (uint32_t i = 0; i < sp_hi_; i++)
    if (tags_[i]) stack_[i].r = copy_obj(stack_[i].r);
  pixels = copy_obj(pixels);
  for (auto &r : troots_) r = copy_obj(r);
  // scan
  size_t scan = 8;
  while (scan < gc_top_) {
    uint8_t *h = other_ + scan;
    uint32_t n;
    memcpy(&n, h + 4, 4);
    if (h[0] == T_ARR && h[1] == K_REF) {
      uint32_t *e = (uint32_t *) (h + 8);
      for (uint32_t k = 0; k < n; k++) e[k] = copy_obj(e[k]);
    } else if (h[0] == T_OBJ) {
      uint16_t cls = (uint16_t) (h[2] | (h[3] << 8));
      Slot *e = (Slot *) (h + 8);
      if (cls < cflav_.size())
        for (uint32_t k = 0; k < n && k < cflav_[cls].size(); k++)
          if (cflav_[cls][k] == 'R') e[k].r = copy_obj(e[k].r);
    }
    scan += obj_size(h);
  }
  uint8_t *t = cur_;
  cur_ = other_;
  other_ = t;
  top_ = gc_top_;
  gcs_++;
  return top_ + need <= half_;
}

uint32_t Vm::build_multi(uint8_t kind, const int32_t *sizes, int nd, int level) {
  if (level == nd - 1) return new_array(kind, sizes[level]);
  uint32_t r = new_array(K_REF, sizes[level]);
  if (!r) return 0;
  troots_.push_back(r);
  size_t slot = troots_.size() - 1;
  for (int32_t i = 0; i < sizes[level]; i++) {
    uint32_t child = build_multi(kind, sizes, nd, level + 1);
    if (!child) {
      troots_.resize(slot);
      return 0;
    }
    uint8_t *h = cur_ + troots_[slot];               // (it may have moved)
    memcpy(h + 8 + (size_t) i * 4, &child, 4);
  }
  r = troots_[slot];
  troots_.resize(slot);
  return r;
}

// ---------------------------------------------------------------------------- the interpreter
bool Vm::check_budget() {
  if (steps_ > limits.steps) {
    set_fault(Fault::Budget, "the frame took too many steps");
    return false;
  }
  if (now_ms) {
    const uint32_t t = now_ms();
    if ((int32_t) (t - deadline_) > 0) {
      set_fault(Fault::Deadline, "the frame ran out of time");
      return false;
    }
    if (yield && t - last_yield_ >= 30) {
      yield();
      last_yield_ = now_ms();
    }
  }
  next_check_ = steps_ + 4096;
  return true;
}

#define FAIL(kind, msg) do { set_fault(kind, msg); goto fault; } while (0)
#define PUSHI(v) do { S[sp].i = (v); T[sp++] = 0; } while (0)
#define PUSHL(v) do { S[sp].l = (v); T[sp++] = 0; } while (0)
#define PUSHF(v) do { S[sp].f = (v); T[sp++] = 0; } while (0)
#define PUSHD(v) do { S[sp].d = (v); T[sp++] = 0; } while (0)
#define PUSHR(v) do { S[sp].r = (v); T[sp++] = 1; } while (0)
#define TICK(n) do { steps_ += (n); if (steps_ >= next_check_ && !check_budget()) goto fault; } while (0)
#define SYNC() (sp_hi_ = sp)

static inline bool cmp_i(int op, int64_t a, int64_t b) {
  switch (op) {
    case 0: return a == b;
    case 1: return a != b;
    case 2: return a < b;
    case 3: return a > b;
    case 4: return a <= b;
    default: return a >= b;
  }
}
static inline bool cmp_f(int op, double a, double b) {
  switch (op) {
    case 0: return a == b;
    case 1: return a != b;
    case 2: return a < b;
    case 3: return a > b;
    case 4: return a <= b;
    default: return a >= b;
  }
}

bool Vm::run(int fi, bool) {
  if (fi < 0) return true;
  Slot *S = stack_.data();
  uint8_t *T = tags_.data();
  const uint32_t cap = (uint32_t) stack_.size();
  frames_.clear();
  depth_ = 0;
  const Func *f = &funcs_[fi];
  if ((uint32_t) f->nslots + f->maxstack > cap) {
    set_fault(Fault::Depth, "the sketch needs too deep a stack");
    return false;
  }
  for (uint32_t k = 0; k < f->nslots; k++) {
    S[k].l = 0;
    T[k] = 0;
  }
  uint32_t base = 0, sp = f->nslots;
  const uint8_t *code = code_ + f->off;
  uint32_t pc = 0;
  uint16_t cur_fn = (uint16_t) fi;
  for (;;) {
    const uint8_t op = code[pc++];
    const uint8_t *a = code + pc;
#ifdef PDE_PROFILE
    prof_ops++;
#endif
    switch (op) {
      case OP_NOP: break;
      case OP_ICONST: PUSHI(rdi32(a)); pc += 4; break;
      case OP_FCONST: PUSHF(rdf32(a)); pc += 4; break;
      case OP_LCONST: PUSHL(longs_[rd16(a)]); pc += 2; break;
      case OP_DCONST: PUSHD(doubles_[rd16(a)]); pc += 2; break;
      case OP_SCONST: PUSHR(strings_[rd16(a)]); pc += 2; break;
      case OP_NULL: PUSHR(0); break;
      case OP_LOAD: { uint32_t s = base + rd16(a); S[sp] = S[s]; T[sp++] = T[s]; pc += 2; break; }
      case OP_STORE: { uint32_t s = base + rd16(a); --sp; S[s] = S[sp]; T[s] = T[sp]; pc += 2; break; }
      case OP_GLOAD: { uint16_t g = rd16(a); S[sp] = globals_[g]; T[sp++] = gflav_[g] == 'R'; pc += 2; break; }
      case OP_GSTORE: { uint16_t g = rd16(a); globals_[g] = S[--sp]; pc += 2; break; }
      case OP_DUP: S[sp] = S[sp - 1]; T[sp] = T[sp - 1]; sp++; break;
      case OP_DUP2:
        S[sp] = S[sp - 2]; T[sp] = T[sp - 2];
        S[sp + 1] = S[sp - 1]; T[sp + 1] = T[sp - 1];
        sp += 2;
        break;
      case OP_DUP_X1: {  // ..., v2, v1 -> ..., v1, v2, v1
        Slot v1 = S[sp - 1], v2 = S[sp - 2];
        uint8_t t1 = T[sp - 1], t2 = T[sp - 2];
        S[sp - 2] = v1; T[sp - 2] = t1;
        S[sp - 1] = v2; T[sp - 1] = t2;
        S[sp] = v1; T[sp] = t1;
        sp++;
        break;
      }
      case OP_DUP_X2: {  // ..., v3, v2, v1 -> ..., v1, v3, v2, v1
        Slot v1 = S[sp - 1], v2 = S[sp - 2], v3 = S[sp - 3];
        uint8_t t1 = T[sp - 1], t2 = T[sp - 2], t3 = T[sp - 3];
        S[sp - 3] = v1; T[sp - 3] = t1;
        S[sp - 2] = v3; T[sp - 2] = t3;
        S[sp - 1] = v2; T[sp - 1] = t2;
        S[sp] = v1; T[sp] = t1;
        sp++;
        break;
      }
      case OP_POP: sp--; break;
      case OP_SWAP: {
        Slot v = S[sp - 1]; uint8_t t = T[sp - 1];
        S[sp - 1] = S[sp - 2]; T[sp - 1] = T[sp - 2];
        S[sp - 2] = v; T[sp - 2] = t;
        break;
      }
      // ---- int
      case OP_IADD: sp--; S[sp - 1].i = (int32_t) ((uint32_t) S[sp - 1].i + (uint32_t) S[sp].i); break;
      case OP_ISUB: sp--; S[sp - 1].i = (int32_t) ((uint32_t) S[sp - 1].i - (uint32_t) S[sp].i); break;
      case OP_IMUL: sp--; S[sp - 1].i = (int32_t) ((uint32_t) S[sp - 1].i * (uint32_t) S[sp].i); break;
      case OP_IDIV: {
        sp--;
        int32_t x = S[sp - 1].i, y = S[sp].i;
        if (y == 0) FAIL(Fault::Runtime, "division by zero");
        S[sp - 1].i = (y == -1) ? (int32_t) (0u - (uint32_t) x) : x / y;
        break;
      }
      case OP_IREM: {
        sp--;
        int32_t x = S[sp - 1].i, y = S[sp].i;
        if (y == 0) FAIL(Fault::Runtime, "division by zero");
        S[sp - 1].i = (y == -1) ? 0 : x % y;
        break;
      }
      case OP_INEG: S[sp - 1].i = (int32_t) (0u - (uint32_t) S[sp - 1].i); break;
      case OP_IAND: sp--; S[sp - 1].i &= S[sp].i; break;
      case OP_IOR: sp--; S[sp - 1].i |= S[sp].i; break;
      case OP_IXOR: sp--; S[sp - 1].i ^= S[sp].i; break;
      case OP_ISHL: sp--; S[sp - 1].i = (int32_t) ((uint32_t) S[sp - 1].i << (S[sp].i & 31)); break;
      case OP_ISHR: sp--; S[sp - 1].i = S[sp - 1].i >> (S[sp].i & 31); break;
      case OP_IUSHR: sp--; S[sp - 1].i = (int32_t) ((uint32_t) S[sp - 1].i >> (S[sp].i & 31)); break;
      case OP_INOT: S[sp - 1].i = ~S[sp - 1].i; break;
      // ---- long
      case OP_LADD: sp--; S[sp - 1].l = (int64_t) ((uint64_t) S[sp - 1].l + (uint64_t) S[sp].l); break;
      case OP_LSUB: sp--; S[sp - 1].l = (int64_t) ((uint64_t) S[sp - 1].l - (uint64_t) S[sp].l); break;
      case OP_LMUL: sp--; S[sp - 1].l = (int64_t) ((uint64_t) S[sp - 1].l * (uint64_t) S[sp].l); break;
      case OP_LDIV: {
        sp--;
        int64_t x = S[sp - 1].l, y = S[sp].l;
        if (y == 0) FAIL(Fault::Runtime, "division by zero");
        S[sp - 1].l = (y == -1) ? (int64_t) (0ull - (uint64_t) x) : x / y;
        break;
      }
      case OP_LREM: {
        sp--;
        int64_t x = S[sp - 1].l, y = S[sp].l;
        if (y == 0) FAIL(Fault::Runtime, "division by zero");
        S[sp - 1].l = (y == -1) ? 0 : x % y;
        break;
      }
      case OP_LNEG: S[sp - 1].l = (int64_t) (0ull - (uint64_t) S[sp - 1].l); break;
      case OP_LAND: sp--; S[sp - 1].l &= S[sp].l; break;
      case OP_LOR: sp--; S[sp - 1].l |= S[sp].l; break;
      case OP_LXOR: sp--; S[sp - 1].l ^= S[sp].l; break;
      case OP_LSHL: sp--; S[sp - 1].l = (int64_t) ((uint64_t) S[sp - 1].l << (S[sp].i & 63)); break;
      case OP_LSHR: sp--; S[sp - 1].l = S[sp - 1].l >> (S[sp].i & 63); break;
      case OP_LUSHR: sp--; S[sp - 1].l = (int64_t) ((uint64_t) S[sp - 1].l >> (S[sp].i & 63)); break;
      case OP_LNOT: S[sp - 1].l = ~S[sp - 1].l; break;
      // ---- float (Java float: each operation rounds to 32 bits)
      case OP_FADD: sp--; S[sp - 1].f = S[sp - 1].f + S[sp].f; break;
      case OP_FSUB: sp--; S[sp - 1].f = S[sp - 1].f - S[sp].f; break;
      case OP_FMUL: sp--; S[sp - 1].f = S[sp - 1].f * S[sp].f; break;
      case OP_FDIV: sp--; S[sp - 1].f = S[sp - 1].f / S[sp].f; break;
      case OP_FREM: sp--; S[sp - 1].f = (float) std::fmod((double) S[sp - 1].f, (double) S[sp].f); break;
      case OP_FNEG: S[sp - 1].f = -S[sp - 1].f; break;
      case OP_DADD: sp--; S[sp - 1].d = S[sp - 1].d + S[sp].d; break;
      case OP_DSUB: sp--; S[sp - 1].d = S[sp - 1].d - S[sp].d; break;
      case OP_DMUL: sp--; S[sp - 1].d = S[sp - 1].d * S[sp].d; break;
      case OP_DDIV: sp--; S[sp - 1].d = S[sp - 1].d / S[sp].d; break;
      case OP_DREM: sp--; S[sp - 1].d = std::fmod(S[sp - 1].d, S[sp].d); break;
      case OP_DNEG: S[sp - 1].d = -S[sp - 1].d; break;
      case OP_BNOT: S[sp - 1].i = S[sp - 1].i ? 0 : 1; break;
      // ---- conversions
      case OP_I2L: S[sp - 1].l = (int64_t) S[sp - 1].i; break;
      case OP_I2F: S[sp - 1].f = (float) S[sp - 1].i; break;
      case OP_I2D: S[sp - 1].d = (double) S[sp - 1].i; break;
      case OP_L2I: S[sp - 1].i = (int32_t) (uint32_t) (uint64_t) S[sp - 1].l; break;
      case OP_L2F: S[sp - 1].f = (float) (double) S[sp - 1].l; break;   // as Python: f32(float(v))
      case OP_L2D: S[sp - 1].d = (double) S[sp - 1].l; break;
      case OP_F2I: S[sp - 1].i = d2i((double) S[sp - 1].f); break;
      case OP_F2L: S[sp - 1].l = d2l((double) S[sp - 1].f); break;
      case OP_F2D: S[sp - 1].d = (double) S[sp - 1].f; break;
      case OP_D2I: S[sp - 1].i = d2i(S[sp - 1].d); break;
      case OP_D2L: S[sp - 1].l = d2l(S[sp - 1].d); break;
      case OP_D2F: S[sp - 1].f = (float) S[sp - 1].d; break;
      case OP_I2B: S[sp - 1].i = (int8_t) S[sp - 1].i; break;
      case OP_I2S: S[sp - 1].i = (int16_t) S[sp - 1].i; break;
      case OP_I2C: S[sp - 1].i = (uint16_t) S[sp - 1].i; break;
      // ---- compare
      case OP_ICMP: sp--; S[sp - 1].i = cmp_i(a[0], S[sp - 1].i, S[sp].i); T[sp - 1] = 0; pc++; break;
      case OP_LCMP: sp--; S[sp - 1].i = cmp_i(a[0], S[sp - 1].l, S[sp].l); T[sp - 1] = 0; pc++; break;
      case OP_FCMP: sp--; S[sp - 1].i = cmp_f(a[0], S[sp - 1].f, S[sp].f); T[sp - 1] = 0; pc++; break;
      case OP_DCMP: sp--; S[sp - 1].i = cmp_f(a[0], S[sp - 1].d, S[sp].d); T[sp - 1] = 0; pc++; break;
      case OP_ACMP: {
        sp--;
        bool eq = S[sp - 1].r == S[sp].r;
        S[sp - 1].i = a[0] == 0 ? eq : !eq;
        T[sp - 1] = 0;
        pc++;
        break;
      }
      case OP_SCMP: {
        sp--;
        uint32_t x = S[sp - 1].r, y = S[sp].r;
        bool eq;
        if (x == y) eq = true;
        else if (!x || !y) eq = false;
        else {
          const uint16_t *sx, *sy;
          uint32_t nx, ny;
          if (!str(x, sx, nx) || !str(y, sy, ny)) goto fault;
          eq = nx == ny && memcmp(sx, sy, (size_t) nx * 2) == 0;
        }
        S[sp - 1].i = a[0] == 0 ? eq : !eq;
        T[sp - 1] = 0;
        pc++;
        break;
      }
      // ---- control
      case OP_JMP: pc = rd16(a); break;
      case OP_JZ: pc = S[--sp].i == 0 ? rd16(a) : pc + 2; break;
      case OP_JNZ: pc = S[--sp].i != 0 ? rd16(a) : pc + 2; break;
      case OP_LOOP: pc = rd16(a); TICK(1); break;
      case OP_CALL:
      case OP_CALLM: {
        const uint16_t t = rd16(a);
        pc += 2;
        const Func &g = funcs_[t];
        const bool m = op == OP_CALLM;
        const uint32_t n = g.nparams;
        const uint32_t argbase = sp - n - (m ? 1 : 0);
        Slot self{};
        if (m) {
          self = S[sp - 1];
          if (self.r == 0) FAIL(Fault::Runtime, "calling a method on null (NullPointerException)");
        }
        if (argbase + (uint32_t) g.nslots + g.maxstack > cap) FAIL(Fault::Depth, "the sketch recursed too deeply");
        const bool counted = !(g.has_this & 2);
        if (counted && ++depth_ > limits.depth) FAIL(Fault::Depth, "the sketch calls itself too deeply");
        frames_.push_back(Frame{cur_fn, pc, base, argbase, counted});
        memmove(&S[argbase + 2], &S[argbase], n * sizeof(Slot));
        memmove(&T[argbase + 2], &T[argbase], n);
        S[argbase].l = 0;
        T[argbase] = 0;
        S[argbase + 1] = self;
        T[argbase + 1] = m ? 1 : 0;
        for (uint32_t k = argbase + 2 + n; k < argbase + g.nslots; k++) {
          S[k].l = 0;
          T[k] = 0;
        }
        base = argbase;
        sp = argbase + g.nslots;
        cur_fn = t;
        code = code_ + g.off;
        pc = 0;
        TICK(1);
        break;
      }
      case OP_RET:
      case OP_RETV: {
        Slot v{};
        uint8_t vt = 0;
        if (op == OP_RETV) {
          v = S[sp - 1];
          vt = T[sp - 1];
        }
        if (frames_.empty()) {
          sp_hi_ = 0;
          return true;
        }
        Frame fr = frames_.back();
        frames_.pop_back();
        if (fr.counted) depth_--;
        sp = fr.sp;
        base = fr.base;
        cur_fn = fr.fn;
        code = code_ + funcs_[cur_fn].off;
        pc = fr.pc;
        if (op == OP_RETV) {
          S[sp] = v;
          T[sp++] = vt;
        }
        break;
      }
      // ---- objects
      case OP_NEWOBJ: {
        uint16_t c = rd16(a);
        pc += 2;
        SYNC();
        uint32_t nf = (uint32_t) cflav_[c].size();
        uint32_t r = alloc(T_OBJ, 0, nf, (size_t) nf * 8);
        if (!r) goto fault;
        cur_[r + 2] = (uint8_t) (c & 0xFF);
        cur_[r + 3] = (uint8_t) (c >> 8);
        PUSHR(r);
        break;
      }
      case OP_GETF:
      case OP_PUTF: {
        uint16_t k = rd16(a);
        pc += 2;
        uint32_t r = op == OP_GETF ? S[sp - 1].r : S[sp - 2].r;
        uint8_t *h = obj_ptr(r);
        if (!h) FAIL(Fault::Runtime, "a null object was used (NullPointerException)");
        uint32_t nf;
        memcpy(&nf, h + 4, 4);
        uint16_t cls = (uint16_t) (h[2] | (h[3] << 8));
        if (h[0] != T_OBJ || k >= nf || cls >= cflav_.size()) FAIL(Fault::Runtime, "not an object");
        Slot *fs = (Slot *) (h + 8);
        if (op == OP_GETF) {
          S[sp - 1] = fs[k];
          T[sp - 1] = cflav_[cls][k] == 'R';
        } else {
          fs[k] = S[sp - 1];
          sp -= 2;
        }
        break;
      }
      case OP_NEWARR: {
        uint8_t kind = a[0];
        pc++;
        SYNC();
        uint32_t r = new_array(kind, S[sp - 1].i);
        if (!r) goto fault;
        S[sp - 1].r = r;
        T[sp - 1] = 1;
        break;
      }
      case OP_NEWMULTI: {
        uint8_t kind = a[0], nd = a[1];
        pc += 2;
        int32_t sizes[8];
        double total = ELEM_SIZE[kind];
        for (int k = 0; k < nd; k++) {
          sizes[k] = S[sp - nd + k].i;
          total *= sizes[k] > 0 ? sizes[k] : 0;
        }
        for (int k = 0; k < nd; k++)
          if (sizes[k] < 0) FAIL(Fault::Runtime, "negative array size");
        if (total > (double) half_) FAIL(Fault::Memory, "an array too big for the VM");
        sp -= nd;
        SYNC();
        uint32_t r = build_multi(kind, sizes, nd, 0);
        if (!r) goto fault;
        PUSHR(r);
        break;
      }
      case OP_ALOAD: {
        uint8_t kind = a[0];
        pc++;
        uint8_t *data;
        uint32_t n;
        if (!arr(S[sp - 2].r, kind, data, n)) goto fault;
        int32_t i = S[sp - 1].i;
        if (i < 0 || (uint32_t) i >= n) FAIL(Fault::Runtime, "index outside the array");
        sp--;
        Slot &o = S[sp - 1];
        T[sp - 1] = 0;
        switch (kind) {
          case K_BOOLEAN: o.i = data[i]; break;
          case K_BYTE: o.i = ((int8_t *) data)[i]; break;
          case K_SHORT: { int16_t v; memcpy(&v, data + 2 * i, 2); o.i = v; break; }
          case K_CHAR: { uint16_t v; memcpy(&v, data + 2 * i, 2); o.i = v; break; }
          case K_INT: memcpy(&o.i, data + 4 * (size_t) i, 4); break;
          case K_LONG: memcpy(&o.l, data + 8 * (size_t) i, 8); break;
          case K_FLOAT: memcpy(&o.f, data + 4 * (size_t) i, 4); break;
          case K_DOUBLE: memcpy(&o.d, data + 8 * (size_t) i, 8); break;
          default: memcpy(&o.r, data + 4 * (size_t) i, 4); T[sp - 1] = 1; break;
        }
        break;
      }
      case OP_ASTORE: {
        uint8_t kind = a[0];
        pc++;
        uint8_t *data;
        uint32_t n;
        if (!arr(S[sp - 3].r, kind, data, n)) goto fault;
        int32_t i = S[sp - 2].i;
        if (i < 0 || (uint32_t) i >= n) FAIL(Fault::Runtime, "index outside the array");
        const Slot &v = S[sp - 1];
        switch (kind) {
          case K_BOOLEAN: data[i] = v.i ? 1 : 0; break;
          case K_BYTE: data[i] = (uint8_t) v.i; break;
          case K_SHORT: case K_CHAR: { uint16_t x = (uint16_t) v.i; memcpy(data + 2 * i, &x, 2); break; }
          case K_INT: memcpy(data + 4 * (size_t) i, &v.i, 4); break;
          case K_LONG: memcpy(data + 8 * (size_t) i, &v.l, 8); break;
          case K_FLOAT: memcpy(data + 4 * (size_t) i, &v.f, 4); break;
          case K_DOUBLE: memcpy(data + 8 * (size_t) i, &v.d, 8); break;
          default: memcpy(data + 4 * (size_t) i, &v.r, 4); break;
        }
        sp -= 3;
        break;
      }
      case OP_ALEN: {
        uint8_t *data;
        uint32_t n;
        if (!arr(S[sp - 1].r, 0xFF, data, n)) goto fault;
        S[sp - 1].i = (int32_t) n;
        T[sp - 1] = 0;
        break;
      }
      // ---- strings
      case OP_TOSTR: {
        uint8_t kind = a[0];
        pc++;
        char buf[64];
        int len = 0;
        Slot &v = S[sp - 1];
        SYNC();
        switch (kind) {
          case TS_INT: len = snprintf(buf, sizeof buf, "%ld", (long) v.i); break;
          case TS_LONG: len = snprintf(buf, sizeof buf, "%lld", (long long) v.l); break;
          case TS_CHAR: {
            uint16_t u = (uint16_t) v.i;
            uint32_t r = new_string(&u, 1);
            if (!r) goto fault;
            v.r = r;
            T[sp - 1] = 1;
            continue;
          }
          case TS_BOOLEAN: len = snprintf(buf, sizeof buf, "%s", v.i ? "true" : "false"); break;
          case TS_FLOAT: len = jfloat_str((double) v.f, true, buf, sizeof buf); break;
          case TS_DOUBLE: len = jfloat_str(v.d, false, buf, sizeof buf); break;
          case TS_TEXTFLOAT: len = text_float(v.d, buf, sizeof buf); break;
          default:  // String: null reads "null"
            if (v.r != 0) continue;
            len = snprintf(buf, sizeof buf, "null");
            break;
        }
        uint32_t r = new_string_ascii(buf, (size_t) len);
        if (!r) goto fault;
        S[sp - 1].r = r;
        T[sp - 1] = 1;
        break;
      }
      case OP_SCAT: {
        const uint16_t *x, *y;
        uint32_t nx, ny;
        if (!str(S[sp - 2].r, x, nx) || !str(S[sp - 1].r, y, ny)) goto fault;
        if ((size_t) nx + ny > 65536) FAIL(Fault::Memory, "a string grew past 64 K characters");
        SYNC();
        uint32_t r = alloc(T_STR, 0, nx + ny, ((size_t) nx + ny) * 2);
        if (!r) goto fault;
        str(S[sp - 2].r, x, nx);          // (the collector may have moved them)
        str(S[sp - 1].r, y, ny);
        memcpy(cur_ + r + 8, x, (size_t) nx * 2);
        memcpy(cur_ + r + 8 + (size_t) nx * 2, y, (size_t) ny * 2);
        sp--;
        S[sp - 1].r = r;
        T[sp - 1] = 1;
        break;
      }
      // ---- natives
      case OP_NATIVE: {
        uint16_t id = rd16(a);
        uint8_t argc = a[2], flags = a[3];
        pc += 4;
        SYNC();
        Slot ret{};
        uint8_t rt = 0;
#ifdef PDE_PROFILE
        const auto p0 = std::chrono::steady_clock::now();
        bool ok = call_native(*this, id, argc, flags, &S[sp - argc], &T[sp - argc], ret, rt);
        prof_native_ns[id] += (uint64_t) std::chrono::duration_cast<std::chrono::nanoseconds>(
                                  std::chrono::steady_clock::now() - p0).count();
        prof_native_calls[id]++;
#else
        bool ok = call_native(*this, id, argc, flags, &S[sp - argc], &T[sp - argc], ret, rt);
#endif
        if (api.fault != Fault::None) set_fault(api.fault, api.fault_msg);
        if (!ok || fault_ != Fault::None) {
          if (fault_ == Fault::None) set_fault(Fault::Runtime, "a call failed");
          goto fault;
        }
        sp -= argc;
        if (NATIVE_RET[id]) {
          S[sp] = ret;
          T[sp++] = rt;
        }
        uint64_t c = api.charged;
        TICK(1 + (uint32_t) (c - charged_seen_));
        charged_seen_ = c;
        break;
      }
      case OP_IINC: {
        uint32_t s = base + rd16(a);
        S[s].i = (int32_t) ((uint32_t) S[s].i + (uint32_t) (int32_t) (int8_t) a[2]);
        pc += 3;
        break;
      }
      case OP_FAULT: FAIL((Fault) a[0], "the program stopped");
      case OP_PIXELS:
        if (!pixels) FAIL(Fault::Runtime, "pixels[] is empty until loadPixels()");
        PUSHR(pixels);
        break;
      case OP_FRAMECOUNT: PUSHI(api.frame_count); break;
      case OP_NULLCHK:
        if (S[sp - 1].r == 0) FAIL(Fault::Runtime, "a null value was used (NullPointerException)");
        break;
      default: FAIL(Fault::Runtime, "bad opcode");
    }
  }
fault:
  frames_.clear();
  sp_hi_ = 0;
  if (fault_ == Fault::None) set_fault(Fault::Runtime, "fault");
  return false;
}

bool Vm::next_frame() {
  if (!loaded_ || fault_ != Fault::None || done_) return false;
  auto reset = [&]() {
    steps_ = 0;
    next_check_ = 4096;
    charged_seen_ = api.charged;
    if (now_ms) deadline_ = now_ms() + limits.ms;
  };
  if (!started_) {
    started_ = true;
    reset();
    if (!run(entry_[0], false)) return false;
    api.rand.seed(0);            // the oracle: randomSeed(0) and noiseSeed(0) before setup()
    api.rand.noise_seed(0);
    reset();
    if (!run(entry_[1], false)) return false;
    if (!run(entry_[2], false)) return false;
    api.reset_shape();
  }
  reset();
  api.frame_count++;
  // PGraphics.beginDraw() before every draw(): the matrix starts from identity; the style and
  // the push stack carry over (python/immanence/pde/__init__.py Sketch.next_frame)
  api.reset_matrix();
  api.begin_frame3d();          // P3D: depth cleared, lights off, the normal back to +z
  if (!run(entry_[3], false)) return false;
  api.reset_shape();
  frame_++;
  last_steps_ = steps_;
  if (top_ > half_ / 2) gc(0);   // between frames: only the globals are roots
  if (entry_[3] < 0 || !api.looping || api.exited) done_ = true;
  return true;
}

}  // namespace pde
