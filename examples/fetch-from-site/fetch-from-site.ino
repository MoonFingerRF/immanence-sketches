// immanence-sketches: fetch pieces from immanencepure.xyz and run them (ESP32-S3, PSRAM advised).
//
// Random mode (USE_RANDOM true, the default): every ROTATE_MS the sketch asks the site for a random
// piece that fits this board: `max_arena` is the arena it could allocate, `p3d=false` is added when
// the heap has no block for the 120 KB P3D depth buffer. Fixed-list mode (USE_RANDOM false): it
// cycles through PIECE_IDS, downloading each program once and keeping it in memory.
// Either way it checks the program's header and bytecode version and shows it on the ST7789 of
// examples/tft-st7789. Without a display (USE_TFT 0) it prints each piece as ASCII art instead.
//
// HTTPS is verified with the certificate bundle built into Arduino-ESP32 3.x / 4.x (the same root
// certificates ESP-IDF ships); the sketch never turns verification off.
//
// The site API (https://immanencepure.xyz/api/v1/...):
//   random.pdeb?<filters>  a random piece's program; its id is in the X-Piece-Id response header,
//                          404 when no piece matches. Filters: still, clock, library, p3d
//                          (true|false), max_arena=<bytes>, max_bytes=<bytes>, seed=<text>
//                          (the same seed gives the same piece).
//   index.json             {"pieces": [{"id", "title", "arena_bytes", "bytecode_bytes", ...}]}
//   info/<id>.json         {"title", "bytecode_bytes", "arena_bytes", "p3d", "golden", ...}
//   bytecode/<id>.pdeb     the program
//
// Libraries: immanence-sketches, ArduinoJson 7, Adafruit ST7735 and ST7789 Library, Adafruit GFX.
// Arduino IDE: board "ESP32S3 Dev Module", PSRAM "OPI PSRAM" (N8R8) or "QSPI PSRAM" (N8R2),
// Flash Size 8MB, Partition Scheme "8M with spiffs".
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <NetworkClientSecure.h>
#include <WiFi.h>
#include <esp_heap_caps.h>
#include <time.h>
#include <immanence.h>

#if __has_include("secrets.h")
#include "secrets.h"   // WIFI_SSID, WIFI_PASSWORD (git-ignored)
#else
#error "Copy secrets.example.h to secrets.h and fill in your Wi-Fi network."
#endif

// ---- which pieces ------------------------------------------------------------------------------
#define USE_RANDOM true   // false: cycle through PIECE_IDS instead
// Extra filters for random mode, e.g. "&still=false" (only animated pieces) or "&clock=false".
const char *const RANDOM_FILTERS = "";
// For fixed-list mode. `immanence fetch list` (the Python package in this repository) prints the
// ids and titles; these three are small 2D pieces that run in a 64 KB arena.
const char *const PIECE_IDS[] = {
    "binary-candles-3b8889",   // binary candles
    "heat-current-aa0047",     // heat current
    "cooling-slats-3f6132",    // cooling slats
};
constexpr int N_IDS = sizeof(PIECE_IDS) / sizeof(PIECE_IDS[0]);
constexpr uint32_t ROTATE_MS = 5 * 60 * 1000;   // next piece every 5 minutes
constexpr uint32_t RETRY_MS = 30 * 1000;        // after a failed fetch
constexpr uint32_t FRAME_MS = 33;               // at most ~30 frames per second
const char *const API = "https://immanencepure.xyz/api/v1";
// Your time zone (POSIX TZ string), for pieces that read hour() / minute() / day().
const char *const TIME_ZONE = "UTC0";           // e.g. "CET-1CEST,M3.5.0,M10.5.0/3"

// ---- memory ------------------------------------------------------------------------------------
// With PSRAM the arena takes the largest free PSRAM block minus PSRAM_MARGIN (the VM object, its
// stack, a P3D depth buffer and the downloads may also land in PSRAM), at most 1.5 MB, which runs
// every piece on the site. Without PSRAM it falls back to a small internal arena.
constexpr size_t PSRAM_MARGIN = 512 * 1024;
const size_t INTERNAL_ARENAS[] = {64 * 1024, 48 * 1024, 32 * 1024};
constexpr size_t PROG_MAX = 16 * 1024;           // largest program accepted (the site's are < 7 KB)
constexpr size_t DEPTH_BLOCK = 128 * 1024;       // P3D depth buffer (120 KB) plus a little

