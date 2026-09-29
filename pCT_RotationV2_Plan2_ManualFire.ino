// ========================================================
// pCT Rotation V2 — Plan 2 (เวอร์ชัน Manual FIRE): ALPIDE + Beam แยกอิสระจากการขยับมอเตอร์
// ไฟล์แยกต่างหาก ไม่ทับ pCT_RotationV2_Plan2_MotorALPIDEBeam.ino (เวอร์ชันเดิม auto-fire)
//
// ต่างจากเวอร์ชันเดิม (auto-fire): เวอร์ชันเดิม 'start'/'close'/พิมพ์มุม จะยิง ALPIDE+Beam
// อัตโนมัติก่อนขยับมอเตอร์ทุกครั้ง — ไฟล์นี้แยกออกจากกันเป็นคนละคำสั่ง:
//   คำสั่งขยับมุม (start/close/พิมพ์มุม/A<มุม>/B<มุม>) = ขยับมอเตอร์เฉยๆ ไม่ยิงอะไรเลย
//   คำสั่ง FIRE = ยิง ALPIDE+Beam ที่ตำแหน่งปัจจุบัน ไม่ขยับมอเตอร์ ต้องพิมพ์เองเท่านั้น
// เหตุผล: มอเตอร์แรงไม่พอผ่านโซนแรงโน้มถ่วง (60-90°/-60~-90°) ต้องดันมือช่วยระหว่างทาง
// ตำแหน่งจริงระหว่างขยับจึงไม่แน่นอน จึงอยากแยกขั้นตอน "ไปตำแหน่ง" กับ "ยิง" ออกจากกัน
// เพื่อให้เช็ค/แก้ตำแหน่งจริงด้วย SETA/SETB ให้เรียบร้อยก่อน แล้วค่อยสั่งยิงเองตอนพร้อมจริง
// (ยกเว้น SCAN ที่ยังคง auto-fire ทุก step เพราะออกแบบมาให้ใช้เฉพาะตอนอยู่นอกโซนอันตรายแล้ว)
//
// อ้างอิงแนวคิดจากโค้ดรุ่นพี่ (eye_tracking_beam.ino, KCMH-Tricker beam_controller.py):
//   - ระยะเวลาค้างสัญญาณ (dwell) เป็นค่า "ปรับได้จาก host" ไม่ใช่ค่าคงที่ตายตัว
//     (ของรุ่นพี่เป็นช่อง UI "Beam delay (ms)" ส่งเป็น byte ให้ FPGA)
//     -> โค้ดนี้ทำเป็นตัวแปรปรับได้ผ่าน Serial command 'D<ms>' แทน พร้อมค่าเริ่มต้น
//   - Relay watchdog: ของรุ่นพี่มีไว้กัน host ค้าง/หลุดการเชื่อมต่อแล้ว relay ค้างในสถานะเปิด
//     ระบบเราจบ sequence ในฟังก์ชันเดียวแบบ blocking (ไม่ปล่อยให้ relay ค้างเปิดข้าม loop())
//     จึงไม่มีความเสี่ยงแบบนั้น แต่ยังคง fail-safe แบบเดียวกันไว้ที่ setup() (initial state = LOW เสมอ)
//     และมี GLOBAL_KILL ผ่านคำสั่ง 'K' ปิดทุกอย่างทันทีเผื่อฉุกเฉินระหว่าง dwell
//
// ⚠️ ข้อควรระวัง — ยังไม่ได้ทดสอบจริงกับฮาร์ดแวร์ ALPIDE/TSS เลย (ดู §16.5 ของเอกสาร):
//   - ค่า dwell เริ่มต้น 90ms อ้างอิงจาก gas1=90 ในโค้ดเก่า (ARDUINO_MODBUS_Original_Code.ino) —
//     ยังไม่มี datasheet ALPIDE ยืนยันตายตัว ต้องทดสอบปรับจริงหน้างาน (ปรับได้ทาง D<ms>)
//   - สัญญาณ ALPIDE ผ่าน LEMO ที่นี่เป็น "5V logic ตรง" (ตามที่ตัดสินใจไว้ในเอกสาร §15.3)
//     ไม่ใช่ readout trigger clock ต่อเนื่องแบบที่โค้ดรุ่นพี่ทำ (นั่นเป็นอีกระบบ ใช้ FPGA/Timer4 PWM
//     ป้อน trigger หลาย kHz ให้ ALPIDE โดยตรง) — ถ้าระบบ ALPIDE จริงของเราต้องการ trigger clock
//     แบบเดียวกัน ต้องแจ้งกลับมาเพื่อเปลี่ยนจาก digitalWrite เดี่ยวเป็น PWM/Timer แทน
//   - D22/D23 (TSS relay) และ D11 (LEMO J2) ยังไม่เคยทดสอบจริงในเซสชันนี้เลย
//
// วิธีใช้ผ่าน Serial Monitor (115200 baud):
//   'start'      -> ไปจุดสมมาตร (A=90, B=-90) เฉยๆ (ไม่ยิง ALPIDE/Beam — มอเตอร์อาจไปได้ไม่ถึงจริง
//                  ถ้าติดโซนแรงโน้มถ่วง ให้เช็คตำแหน่งจริงแล้วแก้ด้วย SETA/SETB ต่อ)
//   'close'      -> กลับ home เฉยๆ (ไม่ยิง ALPIDE/Beam)
//   <ตัวเลข>     -> ไปมุมนั้นเฉยๆ (ต้อง 'start'/SETA+SETB ก่อน, ไม่ยิง ALPIDE/Beam)
//   A<ตัวเลข>/B<ตัวเลข> -> คาลิเบรตแยกกล่อง สั่งมอเตอร์ขยับจริง (ไม่ยิง ALPIDE/Beam)
//   SETA<ตัวเลข>/SETB<ตัวเลข> -> ประกาศตำแหน่งปัจจุบัน "โดยไม่ขยับมอเตอร์" ใช้หลังดันด้วยมือผ่าน
//                  โซนแรงโน้มถ่วง (วัดมุมจริงด้วยตา/โปรแทรกเตอร์แล้วพิมพ์เข้าไป) — ปลดล็อกคำสั่งมุม
//   FIRE         -> ยิง ALPIDE+Beam ที่ตำแหน่งปัจจุบัน (ไม่ขยับมอเตอร์) — พิมพ์เองเมื่อพร้อมจริงเท่านั้น
//   SCAN<องศา/step> -> สแกนอัตโนมัติครบ 360° ทีละ step (ยิง ALPIDE+Beam ทุก step อัตโนมัติ — ใช้เฉพาะ
//                  ตอนอยู่นอกโซนอันตรายแล้ว) เช่น SCAN5 = ทีละ 5°
//   X            -> ยกเลิก SCAN (เช็คระหว่าง step เท่านั้น ไม่ตัดกลางที่กำลังหมุนอยู่)
//   D<ms>        -> ตั้งค่า dwell time ใหม่ (เช่น D800 = 800ms) มีผลกับครั้งถัดไป
//   K            -> Kill ทันที — ปิด LEMO/K1/K2 ทั้งหมด (ใช้เวลาฉุกเฉิน ไม่ต้องรอ dwell จบ)
// ========================================================

