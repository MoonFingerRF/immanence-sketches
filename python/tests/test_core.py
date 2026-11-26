"""Fast checks of the compiler, the reference interpreter and the C++ VM (when built).

    cd python && python3 -m pytest tests        (or: python3 -m unittest discover tests)
    IMMANENCE_VM=../host/build/immanence-vm ... also runs the native VM against the reference.
"""
import os
import subprocess
import sys
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))

import immanence  # noqa: E402
from immanence import PdeError, Unsupported, check, compile_pde, render  # noqa: E402

ANIMATED = """
void setup() { size(240, 128); }
void draw() {
  background(255);
  for (int i = 0; i < 12; i++) {
    float a = frameCount * 0.05 + i * TWO_PI / 12;
    ellipse(width / 2 + cos(a) * 50, height / 2 + sin(a) * 40, 14, 14);
  }
}
"""
STILL = """
size(240, 128);
background(255);
for (int x = 0; x < width; x += 8) line(x, 0, width - x, height);
"""
NOISE = """
void setup() { size(240, 128); noiseSeed(3); }
void draw() {
  loadPixels();
  for (int y = 0; y < height; y++)
    for (int x = 0; x < width; x++)
      pixels[y * width + x] = color(255 * noise(x * 0.03, y * 0.03, frameCount * 0.02));
  updatePixels();
}
"""
THREE = """
void setup() { size(240, 128, P3D); }
void draw() {
  background(255); lights();
  translate(width / 2, height / 2); rotateY(frameCount * 0.05); rotateX(0.4);
  box(50);
}
"""
LIBRARY = """
import bmml.*;
void setup() { size(240, 128); }
void draw() { background(255); fill(0); for (int i = 0; i < 80; i++) ellipse(phylloX(i, CX, 9), phylloY(i, CY, 9), 5 * wave(frameCount * 0.02 + i * 0.1) + 2, 5); }
"""
BLANK = "void setup() { size(240, 128); } void draw() { background(255); }"


class TestCompiler(unittest.TestCase):
    def test_header(self):
        b = compile_pde(ANIMATED)
        self.assertEqual(b[:4], b"PDEB")
        self.assertEqual(b[4], immanence.BYTECODE_VERSION)

    def test_errors_have_lines(self):
        with self.assertRaises(PdeError) as e:
            compile_pde("void setup() {\n  size(240, 128);\n  elipse(1, 2, 3, 4);\n}\n")
        self.assertEqual(e.exception.line, 3)

    def test_unsupported(self):
        with self.assertRaises(Unsupported):
            compile_pde("ArrayList<Integer> a = new ArrayList<Integer>();\nvoid setup() { a.add(1); }")

    def test_spec_header_is_current(self):
        from immanence.pde.bcspec import c_header
        with open(os.path.join(ROOT, "src", "pde_spec.h"), encoding="utf-8") as f:
            self.assertEqual(f.read().rstrip("\n"), c_header().rstrip("\n"))


class TestCheck(unittest.TestCase):
    def test_pass(self):
        for src in (ANIMATED, STILL, NOISE, THREE, LIBRARY):
            r = check(src, frames=6)
            self.assertTrue(r["ok"], r)

    def test_still_and_animated(self):
        self.assertTrue(check(STILL, frames=6)["still"])
        self.assertTrue(check(ANIMATED, frames=6)["animated"])

    def test_blank_fails(self):
        r = check(BLANK, frames=3)
        self.assertFalse(r["ok"])
        self.assertEqual(r["stage"], "picture")


@unittest.skipUnless(os.environ.get("IMMANENCE_VM"), "set IMMANENCE_VM to the built host runner")
class TestNativeVm(unittest.TestCase):
    def test_bit_exact(self):
        vm = os.environ["IMMANENCE_VM"]
        for src in (ANIMATED, STILL, NOISE, THREE, LIBRARY):
            ref = ["%08x" % zlib.crc32(g) for g in render(immanence.for_compiler(src), 20)["frames"]]
            nat = immanence.run_native(vm, compile_pde(src), 20)
            self.assertIsNone(nat["error"])
            self.assertEqual(ref, ["%08x" % zlib.crc32(g) for g in nat["frames"]])


class TestJsCompiler(unittest.TestCase):
    @unittest.skipUnless(subprocess.run(["which", "node"], capture_output=True).returncode == 0, "no node")
    def test_same_bytes(self):
        import json
        js = os.path.join(ROOT, "js", "compile.mjs")
        code = ("import { compile } from %s; let s=''; process.stdin.on('data',d=>s+=d);"
                "process.stdin.on('end',()=>{const r=compile(s); process.stdout.write(r.ok?Buffer.from(r.bytes).toString('hex'):'ERR');});"
                % json.dumps("file://" + js))
        for src in (ANIMATED, STILL, NOISE, THREE, LIBRARY):
            out = subprocess.run(["node", "--input-type=module", "-e", code], input=src, capture_output=True,
                                 text=True).stdout
            self.assertEqual(out, compile_pde(src).hex())


if __name__ == "__main__":
    unittest.main()
