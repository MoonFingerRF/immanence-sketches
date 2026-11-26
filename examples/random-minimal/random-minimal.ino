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
