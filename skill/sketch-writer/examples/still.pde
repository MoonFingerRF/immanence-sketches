// piece "still"
// why "The word stood where I put it and let the sun move its shadow around all day."

void setup() {
  size(240, 128);
  noSmooth();
  textAlign(CENTER);
}

void draw() {
  float a = PI + PI * wave(240) * 4 / 5 + PI / 10;   // the sun going over
  float base = height * 3 / 4, len = height / 2 * (6 / 5.0 - abs(sin(a)));
  background(255);
  textSize(42);
  fill(160);
  for (int k = 1; k < len; k++) {                    // the shadow, squashed flat and stretched away from the sun
    push();
    translate(width / 2 - cos(a) * k, base + k / 10.0);
    scale(1, 2 / 5.0);
    text("still", 0, 0);
    pop();
  }
  fill(0);
  text("still", width / 2, base);
  stroke(0);
  line(0, base + 1, width, base + 1);
  noStroke();
  circle(width / 2 + width * 9 / 20 * cos(a), height / 2 + height * 2 / 5 * sin(a), 10);
}
