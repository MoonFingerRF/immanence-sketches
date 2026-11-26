"""noise(), noiseDetail(), noiseSeed(): Processing's Perlin noise, bit for bit in Java float.

SPDX-License-Identifier: LGPL-2.1-only
This file follows Processing's PApplet.noise (processing/core, Processing 4.5.2, GNU LGPL 2.1),
so that pieces using noise() draw exactly as they do in Processing. See THIRD-PARTY.md. The C++
VM's copy is src/pde_noise.cpp. Randomness (jrandom.py) mixes this class in.
"""
from __future__ import annotations

import math
import struct

_F = struct.Struct("<f")


def _f32(x: float) -> float:
    try:
        return _F.unpack(_F.pack(x))[0]
    except OverflowError:
        return math.copysign(math.inf, x)


f32 = _f32


def noise_tables() -> tuple[list[float], list[float]]:
    """Processing's half-degree cosine table (720 entries, float) and noise_fsc of each entry."""
    deg = f32(math.pi / 180.0)
    cos = [f32(math.cos(f32(f32(i * deg) * 0.5))) for i in range(720)]
    fsc = [f32(0.5 * f32(1.0 - c)) for c in cos]     # PApplet.noise_fsc per index
    return cos, fsc


class ProcessingNoise:
    """The noise half of the sketch's Randomness: needs self.perlin_rnd (a JavaRandom),
    self.perlin, self.octaves, self.falloff and self.fsc, set up by Randomness.__init__."""

    PERLIN_YWRAPB, PERLIN_ZWRAPB, PERLIN_SIZE = 4, 8, 4095

    def noise_seed(self, s: int) -> None:
        self.perlin_rnd.set_seed(int(s))
        self.perlin = None

    def noise_detail(self, lod: int, falloff: float | None = None) -> None:
        if lod > 0:
            self.octaves = min(int(lod), 16)
        if falloff is not None and falloff > 0:
            self.falloff = f32(falloff)


    def noise(self, x: float, y: float = 0.0, z: float = 0.0) -> float:
        """PApplet.noise in Java float: every operation rounds to 32 bits, exactly as the C++ VM
        (src/pde_noise.cpp) computes it."""
        p = self.perlin
        if p is None:
            p = self.perlin = [self.perlin_rnd.next_float() for _ in range(4096)]
        u, k = _F.unpack, _F.pack
        try:
            x, y, z = u(k(x))[0], u(k(y))[0], u(k(z))[0]
        except OverflowError:
            x, y, z = f32(x), f32(y), f32(z)
        if x != x or y != y or z != z:
            return 0.0
        x, y, z = abs(x), abs(y), abs(z)
        # (int) saturates; (float)xi is exact below 2^31 (a float's integer part is a float)
        xi = int(x) if x < 2147483648.0 else 2147483647
        yi = int(y) if y < 2147483648.0 else 2147483647
        zi = int(z) if z < 2147483648.0 else 2147483647
        xf = x - xi if x < 2147483648.0 else f32(x - 2147483648.0)
        yf = y - yi if y < 2147483648.0 else f32(y - 2147483648.0)
        zf = z - zi if z < 2147483648.0 else f32(z - 2147483648.0)
        r = 0.0
        ampl = 0.5
        fsc = self.fsc
        falloff = self.falloff
        for _ in range(self.octaves):
            of = xi + (yi << 4) + (zi << 8)
            # noise_fsc: 0.5f * (1.0f - cos[(int)(v * 360f) % 720]) -- a table of the float results
            rxf = fsc[int(u(k(xf * 360))[0]) % 720]
            ryf = fsc[int(u(k(yf * 360))[0]) % 720]
            n1 = p[of & 4095]
            n1 = u(k(n1 + u(k(rxf * u(k(p[(of + 1) & 4095] - n1))[0]))[0]))[0]
            n2 = p[(of + 16) & 4095]
            n2 = u(k(n2 + u(k(rxf * u(k(p[(of + 17) & 4095] - n2))[0]))[0]))[0]
            n1 = u(k(n1 + u(k(ryf * u(k(n2 - n1))[0]))[0]))[0]
            of += 256
            n2 = p[of & 4095]
            n2 = u(k(n2 + u(k(rxf * u(k(p[(of + 1) & 4095] - n2))[0]))[0]))[0]
            n3 = p[(of + 16) & 4095]
            n3 = u(k(n3 + u(k(rxf * u(k(p[(of + 17) & 4095] - n3))[0]))[0]))[0]
            n2 = u(k(n2 + u(k(ryf * u(k(n3 - n2))[0]))[0]))[0]
            rzf = fsc[int(u(k(zf * 360))[0]) % 720]
            n1 = u(k(n1 + u(k(rzf * u(k(n2 - n1))[0]))[0]))[0]
            r = f32(r + f32(n1 * ampl))          # (f32: a huge noiseDetail falloff overflows to inf)
            ampl = f32(ampl * falloff)
            xi <<= 1
            xf *= 2
            yi <<= 1
            yf *= 2
            zi <<= 1
            zf *= 2
            if xf >= 1.0:
                xi += 1
                xf -= 1
            if yf >= 1.0:
                yi += 1
                yf -= 1
            if zf >= 1.0:
                zi += 1
                zf -= 1
            xi &= 0xFFFFFFFF
            yi &= 0xFFFFFFFF
            zi &= 0xFFFFFFFF
        return r
