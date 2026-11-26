// piece "ridgelines"
// why "Sixteen horizons stacked like breaths, each one hiding the one behind it."

void setup() {
  size(240, 128);
}

void draw() {
  float t = frameCount * 0.04;
  background(255);
  for (int row = 0; row < 16; row++) {
    float base = 30 + row * 6;
    fill(255);                                   // each ridge is paper, so it hides the ones behind
    stroke(0);
    strokeWeight(1);
    beginShape();
    vertex(0, 128);
    for (int x = 0; x <= 240; x += 4) {
      float mid = 1 - abs(x - 120) / 120.0;      // tall in the middle, flat at the edges
      float h = noise(x * 0.03, row * 0.4, t) * 44 * mid * mid;
      vertex(x, base - h);
    }
    vertex(240, 128);
    endShape(CLOSE);
  }
}
