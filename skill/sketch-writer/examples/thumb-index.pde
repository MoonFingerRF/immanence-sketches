// piece "thumb index"
// why "Down the cut edge of the book, one tab stands out for wherever I have got to."

void setup() {
  size(240, 128);
  noSmooth();
  textAlign(CENTER, CENTER);
}

int TABS = 7;

void draw() {
  background(255);
  noStroke();
  fill(160);
  for (int k = 0; k < 40; k++) {                          // the page edges
    rect(24 + k * 3, 12, 2, 104);
  }
  float at = loopT(420) * TABS;                           // which letter is being looked for
  for (int k = 0; k < TABS; k++) {
    float d = abs(k - at);                                // this tab's turn to stand out
    float out = 1 - constrain(d, 0, 1);
    float y = 16 + k * 14;
    fill(0);
    rect(140, y, 20 + 16 * out, 12);                      // the tab itself
    fill(255);
    textSize(9);
    text(char(65 + k * 3), 150 + 8 * out, y + 6);          // its letter
  }
  fill(130);
  for (int r = 0; r < 6; r++) {
    rect(28, 20 + r * 16, 90 + 20 * cellRandom(r, 2, 4), 4);   // a page of the book, open beside it
  }
  pattern("stipple", 4);
}