// A downloaded program. (Declared before the first function: the Arduino IDE puts its generated
// function prototypes there, and some of them use this type.)
struct Program {
  uint8_t *bytes = nullptr;
  size_t len = 0;
  String id;
};

// ---- display: the panel and wiring of examples/tft-st7789 -----------------------------------------
#define USE_TFT 1   // 0: no display, ASCII art on the serial monitor
#if USE_TFT
#include <Adafruit_GFX.h>
#include <Adafruit_ST7789.h>
#include <SPI.h>
constexpr int PIN_SCLK = 12;
constexpr int PIN_MOSI = 11;
constexpr int PIN_CS = 10;
constexpr int PIN_DC = 9;
constexpr int PIN_RST = 8;    // -1 if the module has no reset pin
constexpr int PIN_BL = 7;     // backlight, -1 if it is always on
constexpr int PANEL_NATIVE_W = 135;
constexpr int PANEL_NATIVE_H = 240;
constexpr int ROTATION = 1;
constexpr uint32_t SPI_HZ = 40000000;
SPIClass spi(FSPI);
Adafruit_ST7789 tft(&spi, PIN_CS, PIN_DC, PIN_RST);
uint16_t *line_buf = nullptr;

void display_begin() {
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
}

void show(const uint8_t *grey) {
  const int w = tft.width(), h = tft.height();
  tft.startWrite();
  tft.setAddrWindow(0, 0, w, h);
  immanence::fit(grey, w, h, [w](int x, int, uint8_t g) {
    line_buf[x] = immanence::rgb565(g);
    if (x == w - 1) tft.writePixels(line_buf, w);
  });
  tft.endWrite();
}
#else
void display_begin() {}
uint32_t last_ascii = 0;
void show(const uint8_t *grey) {   // one character per 2x4 block, at most every 10 s
  if (last_ascii && millis() - last_ascii < 10000) return;
  last_ascii = millis();
  static const char RAMP[] = "@%#*+=-:. ";
  char line[immanence::WIDTH / 2 + 1] = {};
  for (int by = 0; by < immanence::HEIGHT; by += 4) {
    for (int bx = 0; bx < immanence::WIDTH; bx += 2) {
      int sum = 0;
      for (int y = by; y < by + 4; y++) sum += grey[y * immanence::WIDTH + bx] + grey[y * immanence::WIDTH + bx + 1];
      line[bx / 2] = RAMP[(sum / 8) * (int) (sizeof(RAMP) - 2) / 255];
    }
    Serial.println(line);
  }
}
#endif

// ---- HTTPS -------------------------------------------------------------------------------------
// The root certificates linked into Arduino-ESP32 (ESP-IDF's bundle).
extern const uint8_t x509_crt_bundle_start[] asm("_binary_x509_crt_bundle_start");
extern const uint8_t x509_crt_bundle_end[] asm("_binary_x509_crt_bundle_end");

NetworkClientSecure tls;

// GET url into buf (size bytes at most). Returns the length, -1 on an error, -404 for "not
// found". When piece_id is given, it receives the X-Piece-Id response header.
int https_get(const String &url, uint8_t *buf, size_t size, String *piece_id = nullptr) {
  HTTPClient http;
  http.setUserAgent("immanence-sketches/1.0 (fetch-from-site example)");
  http.setTimeout(15000);
  if (!http.begin(tls, url)) return -1;
  static const char *WANT_HEADERS[] = {"X-Piece-Id"};   // collectHeaders takes const char **
  if (piece_id) http.collectHeaders(WANT_HEADERS, 1);
  int code = http.GET();
  if (code != HTTP_CODE_OK) {
    Serial.printf("GET %s: %s\n", url.c_str(), code < 0 ? http.errorToString(code).c_str() : String(code).c_str());
    http.end();
    return code == HTTP_CODE_NOT_FOUND ? -404 : -1;
  }
  if (piece_id) *piece_id = http.header("X-Piece-Id");
  int expect = http.getSize();   // -1 when the server does not say
  if (expect > (int) size) {
    Serial.printf("GET %s: %d bytes is too big\n", url.c_str(), expect);
    http.end();
    return -1;
  }
  NetworkClient *s = http.getStreamPtr();
  size_t got = 0;
  uint32_t t0 = millis();
  while (http.connected() && got < size && (expect < 0 || got < (size_t) expect) && millis() - t0 < 20000) {
    size_t n = s->available();
    if (!n) {
      delay(2);
      continue;
    }
    got += s->readBytes(buf + got, min(n, size - got));
  }
  http.end();
  if (expect >= 0 && got != (size_t) expect) {
    Serial.printf("GET %s: short read (%u of %d)\n", url.c_str(), (unsigned) got, expect);
    return -1;
  }
  return (int) got;
}

