#!/bin/sh
# Build the VM to WebAssembly: wasm/build/pde_vm.js + pde_vm.wasm (an ES module, for browsers,
# Web Workers and Node). Needs emscripten (emsdk) on PATH: `source <emsdk>/emsdk_env.sh`.
#   sh wasm/build.sh [out dir]
# -ffp-contract=off and no -ffast-math keep it bit-exact with every other build of the VM.
set -e
cd "$(dirname "$0")/.."
OUT=${1:-wasm/build}
mkdir -p "$OUT"
command -v em++ >/dev/null 2>&1 || { echo "em++ not found: install emsdk and source emsdk_env.sh" >&2; exit 2; }
SRCS=$(ls src/*.cpp | grep -v 'src/immanence.cpp')
em++ -std=c++17 ${OPT:--O2} -ffp-contract=off -fno-fast-math \
  -Isrc -include wasm/utf16_compat.h wasm/vm_wasm.cpp $SRCS \
  -o "$OUT/pde_vm.js" \
  -sMODULARIZE=1 -sEXPORT_ES6=1 -sEXPORT_NAME=createPdeVm \
  -sENVIRONMENT=web,worker,node -sALLOW_MEMORY_GROWTH=1 -sINITIAL_MEMORY=16MB \
  -sFILESYSTEM=0 -sASSERTIONS=0 \
  -sEXPORTED_FUNCTIONS='["_gvm_create","_gvm_destroy","_gvm_reset","_gvm_load","_gvm_set_clock","_gvm_seed","_gvm_next_frame","_gvm_grey","_gvm_frame","_gvm_done","_gvm_fault","_gvm_fault_msg","_gvm_last_steps","_gvm_heap_used","_gvm_max_instances","_malloc","_free"]' \
  -sEXPORTED_RUNTIME_METHODS='["HEAPU8","HEAPU32","UTF8ToString"]'
echo "built $OUT/pde_vm.js + pde_vm.wasm"
