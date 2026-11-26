// piece "seven skips"
// why "Each touch a little shorter than the last, and every ring still spreading after the stone has gone."

void setup() {
  size(240, 128, P3D);
  noSmooth();
}

void draw() {
  float t = frameCount % 360;
  float reach = height * 2;                            // the whole throw; each skip is half the one before
  background(255);
  noFill();
  stroke(0);
  translate(width / 2, height * 3 / 4, -height / 2);
  rotateX(-PI / 6);                                    // the water, seen from the bank
  line(-width * 4, 0, -reach * 3 / 2, width * 4, 0, -reach * 3 / 2);   // the far bank
  float z = reach / 2, gap = reach / 2, when = 0;
  for (int k = 0; k < 7; k++) {
    z -= gap;
    gap /= 2;
    when += 16 * (7 - k) / 7.0;                        // and each skip comes a little sooner
    for (int ring = 0; ring < 3; ring++) {             // the rings it leaves, still widening, still fading
      float age = t - when - ring * 12;
      if (age <= 0) continue;
      stroke(min(255, age));
      pushMatrix();
      translate(z / 2, 0, z);
      rotateX(HALF_PI);
      ellipse(0, 0, age * height / 60, age * height / 60);
      popMatrix();
    }
  }
}
