---
name: sketch-writer
description: Write a new Processing sketch (Java mode) that compiles to bytecode and runs on the small ESP32 virtual machine of this repository, 240 x 128 greyscale, and check it until it passes. Use when asked to make, write, fix or port a sketch, generative picture or animation for this VM, or when a .pde file fails the `immanence check` command.
---

# Writing a sketch for the VM

A sketch is a Processing 4 (Java mode) program for a 240 x 128 greyscale canvas. The VM runs a
subset of Processing plus a built-in library. A sketch is done when `scripts/check.sh` passes:
it compiles, runs 60 frames without a fault, and every frame, from the first, is a picture (some
ink, not all ink).

## Read first

* `reference/language.md`: what the compiler accepts, what it does NOT (no ArrayList, PVector,
  PImage, PGraphics, split()), the limits and the Java pitfalls.
* `reference/library.md`: the built-in library (curves, plots, cells, fractals, patterns, ...).
* `examples/`: about thirty sketches that pass. Open two or three to see the medium, then make
  your own piece: your own idea, not a copy of an example.

## The loop

1. Write the sketch to a file, e.g. `piece.pde`, in this form:

   ```processing
   // piece "a short title"
   // why "one sentence about what the piece is"
   void setup() {
     size(240, 128);
   }
   void draw() {
     background(255);
     // ...
   }
   ```

2. Check it: `scripts/check.sh piece.pde --sheet piece.png`
   (it runs the repository's `immanence check`; set `PYTHONPATH` to the repository's `python/`
   folder if the command is not installed, and `IMMANENCE_VM` to `host/build/immanence-vm` for the
   fast native runner).
3. If it prints `FAIL`, read the kind, the message and the quoted line, fix that, and check again.
   Common fixes:
   * `unsupported: ArrayList/PVector/PImage/...`: use fixed-size arrays, two floats, or draw directly.
   * `X() is not defined`: write the function yourself, or use one from the references.
   * `picture: blank ... on frame 1`: draw something dark and visible on frame 1 (a curve drawn
     with `p = loopT(...)` starts empty: use `0.2 + 0.8 * loopT(...)`).
   * `picture: all ink`: start with `background(255)` and leave paper showing.
   * `budget`: less work per frame; check every loop ends (a fractional step needs a float counter).
   * `runtime` with no line: run `immanence run piece.pde --frames N` (the reference interpreter)
     to get the line.
4. Repeat until it prints `OK`. Then look at the contact sheet (`piece.png`, 8 frames across the 60)
   if you can see images: check that the picture is what you meant and that it moves (or stays
   still) as you meant. A pass only means it runs; whether it is the piece you intended is yours
   to judge.

## Output

Give the final sketch as one `processing` code block, starting with the `// piece` and `// why`
lines, and save it as `<title-slug>.pde`. Say whether it is still or animated and that it passed.
To put it on a device: `immanence compile piece.pde` writes the bytecode (`piece.pdeb`), and
`immanence compile piece.pde --c NAME` a C header with the program and golden frame hashes.

## Notes

* Colours are single greys: 0 = black ink, 255 = white paper. Greys become dot patterns.
* Animation: globals keep their values between frames; `frameCount` counts 1, 2, 3, ...
  A sketch can run for hours, so keep anything that grows bounded.
* `size(240, 128, P3D)` switches on 3D (boxes, spheres, lights, camera).
* For a model without tools, `../prompt/system.md` is the same knowledge as one system prompt,
  and `../generate.py` runs the write, check, fix loop against any OpenAI-compatible endpoint.
