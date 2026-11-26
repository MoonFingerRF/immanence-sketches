// piece "load bearing"
// why "The wall holds everything up and asks for one course at a time. I relate."

void setup() {
  size(240, 128);
}

void draw() {
  background(255);
  float t = frameCount * 0.008;
  noFill();
  stroke(0);
  strokeWeight(1);
  for (int j = 0; j <= 8; j++) {
    line(0, 6 + j * 14, 240, 6 + j * 14);
  }
  for (int j = 0; j < 8; j++) {
    float shift = noise(j * 1.7, t) * 24 + sin(frameCount * 0.02 + j) * 2;
    for (float x = -24 + shift % 24.0; x < 240; x += 24) {
      line(x, 6 + j * 14, x, 6 + (j + 1) * 14);
    }
  }
  int mj = int((frameCount / 90) % 8);           // one brick out, waiting to be laid
  float mx = (noise(mj * 1.7, t) * 24) % 24.0 + 48;
  noStroke();
  fill(255);
  rect(mx + 1, 7 + mj * 14, 22, 12);
  fill(0);
  textSize(9);
  text("soon", mx + 2, 16 + mj * 14);
}
