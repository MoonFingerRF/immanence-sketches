# The VM in WebAssembly (browsers and Node)

The same C++ sources as the ESP32 library (`../src`), compiled with emscripten and the same
flags, so a web page draws exactly the pixels a device draws. This is the engine
[immanencepure.xyz](https://immanencepure.xyz) runs, one VM per piece, in Web Workers.

```sh
source <emsdk>/emsdk_env.sh          # https://emscripten.org/docs/getting_started/
sh wasm/build.sh                     # -> wasm/build/pde_vm.js + pde_vm.wasm (an ES module)
node wasm/run.mjs piece.pdeb 60      # F <frame> <crc32> <steps> per frame, like host/build/immanence-vm
```

## Use it in a page

```js
import createPdeVm from "./pde_vm.js";
const M = await createPdeVm();
const prog = new Uint8Array(await (await fetch("https://immanencepure.xyz/api/v1/random.pdeb")).arrayBuffer());
const vm = M._gvm_create(1536 * 1024);             // arena bytes (see docs/MEMORY.md)
const p = M._malloc(prog.length); M.HEAPU8.set(prog, p);
if (!M._gvm_load(vm, p, prog.length)) throw new Error(M.UTF8ToString(M._gvm_fault_msg(vm)));
M._gvm_set_clock(vm, Date.now() / 1000, -new Date().getTimezoneOffset() * 60);
const img = new ImageData(240, 128);
function frame() {
  if (!M._gvm_next_frame(vm)) return;              // stopped: still piece finished, or a fault
  const g = M.HEAPU8.subarray(M._gvm_grey(vm), M._gvm_grey(vm) + 240 * 128);
  for (let i = 0; i < g.length; i++) img.data.set([g[i], g[i], g[i], 255], i * 4);
  ctx.putImageData(img, 0, 0);
  if (!M._gvm_done(vm)) requestAnimationFrame(frame);
}
frame();
```

The exported functions (`vm_wasm.cpp`): `gvm_create(arena) -> idx`, `gvm_load(idx, ptr, len)`,
`gvm_set_clock(idx, epoch_s, tz_s)`, `gvm_seed(idx, seed)` (a seed for pieces that do not seed
themselves), `gvm_next_frame(idx)`, `gvm_grey(idx)` (pointer to 240 × 128 grey bytes),
`gvm_frame`, `gvm_done`, `gvm_fault`, `gvm_fault_msg`, `gvm_last_steps`, `gvm_heap_used`,
`gvm_reset`, `gvm_destroy`. Up to 512 VMs live at once.
