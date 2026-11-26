// One compatibility header so the VM sources compile under wasm WITHOUT being edited.
//
// The VM keeps strings as `std::basic_string<uint16_t>` (pde_natives.cpp: `using U16 =
// std::basic_string<uint16_t>`), because Processing's strings are UTF-16 code units. On the ESP32
// and on the host runner that works through libstdc++, whose primary `std::char_traits`
// template is defined generically. Emscripten ships libc++, which defines char_traits only for
// char / wchar_t / char8_t / char16_t / char32_t, so the same source fails to instantiate there.
//
// build.sh forces this header into every translation unit (-include). It adds the missing
// specialization and nothing else: the VM's own code, its flags (-ffp-contract=off -fno-fast-math)
// and its behaviour are untouched.
#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <cwchar>
#include <string>

namespace std {

template <>
struct char_traits<unsigned short> {
  using char_type = unsigned short;
  using int_type = unsigned int;
  using off_type = long long;
  using pos_type = long long;
  using state_type = mbstate_t;

  static constexpr void assign(char_type &a, const char_type &b) noexcept { a = b; }
  static constexpr bool eq(char_type a, char_type b) noexcept { return a == b; }
  static constexpr bool lt(char_type a, char_type b) noexcept { return a < b; }

  static constexpr int compare(const char_type *s1, const char_type *s2, size_t n) {
    for (size_t i = 0; i < n; i++)
      if (s1[i] != s2[i]) return s1[i] < s2[i] ? -1 : 1;
    return 0;
  }
  static constexpr size_t length(const char_type *s) {
    size_t n = 0;
    while (s[n] != char_type(0)) n++;
    return n;
  }
  static constexpr const char_type *find(const char_type *s, size_t n, const char_type &a) {
    for (size_t i = 0; i < n; i++)
      if (s[i] == a) return s + i;
    return nullptr;
  }

  static char_type *move(char_type *d, const char_type *s, size_t n) {
    if (n) std::memmove(d, s, n * sizeof(char_type));
    return d;
  }
  static char_type *copy(char_type *d, const char_type *s, size_t n) {
    if (n) std::memcpy(d, s, n * sizeof(char_type));
    return d;
  }
  static char_type *assign(char_type *d, size_t n, char_type a) {
    for (size_t i = 0; i < n; i++) d[i] = a;
    return d;
  }

  static constexpr int_type not_eof(int_type c) noexcept { return c != eof() ? c : int_type(0); }
  static constexpr char_type to_char_type(int_type c) noexcept { return char_type(c); }
  static constexpr int_type to_int_type(char_type c) noexcept { return int_type(c); }
  static constexpr bool eq_int_type(int_type a, int_type b) noexcept { return a == b; }
  static constexpr int_type eof() noexcept { return int_type(0xFFFFFFFFu); }
};

}  // namespace std