// A program starts "PDEB" and its byte 4 is the bytecode version this library must run.
bool program_ok(const String &id, const uint8_t *p, int n) {
  if (n < 16 || memcmp(p, "PDEB", 4) != 0) {
    Serial.printf("%s: not a program\n", id.c_str());
    return false;
  }
  if (p[4] != pde::BC_VERSION) {
    Serial.printf("%s: bytecode v%d, this library runs v%d: update the library\n", id.c_str(), p[4], pde::BC_VERSION);
    return false;
  }
  return true;
}

// ---- pieces ------------------------------------------------------------------------------------
uint8_t *arena = nullptr;
size_t arena_bytes = 0;
bool has_psram = false;

void *big_alloc(size_t n) { return has_psram ? ps_malloc(n) : malloc(n); }

void drop(Program &p) {
  free(p.bytes);
  p = Program();
}

#if USE_RANDOM
// Random mode: one program at a time; the site picks one that fits this board.
Program incoming;

bool fetch_next(Program *&out) {
  if (!incoming.bytes && !(incoming.bytes = (uint8_t *) big_alloc(PROG_MAX))) return false;
  String url = String(API) + "/random.pdeb?max_arena=" + arena_bytes + "&max_bytes=" + PROG_MAX;
  // P3D pieces allocate a 120 KB depth buffer from the heap, outside the arena
  const bool room_for_3d = heap_caps_get_largest_free_block(MALLOC_CAP_8BIT) >= DEPTH_BLOCK;
  if (!room_for_3d) url += "&p3d=false";
  url += RANDOM_FILTERS;
  String id;
  int n = https_get(url, incoming.bytes, PROG_MAX, &id);
  if (n == -404) Serial.println("no piece on the site matches the filters");
  if (n < 0 || !program_ok(id, incoming.bytes, n)) return false;
  incoming.len = n;
  incoming.id = id;
  Serial.printf("%s: %d bytes (random, arena up to %u%s)\n", id.c_str(), n, (unsigned) arena_bytes,
                room_for_3d ? "" : ", no 3D");
  out = &incoming;
  return true;
}
#else
// Fixed-list mode: each program is downloaded once and kept.
Program programs[N_IDS];
int next_id = 0;
uint8_t *scratch = nullptr;   // info JSON
constexpr size_t SCRATCH = 16 * 1024;

bool fetch_id(int i) {
  Program &p = programs[i];
  if (p.bytes) return true;
  const String id = PIECE_IDS[i];
  if (!scratch && !(scratch = (uint8_t *) big_alloc(SCRATCH))) return false;
  int n = https_get(String(API) + "/info/" + id + ".json", scratch, SCRATCH - 1);
  if (n < 0) return false;
  JsonDocument filter, info;
  filter["bytecode_bytes"] = true;
  filter["arena_bytes"] = true;
  filter["title"] = true;
  if (deserializeJson(info, (const char *) scratch, (size_t) n, DeserializationOption::Filter(filter))) {
    Serial.printf("%s: info is not JSON\n", id.c_str());
    return false;
  }
  const size_t want = info["bytecode_bytes"] | 0;
  const size_t need_arena = info["arena_bytes"] | 0;
  const char *title = info["title"] | "";
  if (need_arena > arena_bytes) {
    Serial.printf("%s: needs a %u byte arena, this board has %u\n", id.c_str(), (unsigned) need_arena, (unsigned) arena_bytes);
    return false;
  }
  if (want == 0 || want > PROG_MAX) return false;
  if (!(p.bytes = (uint8_t *) big_alloc(want))) return false;
  n = https_get(String(API) + "/bytecode/" + id + ".pdeb", p.bytes, want);
  if (n != (int) want || !program_ok(id, p.bytes, n)) {
    drop(p);
    return false;
  }
  p.len = want;
  p.id = id;
  Serial.printf("%s: \"%s\", %u bytes, arena %u\n", id.c_str(), title, (unsigned) want, (unsigned) need_arena);
  return true;
}

