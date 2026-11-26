// piece "for later"
// why "One taken from the bottom, one more laid on top; the pile has been this tall for years."

void setup() {
  size(240, 128, P3D);
  noSmooth();
}

void draw() {
  int keep = 9, period = 120;
  int top = frameCount / period;                       // how many have ever been put on it
  float settle = ease((frameCount % period) / 40.0);   // the newest drops, the pile sinks one
  background(255);
  lights();
  stroke(0);
  fill(255);
  translate(width / 2, height, -height / 2);
  rotateX(-PI / 10);
  float y = -height / 8 * settle;                      // the one read from the bottom slides away
  for (int k = top - keep; k <= top; k++) {
    float t = height / 14 * (1 + cellRandom(k, 0, 2));
    float drop = k == top ? (1 - settle) * height : 0;
    pushMatrix();
    translate(k == top - keep ? -width * settle / 2 : 0, y - t / 2 - drop, 0);
    rotateY((cellRandom(k, 1, 2) - 1 / 2.0) * PI / 5);  // none of them quite square with the others
    box(height * 3 / 4 * (1 + cellRandom(k, 2, 2)) / 2 + height / 4, t, height / 2);
    popMatrix();
    if (k > top - keep) y -= t;
  }
}
