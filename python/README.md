# immanence (Python)

The compiler, the reference interpreter and the `immanence` command of
[immanence-sketches](https://github.com/MoonFingerRF/immanence-sketches): compile Processing
sketches to bytecode for the ESP32 VM, run and check them on your computer, fetch pieces from
immanencepure.xyz. Pure Python 3.9+, no dependencies.

    pip install "git+https://github.com/MoonFingerRF/immanence-sketches#subdirectory=python"
    immanence check piece.pde --sheet preview.png
    immanence compile piece.pde
