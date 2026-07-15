// SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
//
// SPDX-License-Identifier: MPL-2.0

// -----------------------------------------------------------------------
// Sketch MCU : servo "flèche" qui pointe vers le palet.
//
// Le MPU (Python) calcule l'angle et appelle "set_servo_angle" via le
// Bridge RPC. Le MCU (STM32) pilote le servo avec la bibliothèque Servo.
//
// CÂBLAGE : signal du servo -> D3, alimentation -> 5V, masse -> GND.
// -----------------------------------------------------------------------

#include <Arduino_RouterBridge.h>
#include <Servo.h>

namespace {
const int kServoPin = 3;      // D3 (change ici si tu rebranches)
const int kStartAngle = 90;   // position au démarrage (milieu)
Servo pointerServo;
}  // namespace

// Appelé depuis Python : Bridge.notify("set_servo_angle", angle)
bool set_servo_angle(int angle) {
  if (angle < 0) angle = 0;
  if (angle > 180) angle = 180;
  pointerServo.write(angle);
  return true;
}

void setup() {
  Bridge.begin();

  pointerServo.attach(kServoPin);
  pointerServo.write(kStartAngle);

  // provide_safe : l'écriture PWM s'exécute dans le contexte loop() (sûr).
  Bridge.provide_safe("set_servo_angle", set_servo_angle);
}

void loop() {
  // Rien à faire : l'angle est mis à jour via les appels Bridge.
}
