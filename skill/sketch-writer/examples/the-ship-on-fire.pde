// piece "the ship on fire"
// why "A hull made of the points that never escape, rocking a degree at a time on a sea of dots."

int MAXN = 30;

float shade(float x, float y) {
  float a = sin(TWO_PI * frameCount / 2000) / 20;       // the hull turning, very slowly
  float s = 5 / 2.0 / height;                            // the frame is two and a half units tall
  float dx = (x - CX) * s, dy = (y - CY) * s;
  float cr = -2 / 5.0 + dx * cos(a) - dy * sin(a);       // centred on the ship
  float ci = -3 / 5.0 + dx * sin(a) + dy * cos(a);
  float zr = 0, zi = 0, tr = 1e9;
  int n = 0;
  while (n < MAXN && zr * zr + zi * zi < 16) {
    float ar = abs(zr), ai = abs(zi);                    // the fold that sets it on fire
    float nr = ar * ar - ai * ai + cr;
    zi = 2 * ar * ai + ci;
    zr = nr;
    tr = min(tr, zr * zr + zi * zi);                     // how near the orbit came to the origin
    n++;
  }
  if (n >= MAXN) return 1 / 7.0 + 2 / 3.0 * constrain(sqrt(tr), 0, 1);
  return 3 / 5.0 * sqrt(n / float(MAXN));                // the sea: rings of escape time
}

void setup() {
  size(240, 128);
  noSmooth();
}

void draw() {
  background(255);
  patternShift(frameCount / 5.0, 0);
  shadeField(5);
  pattern("dots", 5);
}
