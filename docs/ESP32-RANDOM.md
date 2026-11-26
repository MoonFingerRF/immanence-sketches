# Random sketches from immanencepure.xyz on an ESP32

This guide takes you from an ESP32 on your desk to a board that shows a new piece from
[immanencepure.xyz](https://immanencepure.xyz) every few minutes, chosen at random from more than
2,000, always one that fits the board's memory. It uses one HTTPS request per piece.

You need:
* an ESP32 board. Any will do; an **ESP32-S3 with PSRAM** (e.g. a DevKitC-1 N8R8) runs every piece,
  a classic ESP32 without PSRAM runs the 2D pieces that fit 64 KB (more than half of them);
* Wi-Fi;
* the Arduino IDE (with the Arduino-ESP32 core 3.x) or PlatformIO;
* a display is optional: the program below prints each frame as text on the serial monitor.

## 1. Install the library

* **Arduino IDE**: download this repository as a ZIP (*Code → Download ZIP*), then
  *Sketch → Include Library → Add .ZIP Library...*.
* **PlatformIO**: in `platformio.ini`, `lib_deps = https://github.com/MoonFingerRF/immanence-sketches.git`.

## 2. The request: one random piece that fits

```
GET https://immanencepure.xyz/api/v1/random.pdeb?max_arena=<bytes>&max_bytes=<bytes>[&p3d=false]
```

The answer is the piece's compiled program (`application/octet-stream`, with a
`Content-Length`). The piece's id is in the `X-Piece-Id` response header; its title, description
and more are at `/api/v1/info/<id>.json`. The filters keep the choice to pieces your board can run:

