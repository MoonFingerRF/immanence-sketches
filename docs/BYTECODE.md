# The bytecode format (version 1)

A compiled sketch is one self-contained binary, usually 0.3–3 KB. The compiler (Python
`immanence compile`, or `js/compile.mjs`) writes it; the C++ VM (`src/`) loads and runs it.
Files use the extension `.pdeb`.

## Versioning

* Byte 4 is the format version. This document describes **version 1**
  (`BYTECODE_VERSION` in Python, `pde::BC_VERSION` / `immanence::BYTECODE_VERSION` in C++).
* The VM refuses a program whose version differs from its own (`load()` returns false,
  `fault_msg()` = "program version"). A device can check byte 4 before downloading the rest.
* The version changes only when the instruction set, the native table or the layout changes in a
  way an older VM cannot run. Library releases that keep the format keep the version.
* The site API reports the version of every piece (`bytecode.version` in `index.json`).

## Layout (little endian)

| Field | Type | Meaning |
|---|---|---|
| magic | 4 bytes | `PDEB` |
| version | u8 | 1 |
| flags | u8 | bit 0: the sketch reads the wall clock (`hour()`, `minute()`, `day()` ...) |
| reserved | u16 | 0 |
| entries | 4 × i16 | function index of: global initialisers, static initialisers, `setup()`, `draw()`; -1 = none |
| globals | u16 n, then n × u8 | the type of each global slot: `I` int, `L` long, `F` float, `D` double, `R` reference |
| classes | u16 n, then per class: u16 nfields + nfields × u8 | field types, as for globals |
| longs | u16 n, then n × i64 | the long constant pool |
| doubles | u16 n, then n × f64 | the double constant pool |
| strings | u16 n, then per string: u16 length + length × u16 | UTF-16 string constants |
| functions | u16 n, then per function: u8 nparams, u8 flags, u16 nslots, u16 maxstack, u8 returns, u32 code offset, u16 code length | flags bit 0: a method (receiver on the stack), bit 1: a constructor wrapper |
| code | u32 size, then the code of every function | instructions |

An instruction is one opcode byte followed by its operands (`i` int32, `f` float32 bits, `H` u16,
`B` u8, `b` i8). The opcodes, their operands and stack effects, and the table of native calls
(the Processing API: `ellipse`, `noise`, `fill`, ...) are defined once in
`python/immanence/pde/bcspec.py`; `src/pde_spec.h` is generated from it
(`python3 -m immanence.pde.bcspec > src/pde_spec.h`), so both sides always agree.

## Safety

The VM verifies a program when it loads it: every index (constants, globals, fields, functions,
jumps) is bounds-checked and the operand-stack depth of every instruction is recomputed and must
match `maxstack`. While running, every reference is checked when used, and a frame is bounded by
a step budget (4,000,000 by default), an optional wall-clock deadline, a call-depth limit and the
arena. A program can reach only its own values and the drawing API: there is no I/O. Breaking a
limit stops the sketch with a fault (`vm.fault()`, `vm.fault_msg()`); it never crashes the host.
That makes it reasonable to run pieces downloaded from the site, or written by a language model.

## Golden frames

`immanence compile piece.pde --c NAME` also writes `NAME_golden[]`: crc32 of the 240×128 grey
canvas for the first 30 frames, as the reference interpreter draws them. Compare them with
`pde::crc32(vm.grey(), 240 * 128)` after each frame to prove that a new build of the VM (a new
board, compiler or flag) draws exactly. The site API carries the same hashes (`golden`).
Pieces that read the clock have none: they draw differently at every time.
