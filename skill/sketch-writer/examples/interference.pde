// piece "interference"
// why "Two sets of ripples that never agree, and the third pattern their disagreement makes."

void setup() {
  size(240, 128);
}

void draw() {
  float t = frameCount * 0.02;
  background(255);
  noFill();
  stroke(0);
  strokeWeight(1);
  float ax = 90 + 40 * sin(t * 0.7), ay = 64 + 20 * cos(t * 0.5);
  float bx = 150 + 40 * cos(t * 0.6), by = 64 + 20 * sin(t * 0.9);
  for (int r = 4; r < 180; r += 6) {
    ellipse(ax, ay, r * 2, r * 2);
    ellipse(bx, by, r * 2, r * 2);
  }
}
