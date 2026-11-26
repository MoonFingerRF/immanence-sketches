// piece "star ledger"
// why "One star a night, entered in the book; the page fills and the sky is written down."

int cols = 12, rows = 5;
int n = 0;

void page() {
  background(255);
  stroke(238);
  grid(width / cols);                                      // ruled
  noStroke();
  fill(0);
  rect(0, 0, width, height / 32.0);                        // the ledger's heading
}

void setup() {
  size(240, 128);
  noSmooth();
  page();
}

void draw() {
  if (n >= cols * rows) { n = 0; page(); }                 // a full page, a fresh one
  int col = n % cols, row = n / cols;
  float x = xAt(2 * col + 3, 28), y = yAt(row + 1, rows + 1);
  stroke(0);
  strokeWeight(1);
  line(x - 3, y, x + 3, y);
  line(x, y - 3, x, y + 3);
  if (n > 0) {
    float px = xAt(2 * ((n - 1) % cols) + 3, 28), py = yAt((n - 1) / cols + 1, rows + 1);
    line(px, py, x, y);                                    // joined to the one before
  }
  n++;
}
