// piece "the wave"
// why "A line of water given a width, and it becomes a surface you can see the back of."

void setup() {
  size(240, 128, P3D);
  noSmooth();
}

void draw() {
  background(255);
  lights();
  noStroke();
  fill(215);
  translate(width / 2, height / 2, -height / 2);
  rotateX(PI / 3);
  int n = 24;
  float phase = TWO_PI * loopT(600);
  beginShape(TRIANGLE_STRIP);
  for (int i = 0; i <= n; i++) {
    float x = -width / 2 + width * i / n;
    float y = -height / 3 * sin(TWO_PI * i / 12 - phase);
    vertex(x, y, -height / 6);
    vertex(x, y, height / 6);
  }
  endShape();
}
