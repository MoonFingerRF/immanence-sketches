// piece "window leaves"
// why "I wanted to see how window leaves would settle onto this small sheet."
void setup() { size(240, 128); noLoop(); }
void draw() {
  background(255);
  stroke(0); noFill();
  rect(19, 9, 202, 110); line(120, 9, 120, 119);
  line(19, 99, 221, 99);
  fill(0); rect(96, 91, 51, 24);
  float t = frameCount * 0.016;
  for (int i = 0; i < 7; i++) {
    float x = 120 + (i-3.0)*11;
    float y = 55 + 8*sin(t+i);
    line(120, 93, x, y);
    noFill(); ellipse(x+3, y, 13, 6);
  }
}
