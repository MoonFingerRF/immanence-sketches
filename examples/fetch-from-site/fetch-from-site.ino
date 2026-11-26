// immanence-sketches: fetch pieces from immanencepure.xyz and run them (ESP32-S3 with PSRAM).
//
// For each id in PIECE_IDS the sketch reads the piece's info over HTTPS, checks that this library
// can run it (bytecode version, size, arena), downloads its bytecode once (kept in PSRAM), and
// shows it on the ST7789 of examples/tft-st7789, moving to the next id every ROTATE_MS. Without a
// display (USE_TFT 0) it prints each piece as ASCII art on the serial monitor instead.
//
// HTTPS is verified with the certificate bundle built into Arduino-ESP32 3.x (the same root
// certificates ESP-IDF ships); the sketch never turns verification off.
//
// The site API (static JSON, one file per piece):
//   https://immanencepure.xyz/api/v1/index.json         {"pieces": [{"id": ..., "title": ...}, ...]}
//   https://immanencepure.xyz/api/v1/info/<id>.json     {"bytecode_bytes": n, "arena_bytes": n,
//                                                         "bytecode": {"version": 1}, ...}
//   https://immanencepure.xyz/api/v1/bytecode/<id>.pdeb  the program
//
// Libraries: immanence-sketches, ArduinoJson 7, Adafruit ST7735 and ST7789 Library, Adafruit GFX.
// Arduino IDE: board "ESP32S3 Dev Module", PSRAM "OPI PSRAM" (N8R8) or "QSPI PSRAM" (N8R2).
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <NetworkClientSecure.h>
#include <WiFi.h>
#include <time.h>
#include <immanence.h>

#if __has_include("secrets.h")
#include "secrets.h"   // WIFI_SSID, WIFI_PASSWORD (git-ignored)
#else
#error "Copy secrets.example.h to secrets.h and fill in your Wi-Fi network."
#endif

// ---- which pieces ------------------------------------------------------------------------------
// `immanence fetch list` (the Python package in this repository) prints real ids and titles.
const char *const PIECE_IDS[] = {
    "example-id-1",
    "example-id-2",
    "example-id-3",
};
constexpr int N_IDS = sizeof(PIECE_IDS) / sizeof(PIECE_IDS[0]);
constexpr uint32_t ROTATE_MS = 5 * 60 * 1000;   // next piece every 5 minutes
constexpr uint32_t RETRY_MS = 30 * 1000;        // after a failed fetch
constexpr uint32_t FRAME_MS = 33;               // at most ~30 frames per second
const char *const API = "https://immanencepure.xyz/api/v1";
// Your time zone (POSIX TZ string), for pieces that read hour() / minute() / day().
const char *const TIME_ZONE = "UTC0";           // e.g. "CET-1CEST,M3.5.0,M10.5.0/3"

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
// The root certificates linked into Arduino-ESP32 3.x (ESP-IDF's bundle).
extern const uint8_t x509_crt_bundle_start[] asm("_binary_x509_crt_bundle_start");
extern const uint8_t x509_crt_bundle_end[] asm("_binary_x509_crt_bundle_end");

NetworkClientSecure tls;