| Filter | Why |
|---|---|
| `max_arena=<bytes>` | the arena you give the VM (`1572864` = 1.5 MB in PSRAM runs everything; `65536` without PSRAM) |
| `max_bytes=<bytes>` | the size of your download buffer (pieces are 0.3–3 KB; 16384 is plenty) |
| `p3d=false` | leave out 3D pieces when the board has no 120 KB block for the depth buffer (no PSRAM) |
| `still=false`, `library=...`, `clock=...` | optional: only animated pieces, and so on |
| `seed=<text>` | optional: the same seed gives the same piece (e.g. today's date for a piece of the day) |

`404` means nothing matches the filters; `400` means a filter is malformed. Every field is in
[API.md](API.md).

## 3. Check it, load it, run it

A program starts with the bytes `PDEB`, and byte 4 is its bytecode version, which must equal
`pde::BC_VERSION` (a newer site could serve a newer format; update the library then). After that:

```cpp
vm = new pde::Vm(arena, arena_bytes);   // a fresh VM per piece
vm->load(program, n);                   // false: vm->fault_msg() says why
while (vm->next_frame()) show(vm->grey());   // 240 x 128 grey bytes per frame, 0 = black
```

`vm->done()` is true once a still piece has drawn its one frame; `vm->fault()` tells you whether a
piece stopped because it broke a limit (it never crashes the board). Memory: [MEMORY.md](MEMORY.md).

## 4. The whole program

This is [`examples/random-minimal/random-minimal.ino`](../examples/random-minimal/random-minimal.ino),
built with no warnings for the ESP32-S3 (with PSRAM) and the classic ESP32, with both PlatformIO
and the Arduino CLI. Put your Wi-Fi name and password in the two lines at the top (never commit
them), upload, and open the serial monitor at 115200 baud.

```cpp
// Random sketches from immanencepure.xyz on an ESP32: the smallest complete program.
//
// Every few minutes it asks the site for a random piece that fits this board's memory, checks it,
// and runs it on the VM. No display needed: it prints the canvas as text on the serial monitor
// (115200 baud). To draw on a real screen, replace show() (see docs/DISPLAYS.md and
// examples/fetch-from-site). Set your Wi-Fi below; do not commit it.
#include <HTTPClient.h>
#include <NetworkClientSecure.h>
#include <WiFi.h>
#include <immanence.h>

const char *WIFI_SSID = "your-network";
const char *WIFI_PASSWORD = "your-password";
const char *API = "https://immanencepure.xyz/api/v1";
const uint32_t NEXT_PIECE_MS = 5 * 60 * 1000;   // a new random piece every 5 minutes

// The root certificates built into Arduino-ESP32, so HTTPS is verified.
extern const uint8_t x509_crt_bundle_start[] asm("_binary_x509_crt_bundle_start");
extern const uint8_t x509_crt_bundle_end[] asm("_binary_x509_crt_bundle_end");

NetworkClientSecure tls;
uint8_t *arena = nullptr;           // the VM's heap (PSRAM when the board has it)
size_t arena_bytes = 0;
static uint8_t program[16 * 1024];  // the downloaded bytecode (pieces are usually 0.3-3 KB)
pde::Vm *vm = nullptr;
uint32_t started = 0;

// 1. Ask the site for a random piece that fits; returns its length (or -1), and its id.
int fetch_random(String &id) {
  String url = String(API) + "/random.pdeb?max_arena=" + arena_bytes + "&max_bytes=" + sizeof(program);
  if (!psramFound()) url += "&p3d=false";   // 3D needs a 120 KB depth buffer this board lacks
  HTTPClient http;
  if (!http.begin(tls, url)) return -1;
  const char *want[] = {"X-Piece-Id"};
  http.collectHeaders(want, 1);
  int code = http.GET();
  int len = http.getSize();
  if (code != HTTP_CODE_OK || len <= 0 || len > (int) sizeof(program)) {
    Serial.printf("GET random: HTTP %d, %d bytes\n", code, len);
    http.end();
    return -1;
  }
  id = http.header("X-Piece-Id");
  NetworkClient *s = http.getStreamPtr();
  int got = 0;
  for (uint32_t t0 = millis(); got < len && millis() - t0 < 20000;) {
    if (s->available()) got += s->readBytes(program + got, len - got);
    else delay(2);
  }
  http.end();
  return got == len ? len : -1;
}

// 2. Check it (a program starts "PDEB", byte 4 is its bytecode version) and load it.
bool start_random() {
  String id;
  int n = fetch_random(id);
  if (n < 16 || memcmp(program, "PDEB", 4) != 0 || program[4] != pde::BC_VERSION) return false;
  delete vm;                                       // the previous piece stops here
  vm = new pde::Vm(arena, arena_bytes);
  if (!psramFound()) vm->limits.stack_slots = 2048;   // a smaller stack in internal RAM
  vm->api.now_s = time(nullptr);                   // for pieces that read the clock
  if (!vm->load(program, n)) {
    Serial.printf("%s: %s\n", id.c_str(), vm->fault_msg());
    return false;
  }
  Serial.printf("now running %s: %s/info/%s.json\n", id.c_str(), API, id.c_str());
  return true;
}

// 3. Show a frame: here, one character per 2x4 pixels, at most every 10 seconds.
void show(const uint8_t *grey) {
  static uint32_t last = 0;
  if (last && millis() - last < 10000) return;
  last = millis();
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

void setup() {
  Serial.begin(115200);
  // The arena: 1.5 MB of PSRAM runs every piece; without PSRAM, 64 KB runs more than half of them.
  arena_bytes = psramFound() ? immanence::DEFAULT_ARENA : 64 * 1024;
  arena = (uint8_t *) (psramFound() ? ps_malloc(arena_bytes) : malloc(arena_bytes));
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) delay(500);
  configTime(0, 0, "pool.ntp.org");                // UTC; the clock pieces read
  tls.setCACertBundle(x509_crt_bundle_start, x509_crt_bundle_end - x509_crt_bundle_start);
  started = millis() - NEXT_PIECE_MS;              // fetch the first piece now
}

void loop() {
  if (millis() - started >= NEXT_PIECE_MS) {
    started = start_random() ? millis() : millis() - NEXT_PIECE_MS + 30000;   // retry in 30 s
  }
  if (vm && vm->next_frame()) show(vm->grey());    // 4. one frame per call
  else delay(50);                                   // still piece finished, or a fault
}
```

Build and upload:

* **Arduino IDE**: open `examples/random-minimal`, choose your board (for an S3: *PSRAM: OPI PSRAM*;
  for a classic ESP32: *Partition Scheme: Huge APP*, since Wi-Fi + HTTPS + the VM needs ~1.35 MB),
  upload, open the serial monitor.
* **PlatformIO**: `cd examples/random-minimal && pio run -e esp32-s3-devkitc-1-n8r8 -t upload && pio device monitor`
  (or `-e esp32dev`).

## 5. On a real display

Replace `show()` with your display's drawing code; [DISPLAYS.md](DISPLAYS.md) has the conversions
(grey to RGB565 for colour TFTs, the 1-bit dither for OLEDs and e-paper, scaling to any size).
[`examples/fetch-from-site`](../examples/fetch-from-site) is the full version on an ST7789 TFT: it
sizes the arena from the free PSRAM, sends `p3d=false` only when the heap really has no 120 KB block,
sets the time zone for pieces that read the clock, keeps the old piece on screen if a download
fails, and has a fixed-list mode for chosen pieces.

Status: these programs are built and their requests tested against the live API; they have not yet
been run on real boards.
