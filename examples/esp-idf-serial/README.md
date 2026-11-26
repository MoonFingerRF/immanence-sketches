# esp-idf-serial: the self-test

An ESP-IDF project that proves a build draws exactly like the reference. It runs five embedded
pieces for their golden frames (30 for an animated piece, 1 for a still one), takes the crc32 of
every 240x128 grey frame and compares it with the crc the reference compiler recorded. Then it
prints a PASS/FAIL line and one frame as ASCII art. No display is needed.

| piece | file | what it exercises |
|---|---|---|
| interference | `main/pieces/interference.h` | per-pixel maths, about 180,000 VM steps a frame |
| three bodies | `main/pieces/three_bodies.h` | arrays, lines, a little arena use |
| ridgelines | `main/pieces/ridgelines.h` | `noise()` |
| quiet field | `main/pieces/quiet_field.h` | a still piece: textures, `noLoop()` |
| the wave | `main/pieces/the_wave.h` | P3D: a lit triangle strip |

The `.pde` sources sit beside the headers. To regenerate a header (or add your own piece):

```sh
PYTHONPATH=../../python python3 -m immanence compile main/pieces/ridgelines.pde --c ridgelines -o main/pieces/ridgelines.h
```

## Board and wiring

Any ESP32, ESP32-S3 (or other ESP-IDF 5.x target) board, nothing attached. The output goes to
the console UART (or USB serial/JTAG, whatever your board's console is).

## Build and run

ESP-IDF 5.0 or newer (tested with 5.5.5):

```sh
. ~/esp/esp-idf/export.sh
cd examples/esp-idf-serial
idf.py set-target esp32s3        # or esp32, ...
idf.py build flash monitor
```

No board? It runs in Espressif's QEMU (`idf_tools.py install qemu-xtensa`):

```sh
idf.py set-target esp32 && idf.py build && idf.py qemu monitor
```

### How the library is found

`CMakeLists.txt` adds the repository root (`../..`) to `EXTRA_COMPONENT_DIRS`; the root has the
component's `CMakeLists.txt`. The component takes its directory's name, so `main` does not name it
in `REQUIRES` (a `main` without `REQUIRES` sees every component). In your own project either do the
same, or depend on it from `main/idf_component.yml`:

```yaml
dependencies:
  immanence-sketches:
    git: https://github.com/MoonFingerRF/immanence-sketches.git
    # or, for a checkout: path: ../../immanence-sketches  (the directory must be called immanence-sketches)
```

and add `immanence-sketches` to `REQUIRES` in your component.

Keep `-ffp-contract=off -fno-fast-math` on any of your files that do maths you compare with the
reference; the library sets them for itself.

## What you should see

```
immanence-sketches serial self-test: esp32s3 rev 0.3, 2 core(s), bytecode v1
...
== interference (362 bytes of bytecode, 30 golden frames)
F 1 d3800c48 MATCH 2186 ms        (the time is from QEMU; a real board is much faster)
...
== memory faults, on purpose
-- three bodies in a 2 KB arena (it needs about 1.5 KB per half):
fault after frame 0 (memory): the sketch ran out of memory (arena)
-- three bodies with limits.stack_slots = 64 M (a 512 MB stack):
load failed: not enough memory for the VM stack (lower limits.stack_slots)
-- memory faults: reported cleanly, as expected

PASS: 5 of 5 pieces match the reference frame for frame
```

`F <n> <crc> MATCH|DIFF <ms>` is one frame: its number, crc32, the verdict and how long it took.
A `DIFF` means this build draws differently from the reference: please open an issue with the
log, the board and the compiler version.

A piece that needs more memory than the board has is not a failure: the VM stops it with a
`memory` fault (or refuses to load it) and says why, and the summary counts it separately. On a
classic ESP32 without PSRAM the P3D piece does this:

```
== the wave (435 bytes of bytecode, 30 golden frames)
fault after frame 0 (memory): not enough memory for P3D (its depth buffer needs 120 KB in one block)
piece the wave: does not fit this board's memory
...
PASS: 4 of 5 pieces match the reference frame for frame, 1 did not fit in memory
```

The "memory faults, on purpose" section shows both kinds on every board: a sketch whose data
outgrows its arena (`vm.fault() == pde::Fault::Memory`, `vm.fault_msg()` says which limit), and a
`vm.load()` that returns false because the operand stack does not fit.

## Memory

| what | size | where |
|---|---|---|
| arena | 1.5 MB with PSRAM, else 64 KB | `heap_caps_malloc`: PSRAM first, internal RAM if the board has none |
| VM object (`pde::Vm`: the canvas and drawing state) | 72,600 bytes in one block | heap |
| operand stack | 9 bytes x `limits.stack_slots`: 72 KB at the default 8192, 18 KB at 2048 | heap |
| P3D depth buffer | 120 KB in one block | heap, only for P3D pieces |

These five pieces use at most 1.5 KB of the arena. `sdkconfig.defaults` turns PSRAM on with
`CONFIG_SPIRAM_IGNORE_NOTFOUND`, so the same build boots with or without it. Without PSRAM the
example lowers `limits.stack_slots` to 2048 (a classic ESP32's largest free block is about 110
KB, too small for the default stack beside the VM and the arena). On a classic ESP32 without
PSRAM the P3D piece then stops with the memory fault above; an ESP32-S3 without PSRAM runs all
five from internal RAM. The program prints the free heap at each step.

Build (ESP-IDF 5.5.5, GCC 14.2, `CONFIG_COMPILER_OPTIMIZATION_PERF`): image 506,976 bytes on esp32
(static DRAM 50,064 bytes), 510,256 bytes on esp32s3 (static D/IRAM 91,867 bytes); about half of
the default 1 MB app partition.