#include <math.h>   // fabs() ใช้ใน runScanSequence()

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

// TSS relay (dual-channel safety switch — ต้องปิดทั้งคู่ให้ครบวงจร ดู §14.4)
const int tssK1 = 22;
const int tssK2 = 23;

// LEMO ALPIDE — J2/D11 กำลังใช้งานจริงตอนนี้ (J1/D10, J3/D12 เผื่อไว้ ยังไม่ได้ต่อ)
const int lemoJ2 = 11;

// ========================================================
// ⚙️ สเปกมอเตอร์+เกียร์+พูลเลย์+วงแหวน (ค่าจริงจากหัวข้อ 2/5A/8)
// ========================================================
long pulseSetting = 3200;
float gearRatio = 4.25;
long totalPulsesForOneGearRev = pulseSetting * gearRatio;

float trackDiameter = 680.8;
float gearDiameter = 48.51;
float gearRevsNeeded = trackDiameter / gearDiameter;
float totalStepsFor360 = gearRevsNeeded * totalPulsesForOneGearRev;
float stepsPerDegree = totalStepsFor360 / 360.0;   // ≈ 530.14

// ⚙️ Acceleration Ramp
int minSpeedDelay = 500;   // µs — ความเร็วช่วงวิ่งคงที่ (cruise)
int maxSpeedDelay = 1200;  // µs — ความเร็วช่วงเริ่ม/จบ
long rampSteps = 300;      // จำนวนพัลส์ที่ใช้ค่อยๆ เร่ง/ค่อยๆ ลด

