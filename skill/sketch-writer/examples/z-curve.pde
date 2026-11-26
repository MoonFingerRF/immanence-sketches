// piece "z curve"
// why "Every cell gets a number by weaving the bits of where it is; follow the numbers in order and the path writes Z inside Z inside Z."

int depth = 2;
int drawn = 1;
int hold = 0;

float cellX(int key, int d) {                    // unweave: even bits are x, odd bits y, the last bit picks a half
  int n = 0;
  for (int i = 0; i < d; i++) n |= (1 & (key >> (2 * i))) << i;
  n |= (1 & (key >> (2 * d))) << d;
  return n;
}

float cellY(int key, int d) {
  int m = 0;
  for (int i = 0; i < d; i++) m |= (1 & (key >> (2 * i + 1))) << i;
  return m;
}

void curve(int d, int upTo) {
  int w = 1 << d;
  float size = (height - 16.0) / w;
  float x0 = (width - 2 * w * size) / 2 + size / 2, y0 = (height - w * size) / 2 + size / 2;
  for (int k = 0; k < upTo && k < 2 * w * w - 1; k++) {
    line(x0 + size * cellX(k, d), y0 + size * cellY(k, d), x0 + size * cellX(k + 1, d), y0 + size * cellY(k + 1, d));
  }
}

void setup() {
  size(240, 128);
  noSmooth();
}

void draw() {
  background(255);
  int total = 2 * (1 << depth) * (1 << depth) - 1;
  stroke(150);                                   // the whole path, faint, before the pen reaches it
  strokeWeight(1);
  curve(depth, 1 << 20);
  stroke(0);
  strokeWeight(depth < 4 ? 2 : 1);
  curve(depth, drawn);
  if (drawn < total) {
    drawn += max(1, total / 24);                 // about two dozen frames to draw each level
  } else if (++hold > 16) {
    hold = 0;
    drawn = 1;
    depth = depth % 5 + 1;
    if (depth == 1) depth = 2;
  }
}
