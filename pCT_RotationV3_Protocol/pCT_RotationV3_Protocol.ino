// ========================================================
// pCT Rotation V3 — Protocol version (ต่อยอดจาก pCT_RotationV2_Plan2_ManualFire.ino ซึ่งเก็บไว้เป็นต้นแบบ ไม่ถูกแก้)
//
// ฮาร์ดแวร์/การเคลื่อนที่/ค่าคงที่ทั้งหมดเหมือนต้นแบบ ต่างกันที่ "วิธีคุยกับโปรแกรม GUI":
//   GUI อ่านเฉพาะบรรทัดที่ขึ้นต้นด้วย '#' (รูปแบบคงที่ ดูด้านล่าง) ส่วนบรรทัดอื่นเป็นข้อความให้คนอ่าน
//   เปลี่ยนข้อความให้คนอ่านได้อิสระ GUI ไม่พัง ขอแค่คงบรรทัด '#' ไว้
//
// ---------- Protocol (proto=1) ----------
//   เฟิร์มแวร์ -> GUI (ทุกบรรทัดจบด้วย \n)
//     #HELLO proto=1 fw=pCT_RotationV3            ตอนบูต
//     #STATE A=<deg> B=<deg> INIT=<0|1> BUSY=<0|1> DWELL=<ms>
//                                                   ส่งตอนบูต, ก่อนเริ่มงานที่ใช้เวลา (BUSY=1), หลังจบงาน (BUSY=0),
//                                                   และทุก step ของ SCAN
//     #DONE <cmd>                                   คำสั่งทำสำเร็จ (GUI ปลดล็อกปุ่ม)
//     #ERR <reason>                                 คำสั่งถูกปฏิเสธ (not_init / bad_value / unknown_cmd ...)
//   GUI -> เฟิร์มแวร์ (ข้อความบรรทัดเดียว จบด้วย \n)
//     start | close | <มุม> | A<มุม> | B<มุม> | SETA<มุม> | SETB<มุม> | FIRE | SCAN<องศา/step> | X | D<ms> | K | ?
//     '?' = ขอ #STATE ปัจจุบัน
//
// ---------- ต่างจากต้นแบบ (จงใจแก้) ----------
//   1) ค่าที่ไม่ใช่ตัวเลข ตอบ #ERR bad_value แทนที่จะอ่านเป็น 0 แล้วหมุนไป 0°
//   2) INIT=1 ต้องรู้ตำแหน่งครบทั้ง A และ B (start หรือ SETA+SETB ครบคู่) ไม่ใช่ตัวเดียวก็ปลดล็อก
//   3) ระหว่าง SCAN รับทั้ง 'X' (ยกเลิก) และ 'K' (kill) ไม่ทิ้ง K เหมือนต้นแบบ
//   ที่เหลือเหมือนต้นแบบ รวมถึงข้อจำกัดว่าระหว่างหมุน/ยิง (delay) เฟิร์มแวร์ไม่อ่าน Serial
//   ⚠️ ยังไม่ได้ทดสอบกับฮาร์ดแวร์จริง ข้อควรระวังเรื่อง ALPIDE/TSS ในต้นแบบยังใช้ตามเดิมทั้งหมด
// ========================================================

#include <math.h>
#include <stdlib.h>

const int PROTO_VERSION = 1;

// ฝั่ง A (Carriage A)
const int stepPin1 = 2;
const int dirPin1 = 3;
const int stepPin2 = 4;
const int dirPin2 = 5;

// ฝั่ง B (Carriage B)
const int stepPin3 = 6;
const int dirPin3 = 7;
const int stepPin4 = 8;
const int dirPin4 = 9;

// TSS relay (dual-channel safety switch — ต้องปิดทั้งคู่ให้ครบวงจร)
const int tssK1 = 22;
const int tssK2 = 23;

// LEMO ALPIDE — J2/D11 ใช้งานจริงตอนนี้
const int lemoJ2 = 11;

// ---------- สเปกมอเตอร์+เกียร์+พูลเลย์+วงแหวน ----------
long pulseSetting = 3200;
float gearRatio = 4.25;
long totalPulsesForOneGearRev = pulseSetting * gearRatio;

float trackDiameter = 674.8;
float gearDiameter = 48.51;
float gearRevsNeeded = trackDiameter / gearDiameter;
float totalStepsFor360 = gearRevsNeeded * totalPulsesForOneGearRev;
float stepsPerDegree = totalStepsFor360 / 360.0;   // ≈ 530.14

// ---------- Acceleration Ramp ----------
int minSpeedDelay = 500;   // µs — ความเร็วช่วงวิ่งคงที่
int maxSpeedDelay = 1200;  // µs — ความเร็วช่วงเริ่ม/จบ
long rampSteps = 300;