// 📍 ค่าเริ่มต้น — แก้ให้ตรงกับตำแหน่งจริงก่อนใช้งาน
const float initAngleA = 37.1;
const float initAngleB = -35.0;

float currentAngle1 = initAngleA;
float currentAngle2 = initAngleB;

bool isInitialized = false;

// ⏱ dwell time ปรับได้จาก Serial ('D<ms>') — ค่าเริ่มต้น 90ms อ้างอิงจาก gas1=90 ในโค้ดเก่า
// (ARDUINO_MODBUS_Original_Code.ino ระบบ ALPIDE+TSS รุ่นก่อนหน้า ใช้ delay(gas1) ค่าเดียวกันนี้จริง
// สอดคล้องกับ trig_hz=9500Hz ของ ALPIDE กลุ่มวิจัยเดียวกัน = ~850 เฟรม/ตำแหน่งมุม) ยังไม่ยืนยันจาก datasheet
unsigned long alpideDwellMs = 90;

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

  Serial.println(F("\n==========================================="));
  Serial.println(F("   pCT V2 — Plan 2 (Manual FIRE)   "));
  Serial.println(F("==========================================="));
  Serial.print(F(">> dwell time ปัจจุบัน: ")); Serial.print(alpideDwellMs); Serial.println(F(" ms"));
  Serial.println(F(">> 'start'/'close'/<มุม> = ขยับมอเตอร์เฉยๆ (ไม่ยิง ALPIDE/Beam อัตโนมัติ)"));
  Serial.println(F(">> A<มุม>/B<มุม> = คาลิเบรตแยกกล่อง มอเตอร์ขยับจริง (ไม่ยิง ALPIDE/Beam)"));
  Serial.println(F(">> SETA<มุม>/SETB<มุม> = ประกาศตำแหน่งจริงหลังดันมือผ่านโซนแรงโน้มถ่วง (ไม่ขยับมอเตอร์)"));
  Serial.println(F(">> FIRE = ยิง ALPIDE+Beam ที่ตำแหน่งปัจจุบัน (พิมพ์เองเมื่อพร้อมจริง)"));
  Serial.println(F(">> SCAN<องศา/step> = สแกนอัตโนมัติครบ 360° (auto-fire ทุก step) | X = ยกเลิก SCAN"));
  Serial.println(F(">> D<ms> = ตั้ง dwell ใหม่ | K = kill ฉุกเฉิน"));
}

