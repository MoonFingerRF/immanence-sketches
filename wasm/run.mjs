#!/usr/bin/env node
// Run a compiled piece on the WebAssembly VM and print one line per frame, like the native
// host runner: "F <frame> <crc32> <steps>", then "END done|running|fault <msg>".
//   node wasm/run.mjs piece.pdeb [frames] [--arena BYTES]
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const file = args[0], frames = Number(args[1] || 60);
const ai = args.indexOf("--arena"), arena = ai >= 0 ? Number(args[ai + 1]) : 1536 * 1024;
if (!file) { console.error("usage: node wasm/run.mjs piece.pdeb [frames] [--arena BYTES]"); process.exit(2); }
const { default: createPdeVm } = await import(pathToFileURL(resolve(here, "build", "pde_vm.js")).href);
const M = await createPdeVm();
const crcTable = new Uint32Array(256).map((_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
const crc32 = (a) => { let c = 0xffffffff; for (const b of a) c = crcTable[(c ^ b) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };

const prog = readFileSync(file);
const idx = M._gvm_create(arena);
const p = M._malloc(prog.length); M.HEAPU8.set(prog, p);
if (!M._gvm_load(idx, p, prog.length)) { console.log("END bad_program " + M.UTF8ToString(M._gvm_fault_msg(idx))); process.exit(0); }
M._gvm_set_clock(idx, 0, 0);
for (let i = 0; i < frames; i++) {
  if (!M._gvm_next_frame(idx)) break;
  const g = M.HEAPU8.subarray(M._gvm_grey(idx), M._gvm_grey(idx) + 240 * 128);
  console.log(`F ${M._gvm_frame(idx)} ${crc32(g).toString(16).padStart(8, "0")} ${M._gvm_last_steps(idx)}`);
  if (M._gvm_done(idx)) break;
}
const f = M._gvm_fault(idx);
console.log(f ? `END fault ${M.UTF8ToString(M._gvm_fault_msg(idx))}` : (M._gvm_done(idx) ? "END done" : "END running"));
