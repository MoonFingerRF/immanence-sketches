# random-minimal

The smallest complete program that shows random pieces from immanencepure.xyz: every 5 minutes
it asks the site for a random piece that fits the board's memory, checks it and runs it, printing
each frame as text on the serial monitor. Any ESP32 (an S3 with PSRAM runs every piece).

Set `WIFI_SSID` and `WIFI_PASSWORD` at the top of `random-minimal.ino`, then:

* Arduino IDE: open this folder, pick the board (S3: PSRAM OPI; classic ESP32: Partition Scheme
  "Huge APP"), upload, serial monitor at 115200.
* PlatformIO: `pio run -e esp32-s3-devkitc-1-n8r8 -t upload && pio device monitor` (or `-e esp32dev`).

The walkthrough, line by line: [docs/ESP32-RANDOM.md](../../docs/ESP32-RANDOM.md).
