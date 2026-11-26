// piece "atoll"
// why "A volcano slowly sinks while coral grows up to keep near the sunlight; the reef that fringed the island becomes a ring around a lagoon, then a ring around nothing."

float SEA;

void setup() {
  size(240, 128);
  noSmooth();
  SEA = height / 3.0;
}

void draw() {
  float t = ((frameCount + 900) % 3000) / 3000.0;
  float sink = t * height / 2;                   // how far the volcano has sunk
  background(255);
  noStroke();
  fill(230);
  rect(0, SEA, width, height - SEA);             // the sea
  pattern("wavy", 4);
  float peak = SEA - height / 5.0 + sink;
  fill(90);
  beginShape();                                  // the volcano's cone
  vertex(CX - width / 2, height);
  vertex(CX - width / 10, peak);
  vertex(CX + width / 10, peak);
  vertex(CX + width / 2, height);
  endShape(CLOSE);
  fill(180);
  for (int side = -1; side <= 1; side += 2) {    // the reef: grown up from where the old shore was, as fast as it sank
    float slope = (width * 2 / 5.0) / (height - SEA + height / 5.0);   // the cone's sideways reach per pixel down
    float shore0 = width / 10.0 + height / 5.0 * slope;   // where the sea met the cone at the start
    float x0 = CX + side * shore0;
    beginShape();
    vertex(x0 - 5, SEA + sink + 4);
    vertex(x0 - 4, SEA - 2);
    vertex(x0 + 4, SEA - 2);
    vertex(x0 + 5, SEA + sink + 4);
    endShape(CLOSE);
  }
  pattern("cross", 3);
  stroke(0);
  line(0, SEA, width, SEA);
}
