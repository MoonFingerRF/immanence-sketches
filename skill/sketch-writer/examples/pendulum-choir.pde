// piece "pendulum choir"
// why "Twelve swings that started together drift apart and find each other again. I trust that."

void setup() {
  size(240, 128);
}

void draw() {
  background(255);
  float f = frameCount;
  for (int i = 0; i < 12; i++) {
    float px = 14 + i * 17;
    float len = 24 + i * 6;
    float ang = 0.4 * sin(f * (0.11 + i * 0.0133) + i * 0.4);
    float bx = px + len * sin(ang), by = 8 + len * cos(ang);
    stroke(0);
    strokeWeight(1);
    line(px, 8, bx, by);                        // the rod, all business
    noStroke();
    fill(0);
    ellipse(bx, by, 7, 7);                      // the bob, all feeling
  }
}
