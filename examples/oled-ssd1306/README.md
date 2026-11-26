# oled-ssd1306: pieces on a 128x64 OLED, no PSRAM

Cycles through three embedded pieces (wanderers, three bodies, ridgelines) on a 128x64 I2C
SSD1306, one minute each, on any ESP32: it needs no PSRAM.

The canvas is fitted with `immanence::fit`: 240x128 into 128x64 is half scale, 120x64, centred
with 4 dark columns each side. Each OLED pixel then goes 1-bit through the reference's 4x4
ordered dither (the formula in `immanence::ink_at`), evaluated at the OLED pixel's own position so
the dither pattern stays regular after scaling. The sketch writes straight into the SSD1306
buffer (pages of 8 rows) and sends it with `display()`.

The first frames of each piece are checked against the reference crc32s and the serial monitor
prints e.g. `wanderers: MATCH 30/30 frames`.

## Board and wiring

Any ESP32 board, e.g. a classic ESP32 DevKit (`esp32dev`, ESP32-WROOM-32, no PSRAM), and a 128x64
SSD1306 I2C module (address 0x3C, some are 0x3D).

| SSD1306 | ESP32 GPIO | constant |
|---|---|---|
| SDA | 21 | `PIN_SDA` |
| SCL | 22 | `PIN_SCL` |
| VCC, GND | 3V3, GND | |

## Build

PlatformIO (`platformio.ini` builds this same `.ino` for `esp32dev`; it pulls the library from
`../..`):

```sh
cd examples/oled-ssd1306
pio run -t upload && pio device monitor
```

Arduino IDE 2: install this library, *Adafruit SSD1306* and *Adafruit GFX Library*; open File >
Examples > immanence-sketches > oled-ssd1306; board "ESP32 Dev Module".

## Memory (internal RAM only)

A classic ESP32 has about 300 KB of heap at boot, in a few regions; its largest free block is
about 110 KB. The sketch takes, in this order:

| what | size |
|---|---|
| VM object (`pde::Vm`: the 240x128 canvas and drawing state), kept for all pieces | about 71 KB |
| arena: the largest of 96 / 80 / 64 / 48 / 32 KB that still leaves room for the operand stack | 80 KB (Arduino-ESP32 3.3), 96 KB (4.0) |
| operand stack: `limits.stack_slots = 2048` (the default 8192 is 72 KB in two blocks) | about 18 KB |

The three pieces were chosen because they fit easily: they use at most 1.5 KB of the arena (half
of the arena is the live heap, the other half is for the copying collector), no P3D (whose depth
buffer alone is 120 KB) and no `loadPixels()` (120 KB of arena). Pieces from the site list their
`arena_bytes`; on this board pick ones at or below about 40 KB. One that needs more stops with a
memory fault printed on the serial monitor (`<name>: stopped: ...`) rather than crashing.

Verified in QEMU (classic ESP32, no PSRAM; the OLED itself is not emulated), all three pieces
`MATCH 30/30`: the PlatformIO build (Arduino-ESP32 3.3.11) got an 80 KB arena with 138 KB of heap
left; a plain `arduino-cli` build (Arduino-ESP32 4.0.0, no extra flags) a 96 KB arena with 148 KB
left.

PlatformIO build (pioarduino 55.03.311, Arduino-ESP32 3.3, `esp32dev`, 4 MB flash): flash 819,384
bytes (62.5 % of the 1.25 MB app partition), static RAM 49,792 bytes (15.2 %).
