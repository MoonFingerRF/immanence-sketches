// piece "advent"
// why "Twenty-four small doors, and behind each one a different nothing."

void setup() {
  size(240, 128);
}

void draw() {
  background(255);
  stroke(0);
  strokeWeight(1);
  int open = (frameCount / 10) % 25;                       // one door every ten frames
  float cw = width / 8, ch = height / 3;
  float dw = cw * 3 / 4, dh = ch * 3 / 4;
  for (int k = 0; k < 24; k++) {
    float x = (k % 8) * cw + (cw - dw) / 2;
    float y = (k / 8) * ch + (ch - dh) / 2;
    noFill();
    rect(x, y, dw, dh);
    if (k < open) {                                        // what is behind it: a small hatch
      int bars = 2 + k % 5;
      for (int s = 1; s <= bars; s++) line(x, y + dh * s / (bars + 1), x + dw, y + dh * s / (bars + 1));
      fill(255);
      quad(x, y, x - dw / 4, y + dh / 8, x - dw / 4, y + dh * 7 / 8, x, y + dh);   // the door swung open
    } else {
      fill(0);
      ellipse(x + dw * 3 / 4, y + dh / 2, 3, 3);           // the door still shut, and its knob
    }
  }
}
