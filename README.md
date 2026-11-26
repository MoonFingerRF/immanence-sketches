# immanence-sketches

Run small Processing sketches on an ESP32 and any little display.

This is the engine behind [immanencepure.xyz](https://immanencepure.xyz), where more than two
thousand generative pieces run live. Each piece is a Processing (`.pde`) sketch for a 240 × 128
canvas. A compiler turns it into compact bytecode (usually under 2 KB), and a small, safe virtual
machine in plain C++17 runs that bytecode on a microcontroller. The VM draws the same pixels,
bit for bit, as the reference interpreter the site was built with.

The repository has three parts:

1. **The library**: the C++ VM (an Arduino / PlatformIO library and an ESP-IDF component), the
   compiler and a reference interpreter in Python (`pip install`), the same compiler in
   JavaScript, and a native host runner for testing without hardware.
2. **The site API**: every piece on immanencepure.xyz as plain files: its source, its title and
   description, and its precompiled bytecode, one request each.
3. **A skill for writing new sketches** with any language model, even a small local one: a
   language reference, examples, and a compile → run → check loop the model can follow.

> **Status (1.0.0).** The VM is the one that runs the art on the original ESP32-S3 e-paper
> displays. The example projects in `examples/` have been built with ESP-IDF, PlatformIO and the
> Arduino CLI, and the serial example's frames were checked bit for bit in the ESP32 and ESP32-S3
> emulator (QEMU). They have **not yet been run on real boards and displays** in this form.

## Quick start

### Compile and check a sketch on your computer

```sh
pip install "git+https://github.com/MoonFingerRF/immanence-sketches#subdirectory=python"
immanence check my_piece.pde --sheet preview.png    # compiles, runs 60 frames, checks it draws
immanence compile my_piece.pde                      # -> my_piece.pdeb (bytecode)
immanence compile my_piece.pde --c my_piece         # -> my_piece.h, to embed in firmware
```

The Python package has no dependencies. For fast checks, build the native runner (the C++ VM for
your computer) and the commands use it automatically:

```sh
make -C host && export IMMANENCE_VM=$PWD/host/build/immanence-vm
```

### Run it on an ESP32

Add the library (Arduino IDE: *Sketch → Include Library → Add .ZIP Library* with this repository's
zip; PlatformIO: `lib_deps = https://github.com/MoonFingerRF/immanence-sketches.git`; ESP-IDF: see
below), then:

```cpp
#include <immanence.h>
#include "my_piece.h"                       // from `immanence compile my_piece.pde --c my_piece`

uint8_t *arena = (uint8_t *) ps_malloc(immanence::DEFAULT_ARENA);   // PSRAM; see docs/MEMORY.md
pde::Vm *vm = new pde::Vm(arena, immanence::DEFAULT_ARENA);

void setup() { vm->load(my_piece, my_piece_len); }

void loop() {
  if (vm->next_frame()) show(vm->grey());   // 240 x 128 grey bytes, 0 = black
}
```

`show()` is yours: `docs/DISPLAYS.md` shows TFT, OLED and e-paper code, and how to scale or crop
the canvas onto any size. Complete projects:

| Example | Board | Display |
|---|---|---|
| [`examples/esp-idf-serial`](examples/esp-idf-serial) | any ESP32 | none: the serial console, with a bit-exactness self-test |
| [`examples/tft-st7789`](examples/tft-st7789) | ESP32-S3 with PSRAM | SPI ST7789 colour TFT |
| [`examples/oled-ssd1306`](examples/oled-ssd1306) | any ESP32 (no PSRAM) | 128 × 64 I²C SSD1306 OLED |
| [`examples/fetch-from-site`](examples/fetch-from-site) | ESP32-S3 with PSRAM | downloads pieces from the site API over HTTPS |

ESP-IDF projects depend on the component from their `main/idf_component.yml`:

```yaml
dependencies:
  immanence-sketches:
    git: https://github.com/MoonFingerRF/immanence-sketches.git
    version: v1.0.0
```

### Get pieces from immanencepure.xyz (the API)

Every piece on the site is available as plain files, one request each, with CORS open so any web
page can use them too:

| URL | What |
|---|---|
| `https://immanencepure.xyz/api/v1/index.json` | all pieces: id, title, description and facts |
| `https://immanencepure.xyz/api/v1/info/<id>.json` | one piece, with its arena size and golden frame hashes |
| `https://immanencepure.xyz/api/v1/source/<id>.pde` | its Processing source |
| `https://immanencepure.xyz/api/v1/bytecode/<id>.pdeb` | its precompiled program, ready for the VM |

```sh
immanence fetch list                 # every piece: id and title
immanence fetch <id>                 # writes <id>.pde, <id>.pdeb and <id>.json here
curl -O https://immanencepure.xyz/api/v1/bytecode/<id>.pdeb
```

On an ESP32, [`examples/fetch-from-site`](examples/fetch-from-site) downloads pieces over HTTPS
and runs them. [docs/API.md](docs/API.md) documents every field, what v1 promises (ids never
change meaning), and has curl, Python, ESP32 and browser examples.

### Write new sketches with a language model (the skill)

[`skill/`](skill) teaches any model, even a small local one (1–8B parameters), to write new
pieces that run. The model writes a sketch; `immanence check` compiles it, runs 60 frames and
says exactly what is wrong (with the failing line); the model fixes it and checks again. In our
test with a 3.8B model on a CPU, about half of first attempts passed, and about three in four
after up to three rounds of feedback.

**With an agent that loads Agent Skills** (Claude Code and others): copy the skill folder into
your skills directory, for Claude Code:

```sh
pip install "git+https://github.com/MoonFingerRF/immanence-sketches#subdirectory=python"
cp -r skill/sketch-writer ~/.claude/skills/        # or .claude/skills/ inside your project
```

Then ask: *"write a new sketch about tides for the 240×128 VM"*. The agent reads
`reference/language.md`, the library reference and a few examples, writes the piece, runs
`scripts/check.sh` until it passes, and can look at the contact sheet it renders.

**With any model behind an OpenAI-compatible API** (llama.cpp, Ollama, vLLM, LM Studio or a
hosted API), use the plain prompt and the loop in `skill/generate.py`:

```sh
llama-server -m some-small-instruct-model.gguf --port 8080 &      # or any other endpoint
python3 skill/generate.py --endpoint http://127.0.0.1:8080/v1 --model local -n 10 --retries 3
```

Each passing piece is saved with a contact sheet. `skill/prompt/system.md` (about 2,800 tokens)
is the whole instruction if you want to use it in your own tool; `skill/README.md` has the details.

## What the language is

Processing (Java syntax) for one 240 × 128 grey canvas: `setup()` / `draw()`, the 2D drawing API
(shapes, curves, text, transforms, `pixels[]`, blend modes and filters), P3D
(boxes, spheres, custom shapes, lights, camera), `random()` / `noise()` exactly as Processing
computes them, the time functions, and a library of mathematical helpers
([BitMapMathLib](https://github.com/MoonFingerRF/BitMapMathLib)) linked in automatically when a
sketch calls it. A few deliberate differences: edges are crisp unless the sketch calls `smooth()`,
the canvas is always 240 × 128, randomness is seeded so every run is the same, and `millis()`
counts frames (60 per second) so frames are reproducible. Some Java and Processing classes are
not available on the VM (`PImage` / `createGraphics`, `ArrayList`, `IntList`, `HashMap`, `PVector`,
`split()`): the compiler says so clearly.
The full list: [docs/LANGUAGE.md](docs/LANGUAGE.md).

## Documentation

* [docs/LANGUAGE.md](docs/LANGUAGE.md): the supported Processing subset
* [docs/DISPLAYS.md](docs/DISPLAYS.md): the canvas model; putting frames on TFT, OLED, e-paper; scaling and cropping
* [docs/MEMORY.md](docs/MEMORY.md): RAM and PSRAM needs, per-piece arena sizes, frame time
* [docs/BYTECODE.md](docs/BYTECODE.md): the bytecode format and its version, the VM's safety checks
* [docs/API.md](docs/API.md): the immanencepure.xyz API

## Repository layout

```
src/        the C++ VM (Arduino / PlatformIO library, ESP-IDF component); immanence.h
host/       the native host runner (make -C host)
python/     the compiler, the reference interpreter and the `immanence` command
js/         the compiler in JavaScript (browser and Node)
examples/   ESP32 projects
skill/      the sketch-writing skill and prompt
docs/       documentation
```

## Tests

```sh
cd python && IMMANENCE_VM=../host/build/immanence-vm python3 -m unittest discover -s tests
```

The tests check the compiler, the `check` gate, the native VM against the reference interpreter
frame by frame, and the JavaScript compiler against the Python one byte by byte.

## Licence

MIT (`LICENSE`), including the sketches. Two parts follow Processing's own source to draw exactly
as Processing does, and are therefore under the GNU LGPL 2.1 instead: `noise()`
(`pde/pnoise.py`, `src/pde_noise.cpp`) and the 3D renderer (`pde/three.py`, `src/pde_three.*`,
`src/pde_api3.cpp`). The default font's outlines are Adobe Source Sans (SIL OFL 1.1). Details, and
what the LGPL asks of you if you ship firmware with those files: [THIRD-PARTY.md](THIRD-PARTY.md).
