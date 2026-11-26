// immanence-sketches: pieces on an SPI ST7789 colour TFT (ESP32-S3 with PSRAM).
//
// Cycles through the pieces embedded below. The 240x128 grey canvas is scaled to the panel with
// immanence::fit (integer scale when it fits, centred) and sent one row at a time as RGB565.
// The first frames of every piece are checked against the reference (crc32) and the result is
// printed on the serial monitor: "MATCH 30/30" means this board draws bit for bit like the site.
//
// Libraries: immanence-sketches, Adafruit ST7735 and ST7789 Library, Adafruit GFX Library.
// Arduino IDE: board "ESP32S3 Dev Module", PSRAM "OPI PSRAM" (N8R8) or "QSPI PSRAM" (N8R2).
#include <Adafruit_GFX.h>
#include <Adafruit_ST7789.h>
#include <SPI.h>
#include <immanence.h>

#include "interference.h"
#include "ridgelines.h"
#include "the_wave.h"
#include "three_bodies.h"

// ---- wiring and panel (change these for your module) -----------------------------------------
constexpr int PIN_SCLK = 12;
constexpr int PIN_MOSI = 11;
constexpr int PIN_CS = 10;
constexpr int PIN_DC = 9;
constexpr int PIN_RST = 8;    // -1 if the module has no reset pin (tie RST to 3V3)
constexpr int PIN_BL = 7;     // backlight, -1 if it is always on
// Native (portrait) size of the panel: 135x240 (1.14"), 170x320 (1.9"), 240x240 (1.3"), 240x320 (2")
constexpr int PANEL_NATIVE_W = 135;
constexpr int PANEL_NATIVE_H = 240;
constexpr int ROTATION = 1;          // 1 or 3: landscape, which suits the 240x128 canvas
constexpr uint32_t SPI_HZ = 40000000;

// ---- pieces and timing ------------------------------------------------------------------------
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
    {"the wave", the_wave, the_wave_len, the_wave_golden, the_wave_golden_n},
};
constexpr int N_PIECES = sizeof(PIECES) / sizeof(PIECES[0]);
constexpr uint32_t PIECE_MS = 60000;   // how long each piece stays on screen
constexpr uint32_t FRAME_MS = 33;      // at most ~30 frames per second

// ---- display ----------------------------------------------------------------------------------
SPIClass spi(FSPI);
Adafruit_ST7789 tft(&spi, PIN_CS, PIN_DC, PIN_RST);
uint16_t *line_buf = nullptr;   // one panel row of RGB565

void show(const uint8_t *grey) {
  const int w = tft.width(), h = tft.height();
  tft.startWrite();
  tft.setAddrWindow(0, 0, w, h);
  // fit() visits the panel row by row, left to right: fill a row, then send it in one burst
  immanence::fit(grey, w, h, [w](int x, int, uint8_t g) {
    line_buf[x] = immanence::rgb565(g);
    if (x == w - 1) tft.writePixels(line_buf, w);
  });
  tft.endWrite();
}

// ---- the VM -----------------------------------------------------------------------------------
uint8_t *arena = nullptr;
size_t arena_bytes = 0;
pde::Vm *vm = nullptr;
int piece = -1;
uint32_t piece_start = 0, last_frame = 0;
int checked = 0, matched = 0;

uint32_t now32() { return (uint32_t) millis(); }
void let_others_run() { delay(1); }

void alloc_arena() {
  // PSRAM: 1.5 MB runs every piece on the site. Internal RAM only (an N8 board): 64 KB, which
  // leaves room for the VM object (~71 KB), a smaller operand stack and the wave's 120 KB depth
  // buffer.
  static const size_t SIZES[] = {immanence::DEFAULT_ARENA, 1024 * 1024, 512 * 1024};
  static const size_t INTERNAL_SIZES[] = {64 * 1024, 48 * 1024, 32 * 1024};
  if (psramFound()) {
    for (size_t n : SIZES)
      if ((arena = (uint8_t *) ps_malloc(n))) {
        arena_bytes = n;
        return;
      }
  }
  for (size_t n : INTERNAL_SIZES)
    if ((arena = (uint8_t *) malloc(n))) {
      arena_bytes = n;
      return;
    }
}

void start_piece(int i) {
  delete vm;   // the VM object holds the canvas and drawing state (~71 KB; P3D adds 120 KB)
  vm = new pde::Vm(arena, arena_bytes);
  vm->now_ms = now32;
  vm->yield = let_others_run;
  // without PSRAM: a 16 KB operand stack instead of the default 64 KB (plenty for these pieces)
  if (!psramFound()) vm->limits.stack_slots = 2048;
  piece = i;
  piece_start = millis();
  checked = matched = 0;
  if (!vm->load(PIECES[i].prog, PIECES[i].len)) Serial.printf("%s: load failed: %s\n", PIECES[i].name, vm->fault_msg());
  else Serial.printf("%s: running\n", PIECES[i].name);
}

void setup() {
  Serial.begin(115200);
  if (PIN_BL >= 0) {
    pinMode(PIN_BL, OUTPUT);
    digitalWrite(PIN_BL, HIGH);
  }
  spi.begin(PIN_SCLK, -1, PIN_MOSI, PIN_CS);
  tft.init(PANEL_NATIVE_W, PANEL_NATIVE_H);
  tft.setSPISpeed(SPI_HZ);
  tft.setRotation(ROTATION);
  tft.fillScreen(ST77XX_BLACK);
  line_buf = (uint16_t *) malloc(sizeof(uint16_t) * (size_t) tft.width());

  alloc_arena();
  Serial.printf("immanence-sketches: arena %u bytes (%s), free heap %u, free PSRAM %u\n", (unsigned) arena_bytes,
                psramFound() ? "PSRAM" : "internal", (unsigned) ESP.getFreeHeap(), (unsigned) ESP.getFreePsram());
  if (!arena || !line_buf) {
    Serial.println("not enough memory");
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
