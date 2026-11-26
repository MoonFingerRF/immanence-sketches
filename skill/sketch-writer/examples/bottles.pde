// piece "bottles"
// why "Three empty bottles on a sill, each holding a different amount of the afternoon."
void bottle(float x, float w, float h, float neck) {
  beginShape();
  vertex(x - w / 2, 110);
  vertex(x - w / 2, 110 - h);
  bezierVertex(x - w / 2, 110 - h - 10, x - neck, 110 - h - 10, x - neck, 110 - h - 18);
  vertex(x - neck, 110 - h - 30);
  vertex(x + neck, 110 - h - 30);
  vertex(x + neck, 110 - h - 18);
  bezierVertex(x + neck, 110 - h - 10, x + w / 2, 110 - h - 10, x + w / 2, 110 - h);
  vertex(x + w / 2, 110);
  endShape(CLOSE);
}

void setup() {
  size(240, 128);
  noLoop();
}

void draw() {
  background(255);
  noStroke();
  texture("vlines", 4);
  fill(190);
  rect(0, 0, 240, 110);
  noTexture();
  fill(255);
  rect(20, 8, 90, 90);
  stroke(0);
  strokeWeight(2);
  line(65, 8, 65, 98);
  line(20, 53, 110, 53);
  strokeWeight(1);
  fill(0);
  rect(0, 110, 240, 18);
  texture("halftone", 4);
  fill(120);
  bottle(120, 30, 40, 5);
  texture("hatch", 3);
  fill(150);
  bottle(160, 22, 54, 4);
  texture("stipple", 1);
  fill(60);
  bottle(196, 34, 30, 7);
  noTexture();
}
