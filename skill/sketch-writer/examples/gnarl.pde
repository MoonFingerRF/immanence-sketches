// piece "gnarl"
// why "Every lonely cell lights up, and the knot it makes is never the same knot twice."

Life g = new Life("B1/S1", 5);

void setup() {
  size(240, 128);
  g.reseed = false;
  g.gap = 1;
  g.clear();
  for (int i = 22; i < 26; i++) g.set(i, 12, 1);      // a short bright thread
  g.set(40, 12, 1);
  g.set(30, 20, 1);
  for (int k = 0; k < 6; k++) g.step();
}

void draw() {
  background(255);
  if (frameCount % 5 == 0) g.step();
  fill(0);
  g.draw();
  stroke(0);
  noFill();
  rect(2, 2, 236, 124);
}
