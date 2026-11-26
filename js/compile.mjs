#!/usr/bin/env node
// Compile a Processing sketch to VM bytecode with the JavaScript compiler (no Python needed).
//   node js/compile.mjs piece.pde [-o piece.pdeb]
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { compileResult } from "./pde-compiler.js";

const here = dirname(fileURLToPath(import.meta.url));
const library = JSON.parse(readFileSync(resolve(here, "mathlib.json"), "utf8"));
// `import bmml.*;` lines (BitMapMathLib in desktop Processing) reach the compiler blank, as in Python.
const BMML_IMPORT = /^[ \t]*import[ \t]+bmml(?:[ \t]*\.[ \t]*\*)?[ \t]*;?[ \t]*(?:\/\/.*|\/\*.*\*\/[ \t]*)?$/gm;
export function compile(src) { return compileResult(src.replace(BMML_IMPORT, ""), library); }

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  const file = args.find((a, i) => !a.startsWith("-") && args[i - 1] !== "-o");
  if (!file) { console.error("usage: node js/compile.mjs piece.pde [-o piece.pdeb]"); process.exit(2); }
  const oi = args.indexOf("-o");
  const out = oi >= 0 ? args[oi + 1] : file.replace(/\.pde$/, "") + ".pdeb";
  const r = compile(readFileSync(file, "utf8"));
  if (!r.ok) { console.error(`${file}:${r.line}: ${r.kind}: ${r.message}`); process.exit(1); }
  writeFileSync(out, r.bytes);
  console.error(`${out}: ${r.bytes.length} bytes, bytecode v${r.bytes[4]}`);
}
