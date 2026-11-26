# Third-party material and licences

Everything in this repository is MIT-licensed (`LICENSE`) except what this file lists.

## Processing Sans Pro (Adobe Source Sans): SIL Open Font License 1.1

The default sketch font is Processing's "Processing Sans Pro", which is Adobe Source Sans renamed
(https://github.com/processing/processing/issues/4747). Its glyph outlines are stored as data in:

* `python/immanence/pde/font_data.py`
* `src/pde_font.cpp`, `src/pde_font.h` (generated from the above by `host/gen_font.py`)

Licence: `LICENSES/OFL-1.1-ProcessingSansPro.txt`. Copyright 2010, 2012, 2014 Adobe Systems
Incorporated, with Reserved Font Name 'Source'.

## Code that follows Processing's source: GNU LGPL 2.1

Two parts reproduce Processing's own output bit for bit by following its source code
(processing/core, Processing 4.5.2, GNU Lesser General Public License version 2.1). Those files
are offered under the LGPL 2.1 (`LICENSES/LGPL-2.1.txt`), not MIT:

* `noise()`: Processing's Perlin noise (PApplet.noise / noiseDetail / noiseSeed), in
  `python/immanence/pde/pnoise.py`, `src/pde_noise.cpp`.
* P3D, the software 3D renderer, which follows PGraphicsOpenGL, InGeometry and the light, line and
  point shaders: `python/immanence/pde/three.py` and the 3D methods of `python/immanence/pde/runtime.py`
  (camera, projection, lights, materials, box, sphere), and their C++ ports `src/pde_three.cpp`,
  `src/pde_three.h`, `src/pde_api3.cpp`.

What this means for you (a plain reading, not legal advice): if you ship firmware that contains
these files, the LGPL asks you to let its users replace them with a modified copy, which in
practice means publishing these files' source (they are here) together with your firmware's
object files or source. The same applies to the WebAssembly build (`wasm/`), which contains them
too; immanencepure.xyz serves that build and points here for its source.

## BitMapMathLib: MIT

`python/immanence/mathlib.pde` is BitMapMathLib (https://github.com/MoonFingerRF/BitMapMathLib),
MIT, by the same author, used as a prelude.

## Compatibility, not copies

The language and API follow Processing (https://processing.org) by name and behaviour so that
sketches run unchanged. The 2D renderer, text layout, number formatting and random numbers
reproduce Processing 4.5.2's output as measured from outside (running real Processing and
comparing pixels); `java.util.Random`'s generator follows its published API specification.
None of that code is copied from Processing or the JDK.

## The example sketches

The sketches in `skill/` (and those served by https://immanencepure.xyz/api/v1/) were written
for immanencepure.xyz and are MIT-licensed like the code.
