#pragma once
// Microcontroller builds run without C++ exceptions, so a std::vector that cannot get its memory
// aborts the whole chip. The VM's few big blocks (its operand stack, the P3D depth buffer) are
// probed first, so "not enough memory" becomes an ordinary load failure or fault instead.
#include <cstddef>
#include <cstdlib>

namespace pde {

inline bool can_alloc(size_t bytes) {
  void *p = std::malloc(bytes);
  if (!p) return false;
  std::free(p);
  return true;
}

}  // namespace pde