// ---------- ค่าเริ่มต้น (แก้ให้ตรงตำแหน่งจริงก่อนใช้งาน) ----------
const float initAngleA = 37.1;
const float initAngleB = -35.0;

float currentAngle1 = initAngleA;
float currentAngle2 = initAngleB;

// รู้ตำแหน่งจริงของแต่ละกล่องแล้วหรือยัง (INIT=1 เมื่อรู้ครบทั้งคู่)
bool knownA = false;
bool knownB = false;

unsigned long alpideDwellMs = 90;

bool isInitialized() { return knownA && knownB; }

// ========================================================
// Protocol helpers — บรรทัดที่ขึ้นต้น '#' ห้ามเปลี่ยนรูปแบบ (GUI อ่านจากตรงนี้)
// ========================================================
void printState(bool busy) {
  Serial.print(F("#STATE A=")); Serial.print(currentAngle1, 2);
  Serial.print(F(" B="));       Serial.print(currentAngle2, 2);
  Serial.print(F(" INIT="));    Serial.print(isInitialized() ? 1 : 0);
  Serial.print(F(" BUSY="));    Serial.print(busy ? 1 : 0);
  Serial.print(F(" DWELL="));   Serial.println(alpideDwellMs);
}

void replyDone(const __FlashStringHelper* cmd) {
  Serial.print(F("#DONE ")); Serial.println(cmd);
}

void replyErr(const __FlashStringHelper* reason) {
  Serial.print(F("#ERR ")); Serial.println(reason);
}

// แปลงข้อความเป็นตัวเลขแบบเข้มงวด — คืน false ถ้าไม่ใช่ตัวเลขล้วน (ต้นแบบใช้ toFloat ที่อ่านขยะเป็น 0)
bool parseNumber(const String& s, float& out) {
  if (s.length() == 0) return false;
  char* end;
  out = (float)strtod(s.c_str(), &end);
  return end != s.c_str() && *end == '\0';
}

void setup() {
  Serial.begin(115200);

  pinMode(stepPin1, OUTPUT); pinMode(dirPin1, OUTPUT);
  pinMode(stepPin2, OUTPUT); pinMode(dirPin2, OUTPUT);
  pinMode(stepPin3, OUTPUT); pinMode(dirPin3, OUTPUT);
  pinMode(stepPin4, OUTPUT); pinMode(dirPin4, OUTPUT);

  pinMode(tssK1, OUTPUT);
  pinMode(tssK2, OUTPUT);
  pinMode(lemoJ2, OUTPUT);

  // fail-safe: ทุกอย่างเริ่มที่ปิดเสมอตอนเปิดเครื่อง/รีเซ็ต
  digitalWrite(tssK1, LOW);
  digitalWrite(tssK2, LOW);
  digitalWrite(lemoJ2, LOW);

  Serial.print(F("#HELLO proto=")); Serial.print(PROTO_VERSION);
  Serial.println(F(" fw=pCT_RotationV3"));
  Serial.println(F("pCT V3 ready. commands: start close <deg> A<deg> B<deg> SETA<deg> SETB<deg> FIRE SCAN<deg> X D<ms> K ?"));
  printState(false);
}

void loop() {
  if (Serial.available() > 0) {
    String input = Serial.readStringUntil('\n');
    input.trim();
    if (input.length() == 0) return;
    handleCommand(input);
  }
}

