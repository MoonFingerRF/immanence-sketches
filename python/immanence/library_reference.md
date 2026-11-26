# The built-in library (BitMapMathLib, MIT)

These functions and classes are linked into every sketch: no import needed (a line
`import bmml.*;` is accepted and ignored, so the same source also runs in desktop Processing with
the library installed). Drawing uses the current `stroke()` and `fill()`. If you define a function
with the same name yourself, yours is used.

`p` below is how much of a curve is drawn, 0..1: `p = loopT(200)` draws it on over 200 frames.
Careful: at frame 1 that is almost 0, an empty frame, and the check fails. Use
`0.2 + 0.8 * loopT(200)`, or `1` to draw it whole.

## Numbers and places

| name | meaning |
|---|---|
| `PHI` `GOLDEN_ANGLE` `E` `SQRT2` | the golden ratio, the golden angle (radians), e, the square root of 2 |
| `CX`, `CY` | the centre of the canvas (120, 64) |
| `xAt(a, b)`, `yAt(a, b)` | `width * a / b`, `height * a / b` |
| `phylloX(i, cx, s)`, `phylloY(i, cy, s)` | seed `i` of a sunflower spiral around (cx, cy), `s` apart |

## Time and noise

| name | meaning |
|---|---|
| `ease(t)` | t in 0..1, eased in and out |
| `wave(period)` | 0 to 1 and back every `period` frames |
| `loopT(period)` | 0 to 1, repeating every `period` frames |
| `clockT(seconds)` | the same over seconds of the real clock |
| `timeNoise(x, y, speed)` | noise 0..1 drifting over time (speed 1 / 500 to 1 / 50) |
| `flowAngle(x, y, scale, speed)` | the angle of a drifting flow field at (x, y) |
| `cellRandom(i, j, seed)` | a fixed random number 0..1 for a grid cell |

## Plots

| call | draws |
|---|---|
| `plot(x0, x1, y0, y1)` | YOUR `float fy(float x)` across the frame (y0 at the bottom, y1 at the top) |
| `polarPlot(cx, cy, scale, turns)` | YOUR `float fr(float a)` as radius `fr(a) * scale` |
| `polarShape(cx, cy, scale)` | the same, closed and filled |
| `grid(step)` | a square grid |
| `polarGrid(cx, cy, rmax, rings, spokes)` | rings and spokes |

`plot` needs `float fy(float x) { ... }` in your sketch; `polarPlot` / `polarShape` need
`float fr(float a) { ... }`; `shadeField` needs `float shade(float x, float y) { ... }`.

## Curves

| call | draws |
|---|---|
| `lissajous(cx, cy, rx, ry, a, b, phase, p)` | a Lissajous figure |
| `rose(cx, cy, r, k, p)` | a rose curve with k petals' ratio |
| `spiro(cx, cy, R, r, d, p)` | a spirograph (hypotrochoid) |
| `spaceCurve(kind, x, y, w, h, order, p)` | kind: `"hilbert" "zorder" "peano" "dragon" "koch" "gosper"` |
| `LSystem t = new LSystem("F", "F=F[+F]F[-F]F", 25, 4);` | axiom, rules, turn in degrees, iterations |
| `t.draw(x, y, w, h, p)` | fitted into the box; F G draw, f moves, + - turn, [ ] branch |

## Point clouds (build up over frames)

```
Cloud c = new Cloud("fern");     // also "sierpinski" "carpet" "dragon" "lorenz" "clifford" "dejong"
c.step(n);                       // adds n points (500-3000 a frame)
c.draw(kind, scale);             // inks them in a dot pattern (kinds as for pattern())
c.set(a, b, c, d);               // the attractor's numbers;  c.fit(x, y, w, h);  c.fade(keep) 0..1
```

## Cells (drawn in the current fill)

```
Automaton a = new Automaton(30, 3);   // rule 0-255, cell size;  a.step(); a.draw(); a.set(i);
                                      // one new row per step: call a.step() 30 times in setup() so frame 1 shows rows
Life g = new Life("B3/S23", 3);       // g.step(); g.draw(); g.fill(density); g.set(i, j, 1); g.get(i, j); g.clear();
                                      // restarts by itself if everything dies
Fractal f = new Fractal("julia", -4 / 5.0, 1 / 6.0, 1);   // "julia": c = (a, b); "mandel": centre (a, b); zoom
f.draw(rows, kind, scale);            // the next `rows` rows (up to 12 a frame), shaded by escape time
f.set(a, b);                          // takes effect from the next pass. Never clear the canvas behind a Fractal
```

## Geometry

| call | draws |
|---|---|
| `voronoi(xs, ys, rings)` | the cells of the points (two `float[]`); rings > 1 shades them toward the centre |
| `delaunay(xs, ys)` | lines between neighbouring points |
| `Packing k = new Packing(x, y, w, h, rmin, rmax);` | circle packing: `k.step(tries); k.draw(); k.full; k.clear();` and `k.x[i] k.y[i] k.r[i]` for `i < k.n` |
| `truchet(x, y, w, h, cell, kind, t)` | Truchet tiles, kind `"arcs" "diag" "tri"`; tiles turn as t grows |

## Shading

| call | does |
|---|---|
| `pattern(kind, scale)` | turns every grey pixel drawn so far into the pattern, darker = denser |
| `patternRect(kind, scale, x, y, w, h)` | the same inside one box (faster) |
| `patternShift(dx, dy)` | moves the patterns |
| `shadeField(cell)` | fills the frame from YOUR `float shade(float x, float y)` (0..1 ink) |
| `fade(amount)` | washes toward white (30-120); use instead of `background()` for trails |

Pattern kinds (strings): `"flat" "stipple" "hatch" "cross" "lines" "vlines" "dots" "checker"
"weave" "wavy"`; scale 3-10.

## Words

`textCircle(s, cx, cy, r, start)`, `textWave(s, x, y, amp, phase)`, `textFit(s, x, y, w, h)`,
`textGrid(s, x, y, w, h, size)` (the word repeated over the box).

## Cost

`pattern`, `shadeField`, `c.draw` and `f.draw` touch every pixel: at most two of them a frame.

## Three small sketches

```
// a Julia set shaded by escape time
Fractal f = new Fractal("julia", -4 / 5.0, 1 / 6.0, 1);
void setup() { size(240, 128); background(255); }
void draw() { float a = TWO_PI * loopT(2000); f.set(-4 / 5.0 + cos(a) / 20, 1 / 6.0 + sin(a) / 20); f.draw(6, "dots", 4); }
```

```
// Voronoi cells hatched by distance
float[] xs = new float[16];
float[] ys = new float[16];
void setup() { size(240, 128); }
void draw() {
  background(255);
  for (int i = 0; i < 16; i++) { xs[i] = width * timeNoise(i, 0, 1 / 256.0); ys[i] = height * timeNoise(i, PHI, 1 / 256.0); }
  voronoi(xs, ys, 4);
  pattern("hatch", 5);
}
```

```
// a function plot
float fy(float x) { return sin(x) * sin(x / 3 + frameCount / 20.0); }
void setup() { size(240, 128); }
void draw() { background(255); stroke(190); grid(width / 15); stroke(0); plot(-3 * PI, 3 * PI, -3 / 2.0, 3 / 2.0); }
```
