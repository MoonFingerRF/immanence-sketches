// piece "doppler"
// why "It goes past, and the sound is squeezed in front of it and stretched out behind."

void setup() {
  size(240, 128);
  noSmooth();
}

float sourceX(int t) { return t - width / 12; }         // one pixel a frame, from off the left edge

void draw() {
  background(255);
  int P = width + width / 6;                            // frames to cross the frame, and a little over
  int now = (frameCount + P / 3) % P;
  float cy = height / 2, c = 2;                         // sound runs twice as fast as it does
  noFill();
  strokeWeight(1);
  for (int k = 0; k < 24; k++) {                        // it calls out every eighth frame
    int te = (now / 8 - k) * 8;
    if (te < 0) break;
    float r = (now - te) * c;
    stroke(constrain(r, 0, 200));
    ellipse(sourceX(te), cy, r * 2, r * 2);
  }
  float sx = sourceX(now);
  stroke(0);
  fill(0);
  ellipse(sx, cy, 8, 8);
  line(sx, cy, sx + 12, cy);
  stroke(190);
  line(0, height - 6, width, height - 6);
}
