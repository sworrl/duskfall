/*
 * Duskfall Perimeter Sensor — PIR + Reed Switch + Trip Wire
 *
 * Detects motion, door open/close, and trip wire break events.
 * Reports events immediately + periodic heartbeat to Duskfall server.
 *
 * Wiring:
 *   PIR (HC-SR501):  OUT=GPIO 27, VCC=5V, GND
 *   Reed switch:     GPIO 25 (with 10K pullup to 3.3V), GND
 *   Trip wire:       GPIO 26 (NC circuit with 10K pullup to 3.3V)
 *   LED:             GPIO 2 (onboard)
 *   Buzzer (opt):    GPIO 33
 *
 * Libraries: WiFi, HTTPClient, ArduinoJson
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

// ── Configuration ──
const char* WIFI_SSID     = "YOUR_SSID";
const char* WIFI_PASS     = "YOUR_PASSWORD";
const char* DUSKFALL_URL  = "http://YOUR_SERVER:8500";
const char* API_KEY       = "df_YOUR_DEVICE_API_KEY";

const float LATITUDE      = 0.0;
const float LONGITUDE     = 0.0;
const int   HEARTBEAT_S   = 300;  // 5 min heartbeat

// ── Pins ──
#define PIR_PIN    27
#define REED_PIN   25
#define TRIP_PIN   26
#define LED_PIN    2
#define BUZZER_PIN 33

// ── State ──
volatile int motionCount = 0;
bool lastDoorState = false;  // false = closed
bool lastTripState = false;  // false = intact
unsigned long lastHeartbeat = 0;
unsigned long lastMotionReport = 0;

void IRAM_ATTR onMotion() {
  motionCount++;
}

void setup() {
  Serial.begin(115200);
  pinMode(PIR_PIN, INPUT);
  pinMode(REED_PIN, INPUT_PULLUP);
  pinMode(TRIP_PIN, INPUT_PULLUP);
  pinMode(LED_PIN, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);

  // PIR interrupt
  attachInterrupt(digitalPinToInterrupt(PIR_PIN), onMotion, RISING);

  // Initial states
  lastDoorState = digitalRead(REED_PIN) == HIGH;
  lastTripState = digitalRead(TRIP_PIN) == HIGH;

  // WiFi
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts++ < 30) {
    delay(500);
  }
  Serial.printf("[perim] WiFi %s\n",
    WiFi.status() == WL_CONNECTED ? "OK" : "FAIL");

  // Startup alert
  sendEvent("system", "perimeter_online", "info", 0);
}

void sendEvent(const char* sensorType, const char* eventName,
               const char* severity, float value) {
  if (WiFi.status() != WL_CONNECTED) return;

  JsonDocument doc;
  JsonArray arr = doc.to<JsonArray>();
  JsonObject ev = arr.add<JsonObject>();
  ev["uid"] = String("perim-") + sensorType + "-" + millis();
  ev["feed_type"] = "sensor_alert";
  ev["title"] = String("[PERIMETER] ") + eventName;
  ev["description"] = String(sensorType) + " event: " + eventName;
  ev["latitude"] = LATITUDE;
  ev["longitude"] = LONGITUDE;
  ev["severity"] = severity;
  ev["source_url"] = "perimeter-sensor";

  String body;
  serializeJson(arr, body);

  HTTPClient http;
  http.begin(String(DUSKFALL_URL) + "/api/contribute/feed");
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Key", API_KEY);

  int code = http.POST(body);
  Serial.printf("[perim] %s -> %d\n", eventName, code);

  if (code == 200) {
    digitalWrite(LED_PIN, HIGH);
    delay(50);
    digitalWrite(LED_PIN, LOW);
  }
  http.end();
}

void alertBuzzer(int pattern) {
  for (int i = 0; i < pattern; i++) {
    digitalWrite(BUZZER_PIN, HIGH);
    delay(100);
    digitalWrite(BUZZER_PIN, LOW);
    delay(100);
  }
}

void loop() {
  unsigned long now = millis();

  // ── Motion detection ──
  if (motionCount > 0 && (now - lastMotionReport > 10000)) {
    int count = motionCount;
    motionCount = 0;
    lastMotionReport = now;
    Serial.printf("[perim] Motion: %d events\n", count);
    sendEvent("motion", "motion_detected", "warning", count);
    alertBuzzer(1);
  }

  // ── Door sensor (reed switch) ──
  bool doorOpen = digitalRead(REED_PIN) == HIGH;
  if (doorOpen != lastDoorState) {
    lastDoorState = doorOpen;
    if (doorOpen) {
      Serial.println("[perim] DOOR OPENED");
      sendEvent("door", "door_opened", "warning", 1);
      alertBuzzer(2);
    } else {
      Serial.println("[perim] Door closed");
      sendEvent("door", "door_closed", "info", 0);
    }
    delay(200);  // Debounce
  }

  // ── Trip wire (NC circuit — HIGH = broken) ──
  bool tripBroken = digitalRead(TRIP_PIN) == HIGH;
  if (tripBroken && !lastTripState) {
    lastTripState = true;
    Serial.println("[perim] TRIP WIRE BROKEN");
    sendEvent("tripwire", "tripwire_broken", "critical", 1);
    alertBuzzer(5);
  } else if (!tripBroken && lastTripState) {
    lastTripState = false;
    sendEvent("tripwire", "tripwire_restored", "info", 0);
  }

  // ── Heartbeat ──
  if (now - lastHeartbeat > (unsigned long)HEARTBEAT_S * 1000) {
    lastHeartbeat = now;
    sendEvent("heartbeat", "perimeter_alive", "info", motionCount);
  }

  delay(100);  // 10Hz polling
}
