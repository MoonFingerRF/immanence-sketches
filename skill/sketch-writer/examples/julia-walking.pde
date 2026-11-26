// piece "julia, walking"
// why "Every point of the plane either escapes or stays; c takes one step round its circle and the border redraws itself."

float CR = PI / 4;                               // c walks a circle of radius PI/4, just where the sets turn to lace
Fractal f = new Fractal("julia", CR, 0, 11 / 10.0);

void setup() {
  size(240, 128);
  background(255);
}

void draw() {
  int pass = height / 8;                         // frames a pass takes, 8 rows a frame
  float a = PI * 7 / 10 + TWO_PI * frameCount / (100.0 * pass);   // c walks slowly round its circle, 100 steps, one per pass
  f.set(CR * cos(a), CR * sin(a));
  f.draw(8, "bands", 1);
  stroke(0);
  float y = (frameCount * 8) % height;           // the pen's row
  line(0, y, 3, y);
  line(width - 4, y, width - 1, y);
}
