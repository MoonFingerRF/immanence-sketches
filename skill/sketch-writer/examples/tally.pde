// piece "tally"
// why "Counting something I can't name, five at a time, until the page is full and the count is forgotten."

int count = 0;

void page() {
  background(255);
  stroke(200);
  for (int y = 26; y < 128; y += 24) line(0, y, 240, y);   // a ruled page
}

void setup() {
  size(240, 128);
  noSmooth();
  page();
}

void draw() {
  randomSeed(count);                              // the same mark always wobbles the same way
  int group = count / 5;
  int k = count % 5;
  int perRow = 12;
  if (group >= perRow * 5) {                      // the page is full: a fresh page
    page();
    count = 0;
    return;
  }
  float gx = 10 + (group % perRow) * 19;
  float gy = 8 + (group / perRow) * 24;
  stroke(0);
  strokeWeight(1);
  if (k < 4) {
    float x = gx + k * 3 + random(-0.6, 0.6);
    line(x, gy + random(1.5), x + random(-1, 1), gy + 16 + random(-1.5, 0));
  } else {
    line(gx - 2, gy + 13, gx + 13, gy + 3);        // the fifth mark crosses the four
  }
  count++;
}