// GET url into a buffer from PSRAM (size bytes at most). Returns the length, or -1.
int https_get(const String &url, uint8_t *buf, size_t size) {
  HTTPClient http;
  http.setUserAgent("immanence-sketches/1.0 (fetch-from-site example)");
  http.setTimeout(15000);
  if (!http.begin(tls, url)) return -1;
  int code = http.GET();
  if (code != HTTP_CODE_OK) {
    Serial.printf("GET %s: %s\n", url.c_str(), code < 0 ? http.errorToString(code).c_str() : String(code).c_str());
    http.end();
    return -1;
  }
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

// ---- pieces ------------------------------------------------------------------------------------
struct Program {
  uint8_t *bytes = nullptr;   // PSRAM, fetched once
  size_t len = 0;
};
Program programs[N_IDS];
uint8_t *arena = nullptr;
size_t arena_bytes = 0;
uint8_t *scratch = nullptr;   // info JSON
constexpr size_t SCRATCH = 16 * 1024;

bool fetch_piece(int i) {
  if (programs[i].bytes) return true;
  const String id = PIECE_IDS[i];
  int n = https_get(String(API) + "/info/" + id + ".json", scratch, SCRATCH - 1);
  if (n < 0) return false;
  JsonDocument filter, info;
  filter["bytecode_bytes"] = true;
  filter["arena_bytes"] = true;
  filter["title"] = true;
  filter["bytecode"]["version"] = true;
  if (deserializeJson(info, (const char *) scratch, (size_t) n, DeserializationOption::Filter(filter))) {
    Serial.printf("%s: info is not JSON\n", id.c_str());
    return false;
  }
  const size_t want = info["bytecode_bytes"] | 0;
  const size_t need_arena = info["arena_bytes"] | 0;
  const int version = info["bytecode"]["version"] | -1;
  const char *title = info["title"] | "";
  if (version != pde::BC_VERSION) {
    Serial.printf("%s: bytecode v%d, this library runs v%d: update the library\n", id.c_str(), version, pde::BC_VERSION);
    return false;
  }
  if (need_arena > arena_bytes) {
    Serial.printf("%s: needs a %u byte arena, this board has %u\n", id.c_str(), (unsigned) need_arena, (unsigned) arena_bytes);
    return false;
  }
  if (want == 0 || want > 1024 * 1024) return false;
  auto *prog = (uint8_t *) ps_malloc(want);
  if (!prog) return false;
  n = https_get(String(API) + "/bytecode/" + id + ".pdeb", prog, want);
  // a program starts "PDEB" and its byte 4 is the bytecode version
  if (n != (int) want || memcmp(prog, "PDEB", 4) != 0 || prog[4] != pde::BC_VERSION) {
    Serial.printf("%s: bad bytecode download\n", id.c_str());
    free(prog);
    return false;
  }
  programs[i].bytes = prog;
  programs[i].len = want;
  Serial.printf("%s: \"%s\", %u bytes, arena %u\n", id.c_str(), title, (unsigned) want, (unsigned) need_arena);
  return true;
}

pde::Vm *vm = nullptr;
int current = -1;
uint32_t shown_at = 0, last_try = 0, last_frame = 0;

uint32_t now32() { return (uint32_t) millis(); }
void let_others_run() { delay(1); }

int32_t utc_offset_s() {   // the local time zone's offset now (DST included)
  time_t now = time(nullptr);
  struct tm g;
  gmtime_r(&now, &g);
  g.tm_isdst = -1;
  return (int32_t) (now - mktime(&g));
}

bool start(int i) {
  if (!fetch_piece(i)) return false;
  delete vm;
  vm = new pde::Vm(arena, arena_bytes);
  vm->now_ms = now32;
  vm->yield = let_others_run;
  vm->api.now_s = time(nullptr);
  vm->api.tz_s = utc_offset_s();
  if (!vm->load(programs[i].bytes, programs[i].len)) {
    Serial.printf("%s: %s\n", PIECE_IDS[i], vm->fault_msg());
    delete vm;   // the caller tries the other ids (and the current one again)
    vm = nullptr;
    free(programs[i].bytes);
    programs[i] = Program();
    return false;
  }
  current = i;
  shown_at = millis();
  return true;
}

void setup() {
  Serial.begin(115200);
  display_begin();
  static const size_t SIZES[] = {immanence::DEFAULT_ARENA, 1024 * 1024, 512 * 1024};
  for (size_t n : SIZES)
    if ((arena = (uint8_t *) ps_malloc(n))) {
      arena_bytes = n;
      break;
    }
  scratch = (uint8_t *) ps_malloc(SCRATCH);
  if (!arena || !scratch) {
    Serial.println("this example needs PSRAM");
    while (true) delay(1000);
  }
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
}

void loop() {
  const bool due = current < 0 ? millis() - last_try >= RETRY_MS || last_try == 0 : millis() - shown_at >= ROTATE_MS;
  if (due) {   // try the next ids in turn; keep the current piece if none can be fetched
    last_try = millis();
    for (int k = 1; k <= N_IDS; k++) {
      int i = ((current < 0 ? -1 : current) + k) % N_IDS;
      if (start(i)) break;
    }
    if (current >= 0 && millis() - shown_at > ROTATE_MS) shown_at = millis() - ROTATE_MS + RETRY_MS;
  }
  if (!vm || current < 0 || vm->done() || vm->fault() != pde::Fault::None) {
    delay(20);
    return;
  }
  while (millis() - last_frame < FRAME_MS) delay(1);
  last_frame = millis();
  vm->api.now_s = time(nullptr);
  if (vm->next_frame()) show(vm->grey());
  else if (vm->fault() != pde::Fault::None) Serial.printf("%s: stopped: %s\n", PIECE_IDS[current], vm->fault_msg());
}