void loop() {
  if (Serial.available() > 0) {
    String input = Serial.readStringUntil('\n');
    input.trim();
    if (input.length() == 0) return;

    if (input.equalsIgnoreCase("K")) {
      killSwitch();
    }
    else if (input.equalsIgnoreCase("X")) {
      Serial.println(F("\n(หมายเหตุ: 'X' ใช้ยกเลิก SCAN เท่านั้น เช็คระหว่าง step — ตอนนี้ไม่มี SCAN ทำงานอยู่)"));
    }
    else if (input.equalsIgnoreCase("FIRE")) {
      if (!isInitialized) {
        Serial.println(F("\n❌ ต้อง 'start' หรือ SETA+SETB ก่อนถึงจะยิงได้ (ต้องรู้ตำแหน่งจริงก่อน)"));
      } else {
        Serial.println(F("\n🔥 FIRE — ยิง ALPIDE+Beam ที่ตำแหน่งปัจจุบัน (สั่งเองเมื่อพร้อมจริง)"));
        runBeamSequence();
      }
    }
    else if (input.equalsIgnoreCase("start")) {
      Serial.println(F("\n🚀 [ซิงค์] กำลังไปจุดสมมาตร (A=90, B=-90)... (ไม่ยิง ALPIDE/Beam อัตโนมัติ)"));
      moveBothCarriages(90.0, -90.0);
      isInitialized = true;
      Serial.println(F("✅ ถึงแล้ว (หรือใกล้เคียงที่สุดที่มอเตอร์ไปได้) — เช็คตำแหน่งจริง ใช้ SETA/SETB แก้ถ้าจำเป็น แล้วพิมพ์ FIRE เมื่อพร้อม"));
    }
    else if (input.equalsIgnoreCase("close")) {
      Serial.println(F("\n🏠 [ซิงค์] กำลังกลับไปจุดเริ่มต้น... (ไม่ยิง ALPIDE/Beam อัตโนมัติ)"));
      moveBothCarriages(initAngleA, initAngleB);
      isInitialized = false;
      Serial.println(F("✅ ถึงจุดจอดเรียบร้อยแล้ว"));
    }
    else if (input.charAt(0) == 'D' || input.charAt(0) == 'd') {
      long v = input.substring(1).toInt();
      if (v > 0) {
        alpideDwellMs = (unsigned long)v;
        Serial.print(F("⏱ ตั้ง dwell time ใหม่: ")); Serial.print(alpideDwellMs); Serial.println(F(" ms"));
      } else {
        Serial.println(F("❌ ค่า dwell ไม่ถูกต้อง (ต้อง > 0) เช่น D800"));
      }
    }
    else if (input.startsWith("SETA") || input.startsWith("seta")) {
      float v = input.substring(4).toFloat();
      currentAngle1 = v;
      isInitialized = true;
      Serial.print(F("\n📐 ประกาศตำแหน่งจริงกล่อง A (ไม่ขยับมอเตอร์) = ")); Serial.println(currentAngle1);
    }
    else if (input.startsWith("SETB") || input.startsWith("setb")) {
      float v = input.substring(4).toFloat();
      currentAngle2 = v;
      isInitialized = true;
      Serial.print(F("\n📐 ประกาศตำแหน่งจริงกล่อง B (ไม่ขยับมอเตอร์) = ")); Serial.println(currentAngle2);
    }
    else if (input.startsWith("SCAN") || input.startsWith("scan")) {
      float degStep = input.substring(4).toFloat();
      if (!isInitialized) {
        Serial.println(F("\n❌ ต้อง 'start' หรือ SETA+SETB ก่อนเริ่ม SCAN"));
      } else if (degStep == 0) {
        Serial.println(F("\n❌ ค่า step ไม่ถูกต้อง (ต้อง != 0) เช่น SCAN5"));
      } else {
        runScanSequence(degStep);
      }
    }
    else if ((input.charAt(0) == 'A' || input.charAt(0) == 'a') && input.length() > 1) {
      float targetA = input.substring(1).toFloat();
      Serial.println(F("\n🔧 [คาลิเบรต] สั่งเฉพาะกล่อง A (มอเตอร์ขยับจริง, ไม่ยิง ALPIDE/Beam)..."));
      moveSingleBoxA(targetA);
    }
    else if ((input.charAt(0) == 'B' || input.charAt(0) == 'b') && input.length() > 1) {
      float targetB = input.substring(1).toFloat();
      Serial.println(F("\n🔧 [คาลิเบรต] สั่งเฉพาะกล่อง B (มอเตอร์ขยับจริง, ไม่ยิง ALPIDE/Beam)..."));
      moveSingleBoxB(targetB);
    }
    else if (isInitialized) {
      float target1 = input.toFloat();
      float target2 = target1 - 180.0;
      Serial.println(F("\n[ซิงค์] กำลังไปตำแหน่งที่สั่ง... (ไม่ยิง ALPIDE/Beam อัตโนมัติ — พิมพ์ FIRE เมื่อพร้อม)"));
      moveBothCarriages(target1, target2);
    }
    else {
      Serial.println(F("\n❌ กรุณาพิมพ์ 'start' ก่อนเริ่มสั่งการองศาแบบซิงค์"));
    }
  }
}

// ========================================================
// ⚡ ยิง ALPIDE + Beam (§15.3 ของเอกสาร): เปิด ALPIDE -> เปิด Beam (K1+K2 พร้อมกัน)
// -> ค้าง dwell -> ปิดพร้อมกันทั้งคู่ — เรียกเฉพาะตอนพิมพ์คำสั่ง FIRE เท่านั้น (ไม่ผูกกับการขยับมุม)
// เป็นฟังก์ชัน blocking (ใช้ delay()) ไม่ปล่อยให้ relay ค้างเปิดข้าม loop() จึงไม่ต้องมี watchdog
// แบบโค้ดรุ่นพี่
// ========================================================
void runBeamSequence() {
  Serial.println(F("  🔆 ALPIDE ON"));
  digitalWrite(lemoJ2, HIGH);

  Serial.println(F("  ☢️  Beam ON (K1+K2)"));
  digitalWrite(tssK1, HIGH);
  digitalWrite(tssK2, HIGH);

  delay(alpideDwellMs);

  digitalWrite(tssK1, LOW);
  digitalWrite(tssK2, LOW);
  digitalWrite(lemoJ2, LOW);
  Serial.println(F("  ⏹  Beam+ALPIDE OFF (พร้อมกัน)"));
}

