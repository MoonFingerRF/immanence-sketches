# Putting the canvas on a display

Every frame is a 240 × 128 grey canvas: `vm.grey()` returns 30,720 bytes, row-major, one byte
per pixel, 0 = black (ink) and 255 = white (paper). The VM never talks to a display; after each
`vm.next_frame()` you copy the canvas to yours. `src/immanence.h` has the helpers below.

## Colour or grey TFT / OLED (ST7789, ILI9341, SSD1351 ...)

Convert each grey to the panel's colour format and push rows:

```cpp
uint16_t line[240];
for (int y = 0; y < immanence::HEIGHT; y++) {
  const uint8_t *row = vm.grey() + y * immanence::WIDTH;
  for (int x = 0; x < immanence::WIDTH; x++) line[x] = immanence::rgb565(row[x]);
  tft.drawRGBBitmap(0, y, line, immanence::WIDTH, 1);    // or your library's row/window write
}
```

Many pieces are drawn for one-bit displays and look best dithered: use `immanence::ink_at()`
per pixel (black or white) instead of the grey for that look.

## One-bit displays (SSD1306, SH1106, memory LCD, e-paper)

`immanence::to_1bit(grey, out, msb_first, ink_is_one)` packs the canvas into 3,840 bytes with the
reference 4 × 4 ordered dither, the exact look of immanencepure.xyz. Use `msb_first = true` for
row-major displays whose pixel 0 is the high bit (most e-paper and memory LCDs); on an OLED
"1 = lit pixel", so pass `ink_is_one = false` to draw ink as dark on a lit background or `true`
for light ink on black. SSD1306-style page layouts (8 vertical pixels per byte) need their own
loop: call `ink_at(grey, x, y)` per pixel.

## Other sizes: scale or crop

The canvas size is fixed at 240 × 128 in this version (the pieces are written for it). To fit
another display, scale or crop in your copy loop:

* `immanence::fit(grey, w, h, put)` calls `put(x, y, grey)` for every pixel of a `w × h` box with
  the canvas scaled to fit and centred: an integer scale when the box is large enough (2× on
  480 × 272, 3× on 800 × 480 ...), a fractional nearest-neighbour scale when it is smaller
  (0.5× on 128 × 64). The `border` argument fills the margins.
* To crop instead, copy a window: `grey[(y + oy) * 240 + (x + ox)]`.
* On a one-bit display, scale first, then dither at the destination pixel
  (`level > BAYER4[y & 3][x & 3]`, see `src/immanence.cpp`), so the dither pattern stays one
  display pixel fine.

## Slow displays

E-paper refreshes take 0.3–15 s. Draw every Nth frame, or use partial refresh, or run the VM in
its own task and show the latest finished frame whenever the display is ready. A still piece
(`vm.done()` after its first frame) needs one refresh only.

See `examples/` for complete projects (ST7789 TFT, SSD1306 OLED, serial console, and fetching
pieces from immanencepure.xyz).
