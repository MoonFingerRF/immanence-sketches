# tft-st7789: pieces on a colour TFT

Cycles through four embedded pieces (interference, three bodies, ridgelines, the wave) on an SPI
ST7789 panel, one minute each. The 240x128 grey canvas is fitted to the panel with
`immanence::fit` (integer scale when it fits, centred, black borders) and converted with
`immanence::rgb565`; the picture goes out one row at a time through a line buffer
(`writePixels`), not pixel by pixel.

The first frames of each piece are checked against the reference crc32s embedded with it, and the
serial monitor says `interference: MATCH 30/30 frames` when this board draws exactly like the
site.

## Board and wiring

ESP32-S3 with PSRAM, e.g. an ESP32-S3-DevKitC-1 N8R8 (8 MB flash, 8 MB octal PSRAM). Any ST7789
module (1.14" 135x240, 1.3" 240x240, 1.9" 170x320, 2" 240x320); set its native size and the pins at
the top of `tft-st7789.ino`.

| ST7789 | ESP32-S3 GPIO | constant |
|---|---|---|
| SCL / SCK | 12 | `PIN_SCLK` |
| SDA / MOSI | 11 | `PIN_MOSI` |
| CS | 10 | `PIN_CS` |
| DC | 9 | `PIN_DC` |
| RES / RST | 8 | `PIN_RST` (-1 if absent) |
| BLK / BL | 7 | `PIN_BL` (-1 if always on) |
| VCC, GND | 3V3, GND | |

On a 135x240 panel in landscape the canvas shows at 1:1 with 3- and 4-pixel bars above and below; on a
320-wide panel it is still 1:1 (the integer scale), centred.

## Build

PlatformIO (`platformio.ini` builds this same `.ino`; it pulls the library from `../..`):

```sh
cd examples/tft-st7789
pio run -t upload && pio device monitor
```

Arduino IDE 2: install this library (Sketch > Include Library > Add .ZIP Library, or clone it into
`~/Arduino/libraries/immanence-sketches`), then *Adafruit ST7735 and ST7789 Library* and *Adafruit
GFX Library* from the Library Manager. Open File > Examples > immanence-sketches > tft-st7789.
Board "ESP32S3 Dev Module", PSRAM "OPI PSRAM" (N8R8) or "QSPI PSRAM" (N8R2), USB CDC On Boot
"Enabled" for the serial monitor over the USB port.

The Arduino IDE cannot pass compiler flags to a library; the library's sources enforce the one
that matters for exact drawing (no fused multiply-add) themselves, in `src/pde_fp.h`.

## Memory

| what | size | where |
|---|---|---|
| arena | 1.5 MB (`immanence::DEFAULT_ARENA`), smaller if that fails | PSRAM (`ps_malloc`) |
| VM object (canvas and drawing state) | about 71 KB | heap (PSRAM: Arduino-ESP32 puts big blocks there) |
| operand stack | 72 KB at the default `limits.stack_slots` (18 KB without PSRAM) | heap |
| P3D depth buffer (the wave) | 120 KB | heap |
| line buffer | 2 bytes x panel width | internal RAM |

Without PSRAM (an N8 board) the sketch takes a 64 KB internal arena and a 2048-slot operand stack
(18 KB), the configuration in which the serial self-test runs all its pieces, P3D included, on an
ESP32-S3 without PSRAM in QEMU. Pieces from the site then need `arena_bytes` of 64 KB or less.
A piece that does not fit is not a crash: the VM stops it with a memory fault, the serial monitor
prints e.g. `the wave: stopped: not enough memory for P3D (...)`, the last frame stays on screen
and the next piece comes at the usual time.

PlatformIO build (pioarduino 55.03.311, Arduino-ESP32 3.3, `esp32-s3-devkitc-1`, 8 MB flash):
flash 1,167,695 bytes (34.9 % of the 3.2 MB app partition), static RAM 64,580 bytes (19.7 %).
