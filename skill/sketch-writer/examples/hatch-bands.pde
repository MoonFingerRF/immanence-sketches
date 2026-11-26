// piece "hatch bands"
// why "I wanted to see how hatch bands would settle onto this small sheet."
void setup() { size(240, 128); noLoop(); }
void draw() {
  background(255);
  noStroke(); texture("hatch", 7); fill(75);
  float t = frameCount * 0.014;
  for (int i = 0; i < 10; i++) {
    float y = 8 + i * 14;
    float w = 70 + 35 * sin(t + i * 0.7);
    rect(12, y, w, 7);
    rect(228-w, y+4, w, 5);
  }
  noTexture();
}
