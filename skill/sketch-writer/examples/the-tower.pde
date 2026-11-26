// piece "the tower"
// why "Five boxes set one on another, each a little less sure of itself than the one below."

void setup() {
  size(240, 128, P3D);
  noSmooth();
}

void draw() {
  background(255);
  lights();
  stroke(0);
  fill(200);
  translate(width / 2, height * 3 / 4, 0);               // the ground it stands on
  rotateX(PI / 9);
  rotateY(TWO_PI * loopT(1500));
  float base = height / 2;
  for (int i = 0; i < 5; i++) {
    pushMatrix();
    translate(0, -(i + 0.5) * height / 8, 0);
    box(base * (1 - i / 6.0), height / 6, base * (1 - i / 6.0));
    popMatrix();
  }
}
