// piece "quiet field"
// why "Nothing happened for an hour this afternoon, and the nothing had a texture."
void setup() {
  size(240, 128);
  noLoop();
}

void draw() {
  background(235, "stipple");
  noStroke();
  texture("hatch", 6);
  fill(110);
  rect(18, 18, 92, 92);
  texture("dots", 5);
  fill(90);
  ellipse(150, 64, 84, 84);
  texture("radial", 255);                  // black in the middle to paper at the edge
  fill(0);
  circle(200, 36, 36);
  noTexture();
}