// ========================================================
// ตัวแจกคำสั่ง — เพิ่ม/แก้คำสั่งใหม่ที่นี่ แล้วอย่าลืมจบด้วย replyDone() หรือ replyErr() เสมอ
// (GUI รอบรรทัด #DONE / #ERR เพื่อปลดล็อกปุ่ม) และเรียก printState() ถ้ามีการเปลี่ยนตำแหน่ง/สถานะ
// ========================================================
void handleCommand(String input) {
  String up = input;
  up.toUpperCase();
  float v;

  if (up == "K") {
    killAll();
    printState(false);
    replyDone(F("K"));
  }
  else if (up == "X") {
    Serial.println(F("X is used only to cancel SCAN; no SCAN is running"));
    replyDone(F("X"));
  }
  else if (up == "?") {
    printState(false);
    replyDone(F("?"));
  }
  else if (up == "FIRE") {
    if (!isInitialized()) { replyErr(F("not_init")); return; }
    Serial.println(F("FIRE: ALPIDE+Beam at current position"));
    printState(true);
    runBeamSequence();
    printState(false);
    replyDone(F("FIRE"));
  }
  else if (up == "START") {
    Serial.println(F("moving to symmetric point (A=90, B=-90), no fire"));
    printState(true);
    moveBothCarriages(90.0, -90.0);
    knownA = true; knownB = true;
    printState(false);
    replyDone(F("start"));
  }
  else if (up == "CLOSE") {
    Serial.println(F("moving to home, no fire"));
    printState(true);
    moveBothCarriages(initAngleA, initAngleB);
    knownA = false; knownB = false;
    printState(false);
    replyDone(F("close"));
  }
  else if (up.charAt(0) == 'D') {
    if (parseNumber(input.substring(1), v) && v > 0) {
      alpideDwellMs = (unsigned long)v;
      printState(false);
      replyDone(F("D"));
    } else {
      replyErr(F("bad_value"));
    }
  }
  else if (up.startsWith("SETA")) {
    if (!parseNumber(input.substring(4), v)) { replyErr(F("bad_value")); return; }
    currentAngle1 = v; knownA = true;
    printState(false);
    replyDone(F("SETA"));
  }
  else if (up.startsWith("SETB")) {
    if (!parseNumber(input.substring(4), v)) { replyErr(F("bad_value")); return; }
    currentAngle2 = v; knownB = true;
    printState(false);
    replyDone(F("SETB"));
  }
  else if (up.startsWith("SCAN")) {
    if (!parseNumber(input.substring(4), v) || v == 0) { replyErr(F("bad_value")); return; }
    if (!isInitialized()) { replyErr(F("not_init")); return; }
    bool completed = runScanSequence(v);
    printState(false);
    if (completed) replyDone(F("SCAN")); else replyErr(F("scan_aborted"));
  }
  else if (up.charAt(0) == 'A' && up.length() > 1) {
    if (!parseNumber(input.substring(1), v)) { replyErr(F("bad_value")); return; }
    Serial.println(F("calibrate: moving box A only, no fire"));
    printState(true);
    moveSingleBoxA(v);
    printState(false);
    replyDone(F("A"));
  }
  else if (up.charAt(0) == 'B' && up.length() > 1) {
    if (!parseNumber(input.substring(1), v)) { replyErr(F("bad_value")); return; }
    Serial.println(F("calibrate: moving box B only, no fire"));
    printState(true);
    moveSingleBoxB(v);
    printState(false);
    replyDone(F("B"));
  }
  else if (parseNumber(input, v)) {
    if (!isInitialized()) { replyErr(F("not_init")); return; }
    Serial.println(F("sync move (B = A - 180), no fire"));
    printState(true);
    moveBothCarriages(v, v - 180.0);
    printState(false);
    replyDone(F("MOVE"));
  }
  else {
    replyErr(F("unknown_cmd"));
  }
}

// ========================================================
// ยิง ALPIDE + Beam: เปิด ALPIDE -> เปิด Beam (K1+K2) -> ค้าง dwell -> ปิดพร้อมกัน
// blocking (delay) ระหว่างนี้ไม่อ่าน Serial
// ========================================================
void runBeamSequence() {
  Serial.println(F("  ALPIDE ON"));
  digitalWrite(lemoJ2, HIGH);

  Serial.println(F("  Beam ON (K1+K2)"));
  digitalWrite(tssK1, HIGH);
  digitalWrite(tssK2, HIGH);

  delay(alpideDwellMs);

  digitalWrite(tssK1, LOW);
  digitalWrite(tssK2, LOW);
  digitalWrite(lemoJ2, LOW);
  Serial.println(F("  Beam+ALPIDE OFF"));
}

// ========================================================
// สแกนครบ 360° ทีละ degStep องศา ยิงทุก step — ใช้เฉพาะตอนอยู่นอกโซนอันตราย
// เช็ค 'X' (ยกเลิก) และ 'K' (kill) ระหว่าง step เท่านั้น คืน true ถ้าครบ, false ถ้าถูกยกเลิก
// ========================================================
bool runScanSequence(float degStep) {
  int totalSteps = round(360.0 / fabs(degStep));
  Serial.print(F("SCAN start: ")); Serial.print(degStep);
  Serial.print(F(" deg/step, ")); Serial.print(totalSteps); Serial.println(F(" steps (X = cancel, K = kill)"));

  for (int i = 0; i < totalSteps; i++) {
    if (Serial.available() > 0) {
      String cmd = Serial.readStringUntil('\n');
      cmd.trim();
      if (cmd.equalsIgnoreCase("X")) {
        Serial.println(F("SCAN cancelled (stopped between steps)"));
        return false;
      }
      if (cmd.equalsIgnoreCase("K")) {
        killAll();
        Serial.println(F("SCAN aborted by K"));
        return false;
      }
    }

    Serial.print(F("SCAN step ")); Serial.print(i + 1); Serial.print(F("/")); Serial.println(totalSteps);
    printState(true);
    runBeamSequence();

    float target1 = currentAngle1 + degStep;
    moveBothCarriages(target1, target1 - 180.0);
  }

  Serial.println(F("SCAN complete"));
  return true;
}

// Kill — ปิด LEMO/K1/K2 ทั้งหมด
void killAll() {
  digitalWrite(tssK1, LOW);
  digitalWrite(tssK2, LOW);
  digitalWrite(lemoJ2, LOW);
  Serial.println(F("KILL: LEMO/K1/K2 all off"));
}

