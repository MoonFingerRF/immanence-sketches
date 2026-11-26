// piece "three bodies"
// why "Three points that only know about each other. The lines are how they feel the distance."

int TRAIL = 60;
float[][] px = new float[3][TRAIL];
float[][] py = new float[3][TRAIL];

void setup() {
  size(240, 128);
}

void draw() {
  float t = frameCount * 0.05;
  background(255);
  int k = frameCount % TRAIL;
  for (int b = 0; b < 3; b++) {
    px[b][k] = 120 + 95 * sin(t * (1 + b * 0.31) + b * 2);
    py[b][k] = 64 + 52 * sin(t * (1.3 + b * 0.17) + b);
  }
  stroke(0);
  strokeWeight(1);
  int have = min(frameCount, TRAIL);
  for (int b = 0; b < 3; b++) {                  // each body's recent path
    for (int s = 1; s < have; s++) {
      int i = (k - s + TRAIL) % TRAIL, j = (k - s + 1 + TRAIL) % TRAIL;
      line(px[b][i], py[b][i], px[b][j], py[b][j]);
    }
  }
  stroke(0);
  for (int b = 0; b < 3; b++) {                  // the pull between them, dashed
    int c = (b + 1) % 3;
    for (float f = 0; f < 1; f += 0.08) {
      point(lerp(px[b][k], px[c][k], f), lerp(py[b][k], py[c][k], f));
    }
  }
  noStroke();
  fill(0);
  for (int b = 0; b < 3; b++) ellipse(px[b][k], py[b][k], 8, 8);
}
