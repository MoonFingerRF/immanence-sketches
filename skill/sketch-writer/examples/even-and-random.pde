// piece "even and random"
// why "On the left, points placed by reversing the digits of the counting numbers; on the right, points thrown at random. The same count on each side, and only one side leaves holes."

int n = 1;

float reverse(int k, int base) {                 // the digits of k, mirrored behind the point
  float f = 1, r = 0;
  while (k > 0) {
    f /= base;
    r += f * (k % base);
    k /= base;
  }
  return r;
}

void setup() {
  size(240, 128);
  noSmooth();
  background(255);
}

void draw() {
  if (n > 1800) {                                // full: wash and begin again
    fade(80);
    if (frameCount % 40 == 0) n = 1;
    return;
  }
  noStroke();
  fill(0);
  float w = width / 2 - 4;
  for (int k = 0; k < 30; k++) {
    rect(reverse(n, 2) * w, reverse(n, 3) * height, 1, 1);          // Halton: bases two and three
    rect(width - w + cellRandom(n, 0, frameCount / 2000) * w, cellRandom(n, 1, frameCount / 2000) * height, 1, 1);
    n++;
  }
  fill(160);
  rect(width / 2 - 1, 0, 2, height);
  patternRect("flat", 4, width / 2 - 1, 0, 2, height);
}
