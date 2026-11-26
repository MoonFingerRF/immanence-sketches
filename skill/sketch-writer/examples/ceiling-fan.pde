// piece "ceiling fan"
// why "Lying on the floor in summer, watching the fan's blades and their shadows chase each other."

void blades(float a, float cx, float cy, int g) {
  fill(g);
  noStroke();
  for (int i = 0; i < 4; i++) {
    push();
    translate(cx, cy);
    rotate(a + HALF_PI * i);
    quad(8, -4, width / 4, -height / 16, width / 4, height / 16, 8, 4);
    pop();
  }
}

void setup() {
  size(240, 128);
  noSmooth();
}

void draw() {
  float a = TWO_PI * frameCount / 120;
  background(255);
  blades(a + PI / 32, width / 2 + 12, height / 2 + 6, 192);   // the shadow on the ceiling, a little behind
  blades(a, width / 2, height / 2, 0);
  stroke(0);
  fill(255);
  circle(width / 2, height / 2, height / 6);
  fill(0);
  circle(width / 2, height / 2, 8);
}
