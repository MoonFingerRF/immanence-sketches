// piece "point fractals"
// why "One small rule, run from nothing thousands of times; move one number a hair and the whole cloud rearranges its mind."

int NUM = 5000;
float[] xs = new float[NUM];
float[] ys = new float[NUM];

void setup() {
  size(240, 128);
  noSmooth();
}

void draw() {
  float t = 0.39 + 0.08 * sin(TWO_PI * (frameCount / 24) / 240);   // t creeps a notch every 24 frames, and holds
  float x = 0, y = 0;
  float minX = 1e9, minY = 1e9, maxX = -1e9, maxY = -1e9;
  int n = 0;
  for (; n < NUM; n++) {
    float tmpX = x;
    x = x * x - t * x + y * t - x;
    y = -y * y - t * t - tmpX * y - tmpX * t - y * t - y;
    if (abs(x) > 50 || abs(y) > 50) break;       // it ran away: keep what it drew before
    xs[n] = x; ys[n] = y;
    if (n > 50) {
      minX = min(minX, x); maxX = max(maxX, x);
      minY = min(minY, y); maxY = max(maxY, y);
    }
  }
  background(255);
  stroke(0);
  float w = 1.25 * max(maxX - minX, maxY - minY);
  float cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
  float view = 2 * height;                       // fitted to the frame's width, as the square was
  for (int i = 50; i < n; i++) {
    point(width / 2 + view * (xs[i] - cx) / w, height / 2 + view * (ys[i] - cy) / w * 9 / 10);
  }
}
