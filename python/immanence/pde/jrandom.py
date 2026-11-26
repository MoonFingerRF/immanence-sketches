"""Processing's randomness, bit for bit: java.util.Random, PApplet.random/randomGaussian, and
PApplet.noise (Ken Perlin's noise as Processing implements it: a 4096-entry table filled from its
own java.util.Random, a 720-entry cosine table, 4 octaves, falloff 0.5 by default)."""
from __future__ import annotations

import math
import struct

from .pnoise import ProcessingNoise, noise_tables

_F = struct.Struct("<f")
_MASK48 = (1 << 48) - 1
_MUL = 0x5DEECE66D


def f32(x: float) -> float:
    """Round a Python float to Java float precision (overflow to +-inf, as Java does)."""
    try:
        return _F.unpack(_F.pack(x))[0]
    except OverflowError:
        return math.copysign(math.inf, x)


def _idx(v: float) -> int:
    """(int)(v * 360) with Java's float multiply: only a product just under an integer can round
    up to it in 32 bits, so only those pay for the exact rounding."""
    p = v * 360
    i = int(p)
    if p - i > 0.9999:
        i = int(f32(p))
    return i % 720


RETRIES = 64          # random()'s redraw loops, bounded (the C++ VM: src/pde_random.cpp, the same)


class JavaRandom:
    __slots__ = ("seed", "_gauss")

    def __init__(self, seed: int = 0) -> None:
        self.set_seed(seed)

    def set_seed(self, seed: int) -> None:
        self.seed = (seed ^ _MUL) & _MASK48
        self._gauss = None

    def next(self, bits: int) -> int:
        self.seed = (self.seed * _MUL + 0xB) & _MASK48
        v = self.seed >> (48 - bits)
        if v >= 1 << (bits - 1) and bits == 32:
            v -= 1 << 32
        return v

    def next_float(self) -> float:
        return self.next(24) / float(1 << 24)

    def next_double(self) -> float:
        return ((self.next(26) << 27) + self.next(27)) * (1.0 / (1 << 53))

    def next_int(self, bound: int | None = None) -> int:
        if bound is None:
            return self.next(32)
        if bound <= 0:
            raise ValueError("bound must be positive")
        if bound & -bound == bound:
            return (bound * self.next(31)) >> 31
        while True:
            bits = self.next(31)
            val = bits % bound
            if bits - val + (bound - 1) < (1 << 31):
                return val

    def next_gaussian(self) -> float:
        if self._gauss is not None:
            g, self._gauss = self._gauss, None
            return g
        while True:
            v1 = 2 * self.next_double() - 1
            v2 = 2 * self.next_double() - 1
            s = v1 * v1 + v2 * v2
            if 0 < s < 1:
                break
        mul = math.sqrt(-2 * math.log(s) / s)    # StrictMath in Java; the same to the last bit here
        self._gauss = v2 * mul
        return v1 * mul


class Randomness(ProcessingNoise):
    """The sketch's random() and noise() state (noise() itself: pnoise.py)."""

    def __init__(self) -> None:
        self.rnd = JavaRandom(0)          # seeded with 0 before setup(): every run draws the same frames
        self.perlin_rnd = JavaRandom(0)   # ... and noiseSeed(0)
        self.perlin: list[float] | None = None
        self.octaves = 4
        self.falloff = 0.5
        self.cos, self.fsc = noise_tables()

    # -------------------------------------------------------------- random
    def seed(self, s: int) -> None:
        self.rnd.set_seed(int(s))

    def random(self, high: float) -> float:
        if high == 0 or high != high:
            return 0.0
        if high in (math.inf, -math.inf):
            # PApplet redraws until value != high, and nextFloat() * inf is inf unless the draw is 0
            # (~2^24 draws): one draw, and the NaN Processing would end with
            self.rnd.next_float()
            return math.nan
        v = high
        for _ in range(RETRIES):         # v == high is a 2^-24 rounding case: never 64 in a row
            v = f32(self.rnd.next_float() * high)
            if v != high:
                return v
        return v

    def random2(self, low: float, high: float) -> float:
        if low >= high:
            return float(low)
        diff = f32(high - low)
        v = high
        for _ in range(RETRIES):
            v = f32(self.random(diff) + low)
            if v != high:
                return v
        return v

    def gaussian(self) -> float:
        return f32(self.rnd.next_gaussian())
