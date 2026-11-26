// piece "nutation"
// why "A spinning top's axis leans and swings round, and as it does it nods; the tip of its axis draws waves, cusps or loops depending on how it was let go."

void setup() {
  size(240, 128);
  noSmooth();
}

float[] tip(float t, float B) {
  float th = PI / 3 + PI / 14 * cos(t * 7);      // the nodding
  float ph = t + B * sin(t * 7) / 7;             // the swinging round, sped and slowed by the nod
  float[] p = {sin(th) * cos(ph), sin(th) * sin(ph), cos(th)};
  return p;
}

void draw() {
  background(255);
  float B = 2 * wave(1600);                      // waves (B < 1), cusps (B = 1) or loops (B > 1)
  float R = height * 7 / 16, tilt = PI / 5;
  noStroke();
  fill(225);
  ellipse(CX, CY + R * sin(tilt) * cos(PI / 3), 2 * R * sin(PI / 3), 2 * R * sin(PI / 3) * sin(tilt));
  pattern("dots", 3);                            // the circle it swings about
  stroke(0);
  noFill();
  float now = TWO_PI * frameCount / 1000.0;
  beginShape();
  for (int k = 0; k < 300; k++) {                // the last turn of the tip's path, seen from a little above
    float[] p = tip(now - TWO_PI * k / 300, B);
    vertex(CX + R * p[0], CY + R * (p[1] * sin(tilt) - p[2] * cos(tilt)) + R * cos(tilt) / 2);
  }
  endShape();
  float[] p = tip(now, B);
  float ex = CX + R * p[0], ey = CY + R * (p[1] * sin(tilt) - p[2] * cos(tilt)) + R * cos(tilt) / 2;
  float bx = CX, by = CY + R * cos(tilt) / 2 + R / 4;
  strokeWeight(2);
  line(bx, by, ex, ey);                          // the axis, from where the top stands
  strokeWeight(1);
  fill(0);
  circle(ex, ey, 5);
  triangle(bx, by, bx - 6, by + 8, bx + 6, by + 8);
}