bool fetch_next(Program *&out) {   // the next id that can be fetched, in turn
  for (int k = 0; k < N_IDS; k++) {
    int i = (next_id + k) % N_IDS;
    if (fetch_id(i)) {
      next_id = (i + 1) % N_IDS;
      out = &programs[i];
      return true;
    }
  }
  return false;
}
#endif

pde::Vm *vm = nullptr;
String current;
uint32_t shown_at = 0, last_frame = 0;
bool showing = false;

uint32_t now32() { return (uint32_t) millis(); }
void let_others_run() { delay(1); }

int32_t utc_offset_s() {   // the local time zone's offset now (DST included)
  time_t now = time(nullptr);
  struct tm g;
  gmtime_r(&now, &g);
  g.tm_isdst = -1;
  return (int32_t) (now - mktime(&g));
}

// Fetch the next piece and start it. On failure the current piece stays on screen.
bool start_next() {
  Program *p = nullptr;
  if (!fetch_next(p)) return false;
  delete vm;   // the old piece stops here
  vm = new pde::Vm(arena, arena_bytes);
  vm->now_ms = now32;
  vm->yield = let_others_run;
  if (!has_psram) vm->limits.stack_slots = 2048;   // 16 KB instead of 64 KB of internal RAM
  vm->api.now_s = time(nullptr);
  vm->api.tz_s = utc_offset_s();
  // The VM keeps its own copy of the program, so the download buffer can be reused.
  if (!vm->load(p->bytes, p->len)) {
    Serial.printf("%s: %s\n", p->id.c_str(), vm->fault_msg());
    delete vm;
    vm = nullptr;
    showing = false;
#if !USE_RANDOM
    drop(*p);   // fetch it again next time
#endif
    return false;
  }
  current = p->id;
  showing = true;
  return true;
}

void alloc_arena() {
  has_psram = psramFound();
  if (has_psram) {
    size_t largest = heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM);
    size_t n = largest > PSRAM_MARGIN ? largest - PSRAM_MARGIN : 0;
    if (n > immanence::DEFAULT_ARENA) n = immanence::DEFAULT_ARENA;
    n &= ~(size_t) 0xFFFF;   // whole 64 KB steps, like the site's arena sizes
    if (n >= 64 * 1024 && (arena = (uint8_t *) ps_malloc(n))) {
      arena_bytes = n;
      return;
    }
  }
  for (size_t n : INTERNAL_ARENAS)
    if ((arena = (uint8_t *) malloc(n))) {
      arena_bytes = n;
      return;
    }
}

void setup() {
  Serial.begin(115200);
  display_begin();
  alloc_arena();
  if (!arena) {
    Serial.println("not enough memory for an arena");
    while (true) delay(1000);
  }
  Serial.printf("arena %u bytes in %s\n", (unsigned) arena_bytes, has_psram ? "PSRAM" : "internal RAM");
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Wi-Fi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print('.');
  }
  Serial.println(" connected");
  configTzTime(TIME_ZONE, "pool.ntp.org", "time.nist.gov");
  tls.setCACertBundle(x509_crt_bundle_start, x509_crt_bundle_end - x509_crt_bundle_start);
  shown_at = millis() - ROTATE_MS;   // fetch the first piece right away
}

void loop() {
  if (millis() - shown_at >= ROTATE_MS) {
    // a new piece every ROTATE_MS; after a failed fetch, try again in RETRY_MS
    shown_at = start_next() ? millis() : millis() - ROTATE_MS + RETRY_MS;
  }
  if (!showing || vm->done() || vm->fault() != pde::Fault::None) {   // still, stopped, or none yet
    delay(20);
    return;
  }
  while (millis() - last_frame < FRAME_MS) delay(1);
  last_frame = millis();
  vm->api.now_s = time(nullptr);
  if (vm->next_frame()) show(vm->grey());
  else if (vm->fault() != pde::Fault::None) Serial.printf("%s: stopped: %s\n", current.c_str(), vm->fault_msg());
}
