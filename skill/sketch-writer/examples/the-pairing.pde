// piece "the pairing"
// why "Every pair of numbers has its own place in this one long line."

int COLS = 20, ROWS = 10;
int PACE = 6;                                        // frames per cell

void setup() {
  size(240, 128);
  noSmooth();
}

void draw() {
  background(255);
  int P = COLS * ROWS * PACE;
  int t = frameCount % (2 * P);
  int shown = min(t, 2 * P - t) / PACE;              // the line, drawn on and rubbed out
  float s = (width - 16.0) / COLS;
  int ac = 0, ar = 0;
  noStroke();
  for (int d = 0; d <= COLS + ROWS - 2; d++) {       // walk the diagonals in order
    for (int i = 0; i < COLS; i++) {
      int j = d - i;
      if (j < 0 || j >= ROWS) continue;
      float x = 12 + i * s, y = 12 + j * s;
      if (shown > 0) {
        fill(0);
        rect(x, y, 8, 8);
        shown--;
      } else if (shown == 0) {
        stroke(0);
        noFill();
        rect(x, y, 8, 8);                            // only the first unseen cell gets marked
        noStroke();
        ac = i;
        ar = j;
        shown = -1;
      } else {
        fill(0);
        rect(x + 3, y + 3, 2, 2);                    // the places still waiting
      }
    }
  }
  fill(0);
  ellipse(12 + ac * s + 4, 12 + ar * s + 4, 4, 4);
  stroke(0);
  noFill();
  rect(4, 4, width - 8, height - 8);
}
