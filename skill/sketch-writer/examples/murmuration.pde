// piece "murmuration"
// why "A thousand small decisions, and the shape they agree on keeps turning."

int N = 150;
float[] x = new float[N];
float[] y = new float[N];

void setup() {
  size(240, 128);
  noSmooth();
  for (int i = 0; i < N; i++) {
    float a = TWO_PI * i / N;
    x[i] = CX + width / 6.0 * cos(a);
    y[i] = CY + height / 4.0 * sin(a);
  }
}

void draw() {
  background(255);
  float t = TWO_PI * loopT(1500);
  float cx = CX + width / 10.0 * sin(t);                   // the centre they all circle
  float cy = CY + height / 8.0 * sin(2 * t + 1);
  noStroke();
  fill(0);
  for (int i = 0; i < N; i++) {
    float dx = x[i] - cx, dy = y[i] - cy;
    float r = max(2, sqrt(dx * dx + dy * dy));
    float ang = atan2(dy, dx) + TWO_PI / 1200 + 1 / (50 * r);          // the flock turns, tighter inside
    float target = height / 6.0 + height / 4.0 * timeNoise(i / PHI, 0, 1 / 200.0);   // each bird's own ring
    r += (target - r) / 50;
    x[i] = cx + r * cos(ang);
    y[i] = cy + r * sin(ang);
    rect(x[i], y[i], 2, 2);
  }
}
