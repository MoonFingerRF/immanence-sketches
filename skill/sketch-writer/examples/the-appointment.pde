// piece "the appointment"
// why "A date circled months ago, and the circle has still not stopped going round it."

void setup() {
  size(240, 128);
}

void draw() {
  background(255);
  stroke(0);
  strokeWeight(1);
  noFill();
  float w = width / 8, h = height / 7, x0 = width / 16, y0 = height / 6;
  for (int i = 0; i < 7; i++) {                            // the week across the top
    line(x0 + i * w + w / 2, y0 / 2, x0 + i * w + w / 2, y0 * 3 / 4);
  }
  for (int j = 0; j < 5; j++) {                            // five weeks of days
    for (int i = 0; i < 7; i++) rect(x0 + i * w, y0 + j * h, w, h);
  }
  float cx = x0 + w * 9 / 2, cy = y0 + h * 5 / 2;              // the one day that matters
  float loops = frameCount / 200.0;                        // one more time round every 200 frames
  int done = 1 + int(loops) % 5;                           // laps already drawn
  strokeWeight(2);
  beginShape();
  for (float a = 0; a < TWO_PI * (done + loops % 1); a += TWO_PI / 60) {
    float wob = 1 + a / TWO_PI / 12;                       // each lap a little wider than the last
    vertex(cx + cos(a - HALF_PI) * w * 2 / 3 * wob, cy + sin(a - HALF_PI) * h * 2 / 3 * wob);
  }
  endShape();
}
