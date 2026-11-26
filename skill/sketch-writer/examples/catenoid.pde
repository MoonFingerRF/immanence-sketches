// piece "catenoid"
// why "A soap film between two rings takes the shape of a hanging chain spun about the axis; pull the rings apart and its waist narrows until it can no longer hold."

void setup() {
  size(240, 128);
  noSmooth();
}

void draw() {
  background(255);
  float cx = width / 2, cy = height / 2, R = height * 2 / 5;   // the rings' radius
  float h = R * 13 / 20 * ease(wave(600));       // half the gap: just short of where the film must break
  float c = R;                                   // find the waist c with c cosh(h / c) = R (the wider solution)
  for (int k = 0; k < 40; k++) {
    float f = c * (exp(h / c) + exp(-h / c)) / 2 - R;
    c -= f / 3;
    c = max(c, R / 10);
  }
  noStroke();
  for (float x = -h; x <= h; x += 2) {           // the film, a column at a time, shaded by its tilt
    float r = c * (exp(x / c) + exp(-x / c)) / 2, slope = (exp(x / c) - exp(-x / c)) / 2;
    fill(200 - 90 * abs(slope) / (1 + abs(slope)));
    rect(cx + x, cy - r, 2, 2 * r);
  }
  pattern("hatch", 3);
  stroke(0);
  noFill();
  ellipse(cx - h, cy, R / 3, 2 * R);             // the two rings
  ellipse(cx + h, cy, R / 3, 2 * R);
  beginShape();
  for (float x = -h; x <= h; x += 2) vertex(cx + x, cy - c * (exp(x / c) + exp(-x / c)) / 2);
  endShape();
  beginShape();
  for (float x = -h; x <= h; x += 2) vertex(cx + x, cy + c * (exp(x / c) + exp(-x / c)) / 2);
  endShape();
}