int rampDelayFor(long x, long totalSteps) {
  if (totalSteps <= rampSteps * 2) {
    long half = totalSteps / 2;
    if (half == 0) return minSpeedDelay;
    if (x < half) {
      float frac = (float)x / half;
      return maxSpeedDelay - frac * (maxSpeedDelay - minSpeedDelay);
    } else {
      float frac = (float)(totalSteps - x) / (totalSteps - half);
      return maxSpeedDelay - frac * (maxSpeedDelay - minSpeedDelay);
    }
  }
  if (x < rampSteps) {
    float frac = (float)x / rampSteps;
    return maxSpeedDelay - frac * (maxSpeedDelay - minSpeedDelay);
  } else if (x > totalSteps - rampSteps) {
    float frac = (float)(totalSteps - x) / rampSteps;
    return maxSpeedDelay - frac * (maxSpeedDelay - minSpeedDelay);
  } else {
    return minSpeedDelay;
  }
}

// กล่อง A — สมมติฐานเดิม (DIR สวนกัน) ยังไม่เคยยืนยันด้วยวิธีแม่นยำ
void setDirBoxA(float diff) {
  if (diff > 0) {
    digitalWrite(dirPin1, LOW); digitalWrite(dirPin2, HIGH);
  } else {
    digitalWrite(dirPin1, HIGH); digitalWrite(dirPin2, LOW);
  }
}

// กล่อง B — ยืนยันจากการทดสอบจริงด้วย B<มุม> เดี่ยว (2026-09-04)
void setDirBoxB(float diff) {
  if (diff > 0) {
    digitalWrite(dirPin3, LOW); digitalWrite(dirPin4, HIGH);
  } else {
    digitalWrite(dirPin3, HIGH); digitalWrite(dirPin4, LOW);
  }
}

// โหมดซิงค์ — สั่งทั้งสองกล่องพร้อมกัน (มี ramp)
void moveBothCarriages(float target1, float target2) {
  float diff1 = target1 - currentAngle1;
  float diff2 = target2 - currentAngle2;

  long steps1 = round(abs(diff1) * stepsPerDegree);
  long steps2 = round(abs(diff2) * stepsPerDegree);
  long maxSteps = max(steps1, steps2);

  if (maxSteps > 0) {
    setDirBoxA(diff1);
    setDirBoxB(diff2);

    for (long x = 0; x < maxSteps; x++) {
      if (x < steps1) { digitalWrite(stepPin1, HIGH); digitalWrite(stepPin2, HIGH); }
      if (x < steps2) { digitalWrite(stepPin3, HIGH); digitalWrite(stepPin4, HIGH); }
      int d = rampDelayFor(x, maxSteps);
      delayMicroseconds(d);
      digitalWrite(stepPin1, LOW); digitalWrite(stepPin2, LOW);
      digitalWrite(stepPin3, LOW); digitalWrite(stepPin4, LOW);
      delayMicroseconds(d);
    }

    currentAngle1 = target1;
    currentAngle2 = target2;
  } else {
    Serial.println(F("already at target"));
  }
}

// โหมดคาลิเบรต — สั่งกล่อง A เดี่ยว (มี ramp, ไม่ยิง)
void moveSingleBoxA(float target1) {
  float diff1 = target1 - currentAngle1;
  long steps1 = round(abs(diff1) * stepsPerDegree);

  if (steps1 > 0) {
    setDirBoxA(diff1);
    for (long x = 0; x < steps1; x++) {
      digitalWrite(stepPin1, HIGH); digitalWrite(stepPin2, HIGH);
      int d = rampDelayFor(x, steps1);
      delayMicroseconds(d);
      digitalWrite(stepPin1, LOW); digitalWrite(stepPin2, LOW);
      delayMicroseconds(d);
    }
    currentAngle1 = target1;
  } else {
    Serial.println(F("box A already at target"));
  }
}

// โหมดคาลิเบรต — สั่งกล่อง B เดี่ยว (มี ramp, ไม่ยิง)
void moveSingleBoxB(float target2) {
  float diff2 = target2 - currentAngle2;
  long steps2 = round(abs(diff2) * stepsPerDegree);

  if (steps2 > 0) {
    setDirBoxB(diff2);
    for (long x = 0; x < steps2; x++) {
      digitalWrite(stepPin3, HIGH); digitalWrite(stepPin4, HIGH);
      int d = rampDelayFor(x, steps2);
      delayMicroseconds(d);
      digitalWrite(stepPin3, LOW); digitalWrite(stepPin4, LOW);
      delayMicroseconds(d);
    }
    currentAngle2 = target2;
  } else {
    Serial.println(F("box B already at target"));
  }
}
