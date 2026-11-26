// piece "the town"
// why "One rule for the height of every block, and a skyline appears that nobody drew."

void setup() {
  size(240, 128, P3D);
  noSmooth();
}

void draw() {
  background(255);
  lights();
  noStroke();
  fill(200);
  translate(width / 2, height * 3 / 4, -height);        // looking in over the rooftops
  rotateX(PI / 6);
  rotateY(TWO_PI * loopT(2400));                          // walking slowly round the outside
  int n = 5;
  float s = height / 3;                                   // one block, one plot
  for (int i = 0; i < n; i++) {
    for (int j = 0; j < n; j++) {
      float h = s / 2 + s * cellRandom(i, j, 7);
      float x = (i - (n - 1) / 2.0) * s;
      float z = (j - (n - 1) / 2.0) * s;
      pushMatrix();
      translate(x, -h / 2, z);
      box(s / 2, h, s / 2);
      popMatrix();
    }
  }
}
