# fetch-from-site: pieces from immanencepure.xyz

Downloads pieces from the site's static API over HTTPS and runs them on the ST7789 panel of
[`tft-st7789`](../tft-st7789/) (same pins and constants), a new piece every five minutes. Set
`USE_TFT` to 0 to print each piece as ASCII art on the serial monitor instead.

For every id in `PIECE_IDS` it:

1. reads `https://immanencepure.xyz/api/v1/info/<id>.json` and checks `bytecode.version` against
   `pde::BC_VERSION`, `bytecode_bytes` (the download size) and `arena_bytes` against the arena
   it has;
2. downloads `https://immanencepure.xyz/api/v1/bytecode/<id>.pdeb` into PSRAM, checks the size,
   the `PDEB` magic and byte 4 (the bytecode version), and keeps it, so each piece is fetched once
   per boot;
3. runs it, with the clock set from NTP for pieces that read `hour()`, `minute()` or `day()`.

A failed fetch keeps the current piece on screen and tries again 30 s later.

HTTPS is verified against the root certificate bundle that Arduino-ESP32 3.x links in (ESP-IDF's
bundle, `NetworkClientSecure::setCACertBundle`); the sketch never calls `setInsecure()`.

## Choosing pieces

The ids at the top of `fetch-from-site.ino` are placeholders (`example-id-1`, ...). List real
ones with the Python package in this repository:

```sh
PYTHONPATH=python python3 -m immanence fetch list
```

or read `https://immanencepure.xyz/api/v1/index.json` (`pieces: [{id, title, ...}]`).

## Board and wiring

ESP32-S3 with PSRAM (e.g. ESP32-S3-DevKitC-1 N8R8) and the ST7789 wiring of
[`tft-st7789`](../tft-st7789/README.md#board-and-wiring). Wi-Fi with internet access.

## Build

```sh
cd examples/fetch-from-site
cp secrets.example.h secrets.h       # then put your Wi-Fi name and password in secrets.h
pio run -t upload && pio device monitor
```

`secrets.h` is in `.gitignore`; never commit it. Set `TIME_ZONE` (a POSIX TZ string) at the top
of the sketch if your pieces read the clock.

Arduino IDE 2: install this library, *ArduinoJson* (7.x), *Adafruit ST7735 and ST7789 Library* and
*Adafruit GFX Library*; board "ESP32S3 Dev Module" with PSRAM enabled, Flash Size 8MB and
Partition Scheme "8M with spiffs" (the sketch is about 1.4 MB: more than the 1.25 MB app partition
of the 4 MB default). Arduino-ESP32 3.x or 4.x is needed (for `NetworkClientSecure` and its
built-in certificate bundle).

## Memory

| what | size | where |
|---|---|---|
| arena | 1.5 MB (`immanence::DEFAULT_ARENA`): enough for every piece on the site | PSRAM |
| programs | `bytecode_bytes` each (most are under 2 KB), kept for the session | PSRAM |
| info JSON buffer | 16 KB | PSRAM |
| VM object, operand stack, P3D depth buffer | about 71 KB, 72 KB, 120 KB | heap |
| TLS session | about 40 KB while a download runs | internal RAM |

PlatformIO build (pioarduino 55.03.311, Arduino-ESP32 3.3, `esp32-s3-devkitc-1`, 8 MB flash):
flash 1,381,071 bytes (41.3 %), static RAM 54,484 bytes (16.6 %).
