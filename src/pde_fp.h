#pragma once
// Bit-exact arithmetic: the VM draws the same pixels as the reference interpreter only if the
// compiler never fuses a*b+c into one fused multiply-add (one rounding instead of two) and never
// uses fast-math. Build systems that can pass flags add -ffp-contract=off -fno-fast-math; this
// header enforces the first from the source too, for builds that cannot (the Arduino IDE).
// It is the first include of every .cpp in this library.
#if defined(__FAST_MATH__)
#error "immanence-sketches: do not build with -ffast-math (it breaks bit-exact drawing)"
#endif
#if defined(__clang__)
#pragma STDC FP_CONTRACT OFF
#elif defined(__GNUC__)
#pragma GCC optimize("fp-contract=off")
#endif
