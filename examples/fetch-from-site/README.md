# fetch-from-site: pieces from immanencepure.xyz

Downloads pieces from the site's API over HTTPS and runs them on the ST7789 panel of
[`tft-st7789`](../tft-st7789/) (same pins and constants), a new piece every five minutes. Set
`USE_TFT` to 0 to print each piece as ASCII art on the serial monitor instead.

## Random mode (the default)

Every five minutes the sketch asks for a random piece that fits this board:

```
GET https://immanencepure.xyz/api/v1/random.pdeb?max_arena=<arena>&max_bytes=16384[&p3d=false]
```

- `max_arena` is the arena the sketch could allocate at boot: the largest free PSRAM block minus
  a 512 KB margin, at most 1.5 MB (`immanence::DEFAULT_ARENA`, which runs every piece), in whole
  64 KB steps. Without PSRAM it is a 64 KB (or smaller) internal arena.
- `p3d=false` is added when the heap has no 128 KB block left for the 120 KB depth buffer that
  P3D pieces allocate outside the arena.
- `max_bytes` matches the 16 KB download buffer (the site's largest program is about 6 KB).

The response is the program itself; the sketch reads the piece id from the `X-Piece-Id` response
header (`HTTPClient::collectHeaders`), checks the `PDEB` magic and byte 4 (the bytecode version,
which must equal `pde::BC_VERSION`) and starts it. A 404 means no piece matches the filters.

More filters go in `RANDOM_FILTERS` at the top of the sketch, for example `"&still=false"` (only
animated pieces), `"&clock=false"` (none that read the time) or `"&library=false"`.
`seed=<text>` always returns the same piece for the same text.

## Fixed-list mode

Set `USE_RANDOM` to `false` to cycle through `PIECE_IDS` instead. For each id the sketch reads
`info/<id>.json` (`bytecode_bytes`, `arena_bytes`, `title`), skips pieces whose arena is bigger
than its own, downloads `bytecode/<id>.pdeb`, checks it the same way and keeps it, so each piece is
fetched once per boot. The three ids in the sketch are small 2D pieces that run in a 64 KB arena
(binary candles, heat current, cooling slats). To pick others:

```sh
PYTHONPATH=python python3 -m immanence fetch list
```

or read `https://immanencepure.xyz/api/v1/index.json`, whose rows carry `id`, `title`,
`arena_bytes`, `bytecode_bytes`, `still`, `clock`, `library` and `p3d`.

## Both modes

Pieces that read `hour()`, `minute()` or `day()` get the clock from NTP; set `TIME_ZONE` (a POSIX
TZ string) at the top of the sketch. A failed fetch keeps the current piece on screen and tries
again 30 s later. A piece that stops with a fault (for example a memory fault) leaves its last
frame on screen until the next one.

HTTPS is verified against the root certificate bundle that Arduino-ESP32 links in (ESP-IDF's
bundle, `NetworkClientSecure::setCACertBundle`); the sketch never calls `setInsecure()`.

## Board and wiring

ESP32-S3 with PSRAM (e.g. ESP32-S3-DevKitC-1 N8R8) and the ST7789 wiring of
[`tft-st7789`](../tft-st7789/README.md#board-and-wiring). Wi-Fi with internet access. Without PSRAM
the sketch falls back to a small internal arena and a 2048-slot operand stack and asks only for
pieces that fit (not tried on hardware).

## Build

```sh
cd examples/fetch-from-site
cp secrets.example.h secrets.h       # then put your Wi-Fi name and password in secrets.h
pio run -t upload && pio device monitor
```

`secrets.h` is in `.gitignore`; never commit it.

Arduino IDE 2: install this library, *ArduinoJson* (7.x), *Adafruit ST7735 and ST7789 Library* and
*Adafruit GFX Library*; board "ESP32S3 Dev Module" with PSRAM enabled, Flash Size 8MB and
Partition Scheme "8M with spiffs" (the sketch is about 1.4 MB: more than the 1.25 MB app partition
of the 4 MB default). Arduino-ESP32 3.x or 4.x is needed (for `NetworkClientSecure` and its
built-in certificate bundle).

## Memory

| what | size | where |
|---|---|---|
| arena | largest PSRAM block minus 512 KB, at most 1.5 MB | PSRAM |
| download buffer | 16 KB (random mode, reused) or `bytecode_bytes` per piece (fixed list, kept) | PSRAM |
| info JSON buffer | 16 KB (fixed-list mode only) | PSRAM |
| VM object, operand stack, P3D depth buffer | about 71 KB, 72 KB, 120 KB | heap |
| TLS session | about 40 KB while a download runs | internal RAM |

The VM copies the program when it loads it, so the random-mode buffer is reused for the next one.

PlatformIO build (pioarduino 55.03.311, Arduino-ESP32 3.3, `esp32-s3-devkitc-1`, 8 MB flash,
random mode): flash 1,374,195 bytes (41.1 %), static RAM 54,484 bytes (16.6 %).