// ========================================================
// 🔄 สแกนอัตโนมัติครบ 360° ทีละ degStep องศา — ยังคง auto-fire ทุก step (ต่างจากคำสั่งขยับมุมปกติ
// ที่แยกออกจาก FIRE แล้ว) เพราะ SCAN ออกแบบมาให้ใช้เฉพาะตอนอยู่นอกโซนอันตรายแล้วเท่านั้น
// เช็คคำสั่ง 'X' ระหว่าง step เพื่อยกเลิกได้ (เช็คได้เฉพาะ "ระหว่าง step" เท่านั้น — ถ้ากำลังหมุน
// หรือ dwell อยู่ต้องรอให้ step นั้นจบก่อน)
// ========================================================
void runScanSequence(float degStep) {
  int totalSteps = round(360.0 / fabs(degStep));
  Serial.print(F("\n🔄 เริ่มสแกนอัตโนมัติ ")); Serial.print(degStep);
  Serial.print(F(" องศา/step, รวม ")); Serial.print(totalSteps); Serial.println(F(" step (พิมพ์ X เพื่อยกเลิก)"));

  for (int i = 0; i < totalSteps; i++) {
    if (Serial.available() > 0) {
      String cmd = Serial.readStringUntil('\n');
      cmd.trim();
      if (cmd.equalsIgnoreCase("X")) {
        Serial.println(F("\n⛔ ยกเลิก SCAN แล้ว (หยุดที่ step ปัจจุบัน)"));
        return;
      }
    }

    Serial.print(F("\n--- SCAN step ")); Serial.print(i + 1); Serial.print(F("/")); Serial.println(totalSteps);
    runBeamSequence();

    float target1 = currentAngle1 + degStep;
    float target2 = target1 - 180.0;
    moveBothCarriages(target1, target2);
  }

  Serial.println(F("\n✅ สแกนครบ 360° แล้ว"));
}

// Kill ฉุกเฉิน — ใช้ตอนพิมพ์ 'K' เข้ามา (นอก sequence ปกติ เผื่อกรณีต้องปิดกลางทางแบบ manual)
void killSwitch() {
  digitalWrite(tssK1, LOW);
  digitalWrite(tssK2, LOW);
  digitalWrite(lemoJ2, LOW);
  Serial.println(F("\n🛑 KILL — ปิด LEMO/K1/K2 ทั้งหมดทันที"));
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

// กล่อง A — สมมติฐานเดิม (DIR สวนกัน) ยังไม่เคยยืนยันด้วยวิธีแม่นยำ (ดู §16.2 ของเอกสาร)
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
    Serial.print(F("📍 ตำแหน่งปัจจุบัน: A=")); Serial.print(currentAngle1);
    Serial.print(F(", B=")); Serial.println(currentAngle2);
  } else {
    Serial.println(F("📍 อยู่ตำแหน่งเป้าหมายแล้ว"));
  }
}

// โหมดคาลิเบรต — สั่งกล่อง A เดี่ยว (มี ramp, ไม่ยิง ALPIDE/Beam)
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
    Serial.print(F("📍 กล่อง A ตำแหน่งปัจจุบัน: ")); Serial.println(currentAngle1);
  } else {
    Serial.println(F("📍 กล่อง A อยู่ตำแหน่งเป้าหมายแล้ว"));
  }
}

// โหมดคาลิเบรต — สั่งกล่อง B เดี่ยว (มี ramp, ไม่ยิง ALPIDE/Beam)
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
    Serial.print(F("📍 กล่อง B ตำแหน่งปัจจุบัน: ")); Serial.println(currentAngle2);
  } else {
    Serial.println(F("📍 กล่อง B อยู่ตำแหน่งเป้าหมายแล้ว"));
  }
}
