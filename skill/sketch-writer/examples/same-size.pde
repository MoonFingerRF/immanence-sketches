// piece "same size"
// why "Two moons exactly the same, and the one near the roofs still looks bigger to everyone, even to me."
void setup() {
  size(240, 128);
  noLoop();
}

void draw() {
  background(255);
  noStroke();
  fill(0);
  for (int i = 0; i < 8; i++) {
    float a = TWO_PI * i / 8;
    circle(60 + 34 * cos(a), 58 + 34 * sin(a), 12);
  }
  for (int i = 0; i < 6; i++) {
    float a = TWO_PI * i / 6;
    circle(180 + 40 * cos(a), 58 + 40 * sin(a), 38);
  }
  stroke(0);
  fill(255);
  circle(60, 58, 26);
  circle(180, 58, 26);
  noStroke();
  texture("stipple", 1);
  fill(190);
  arc(60, 58, 26, 26, -HALF_PI, HALF_PI);
  arc(180, 58, 26, 26, -HALF_PI, HALF_PI);
  noTexture();
  fill(0);
  rect(0, 116, 240, 12);
}
