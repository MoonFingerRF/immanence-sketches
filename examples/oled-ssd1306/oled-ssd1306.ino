// immanence-sketches: pieces on a 128x64 I2C SSD1306 OLED, any ESP32 (no PSRAM needed).
//
// The 240x128 canvas is fitted into 128x64 with immanence::fit (half scale: 120x64, centred) and
// made 1-bit with the reference's 4x4 ordered dither, applied at each OLED pixel so the pattern
// stays regular after scaling. The VM arena comes from internal RAM (up to 96 KB; these pieces
// use under 2 KB of it). The first frames of each piece are checked against the reference crc32
// and the result printed on the serial monitor.
//
// Libraries: immanence-sketches, Adafruit SSD1306, Adafruit GFX Library.
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Wire.h>

#include <new>
#include <immanence.h>

#include "ridgelines.h"
#include "three_bodies.h"
#include "wanderers.h"

// ---- wiring and panel (change these for your module) -----------------------------------------
constexpr int PIN_SDA = 21;
constexpr int PIN_SCL = 22;
constexpr uint8_t OLED_ADDR = 0x3C;   // some modules use 0x3D
constexpr int OLED_W = 128;
constexpr int OLED_H = 64;
constexpr uint32_t I2C_HZ = 400000;

// ---- pieces and timing ------------------------------------------------------------------------
struct Piece {
  const char *name;
  const uint8_t *prog;
  size_t len;
  const uint32_t *golden;
  int golden_n;
};
const Piece PIECES[] = {
    {"wanderers", wanderers, wanderers_len, wanderers_golden, wanderers_golden_n},
    {"three bodies", three_bodies, three_bodies_len, three_bodies_golden, three_bodies_golden_n},
    {"ridgelines", ridgelines, ridgelines_len, ridgelines_golden, ridgelines_golden_n},
};
constexpr int N_PIECES = sizeof(PIECES) / sizeof(PIECES[0]);
constexpr uint32_t PIECE_MS = 60000;   // how long each piece stays on screen
constexpr uint32_t FRAME_MS = 50;      // at most ~20 frames per second
// The arena, from internal RAM. A classic ESP32's largest free block is about 110 KB, so 96 KB
// is near the top; these pieces use under 2 KB of it, so smaller is fine too.
const size_t ARENA_SIZES[] = {96 * 1024, 80 * 1024, 64 * 1024, 48 * 1024, 32 * 1024};
// The VM's operand stack: 2048 slots of 8 bytes (16 KB) instead of the default 8192 (64 KB in one
// block, which a PSRAM-less ESP32 cannot spare beside the arena). Plenty for these pieces.
constexpr uint32_t STACK_SLOTS = 2048;

Adafruit_SSD1306 oled(OLED_W, OLED_H, &Wire, -1);

// The reference dither (immanence::ink_at), evaluated at the destination pixel.
const uint8_t BAYER4[4][4] = {{0, 8, 2, 10}, {12, 4, 14, 6}, {3, 11, 1, 9}, {15, 7, 13, 5}};

void show(const uint8_t *grey) {
  uint8_t *buf = oled.getBuffer();   // SSD1306 pages: byte x + (y / 8) * width, bit y & 7, 1 = lit
  memset(buf, 0, OLED_W * OLED_H / 8);
  immanence::fit(grey, OLED_W, OLED_H, [buf](int x, int y, uint8_t g) {
    bool ink = ((255 - g) * 16 + 127) / 255 > BAYER4[y & 3][x & 3];
    if (!ink) buf[x + (y >> 3) * OLED_W] |= (uint8_t) (1u << (y & 7));   // paper (white) = lit
  });
  oled.display();
}

// ---- the VM -----------------------------------------------------------------------------------
uint8_t *arena = nullptr;
size_t arena_bytes = 0;
pde::Vm *vm = nullptr;
int piece = -1;
uint32_t piece_start = 0, last_frame = 0;
int checked = 0, matched = 0;

// The VM object (about 71 KB: the canvas plus drawing state) gets its block first and keeps it
// for every piece; the arena then takes the largest size that still leaves room for the VM's
// operand stack, which needs a block of its own.
void *vm_mem = nullptr;

void alloc_memory() {
  vm_mem = ::operator new(sizeof(pde::Vm), std::nothrow);   // aligned like `new pde::Vm`
  if (!vm_mem) return;
  for (size_t n : ARENA_SIZES) {
    arena = (uint8_t *) malloc(n);
    if (!arena) continue;
    void *stack = malloc(STACK_SLOTS * 9 + 4096);
    free(stack);
    if (stack) {
      arena_bytes = n;
      return;
    }
    free(arena);
    arena = nullptr;
  }
}

uint32_t now32() { return (uint32_t) millis(); }
void let_others_run() { delay(1); }

void start_piece(int i) {
  if (vm) vm->~Vm();   // a fresh VM for each piece, in the same block
  vm = new (vm_mem) pde::Vm(arena, arena_bytes);
  vm->now_ms = now32;
  vm->yield = let_others_run;
  vm->limits.stack_slots = STACK_SLOTS;
  piece = i;
  piece_start = millis();
  checked = matched = 0;
  if (!vm->load(PIECES[i].prog, PIECES[i].len)) Serial.printf("%s: load failed: %s\n", PIECES[i].name, vm->fault_msg());
  else Serial.printf("%s: running\n", PIECES[i].name);
}

void setup() {
  Serial.begin(115200);
  Wire.begin(PIN_SDA, PIN_SCL);
  Wire.setClock(I2C_HZ);
  if (!oled.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR)) Serial.println("SSD1306 not found (check wiring and address)");
  oled.clearDisplay();
  oled.display();

  alloc_memory();
  Serial.printf("immanence-sketches: arena %u bytes, VM object %u bytes, free heap %u, largest block %u\n",
                (unsigned) arena_bytes, (unsigned) sizeof(pde::Vm), (unsigned) ESP.getFreeHeap(),
                (unsigned) heap_caps_get_largest_free_block(MALLOC_CAP_8BIT));
  if (!vm_mem || !arena) {
    Serial.println("not enough memory for the VM");
    while (true) delay(1000);
  }
  start_piece(0);
}

void loop() {
  if (millis() - piece_start >= PIECE_MS) start_piece((piece + 1) % N_PIECES);
  if (vm->done() || vm->fault() != pde::Fault::None) {   // a still piece, or one that stopped
    delay(20);
    return;
  }
  while (millis() - last_frame < FRAME_MS) delay(1);
  last_frame = millis();
  if (!vm->next_frame()) {
    if (vm->fault() != pde::Fault::None) Serial.printf("%s: stopped: %s\n", PIECES[piece].name, vm->fault_msg());
    return;
  }
  if (checked < PIECES[piece].golden_n) {   // prove the build: compare with the reference frames
    matched += pde::crc32(vm->grey(), immanence::WIDTH * immanence::HEIGHT) == PIECES[piece].golden[checked];
    if (++checked == PIECES[piece].golden_n || vm->done())
      Serial.printf("%s: %s %d/%d frames\n", PIECES[piece].name, matched == checked ? "MATCH" : "DIFF", matched, checked);
  }
  show(vm->grey());
}
