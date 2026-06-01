/*
 * Duskfall ESP32 Geiger Counter Node
 *
 * Reads pulses from a Geiger-Muller tube (via RadiationD-v1.1 board or similar)
 * and reports radiation levels (uSv/h) to Duskfall backend.
 *
 * Wiring:
 *   Geiger pulse output -> GPIO 18 (interrupt)
 *   LED status          -> GPIO 2 (built-in)
 *
 * Tube calibration:
 *   SBM-20:     CPM / 151 = uSv/h
 *   J305:       CPM / 153 = uSv/h
 *   LND-712:    CPM / 120 = uSv/h
 *
 * Normal background radiation: 0.05 - 0.20 uSv/h
 * Alert threshold:             > 0.50 uSv/h
 * Dangerous:                   > 1.00 uSv/h
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

// ============ CONFIGURATION ============
const char* WIFI_SSID     = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* DUSKFALL_URL  = "http://YOUR_DUSKFALL_HOST:8500";
const char* DUSKFALL_API_KEY = "YOUR_API_KEY";

const float LATITUDE  = 0.0;
const float LONGITUDE = 0.0;

// Tube calibration factor (CPM to uSv/h)
const float CPM_TO_USV = 151.0; // SBM-20 default

// Reporting interval
const int REPORT_INTERVAL_SEC = 60;

// Pins
const int GEIGER_PIN = 18;
const int LED_PIN    = 2;

// ============ GLOBALS ============
volatile unsigned long pulseCount = 0;
unsigned long lastReport = 0;
unsigned long lastMinutePulses = 0;
float cpm = 0;
float usvh = 0;

// Ring buffer for 5-minute running average
const int AVG_WINDOW = 5;
float cpmHistory[AVG_WINDOW] = {0};
int historyIdx = 0;

void IRAM_ATTR onGeigerPulse() {
  pulseCount++;
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  pinMode(GEIGER_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(GEIGER_PIN), onGeigerPulse, FALLING);

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nConnected: " + WiFi.localIP().toString());
}

void loop() {
  if (millis() - lastReport >= REPORT_INTERVAL_SEC * 1000UL) {
    lastReport = millis();

    // Calculate CPM
    noInterrupts();
    unsigned long counts = pulseCount;
    pulseCount = 0;
    interrupts();

    cpm = counts * (60.0 / REPORT_INTERVAL_SEC);
    usvh = cpm / CPM_TO_USV;

    // Update running average
    cpmHistory[historyIdx] = cpm;
    historyIdx = (historyIdx + 1) % AVG_WINDOW;
    float avgCPM = 0;
    for (int i = 0; i < AVG_WINDOW; i++) avgCPM += cpmHistory[i];
    avgCPM /= AVG_WINDOW;
    float avgUSV = avgCPM / CPM_TO_USV;

    Serial.printf("CPM: %.0f | uSv/h: %.3f | 5min avg: %.3f uSv/h\n",
                  cpm, usvh, avgUSV);

    // Determine quality
    const char* quality = "good";
    if (counts < 5) quality = "low_count"; // statistically unreliable

    // Flash LED proportional to radiation level
    int flashes = (usvh > 1.0) ? 10 : (usvh > 0.5) ? 5 : 1;
    blinkLED(flashes);

    // Report to Duskfall
    reportRadiation(usvh, avgUSV, cpm, quality);
  }

  delay(10);
}

void reportRadiation(float instant, float avg5m, float currentCPM, const char* quality) {
  if (WiFi.status() != WL_CONNECTED) return;

  JsonDocument doc;
  JsonArray readings = doc["readings"].to<JsonArray>();

  // Instant reading
  JsonObject r1 = readings.add<JsonObject>();
  r1["sensor_type"] = "radiation";
  r1["value"] = instant;
  r1["unit"] = "uSv/h";
  r1["quality"] = quality;

  String payload;
  serializeJson(doc, payload);

  HTTPClient http;
  http.begin(String(DUSKFALL_URL) + "/api/sensors/batch");
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Sensor-Key", DUSKFALL_API_KEY);
  http.POST(payload);
  http.end();
}

void blinkLED(int times) {
  for (int i = 0; i < times; i++) {
    digitalWrite(LED_PIN, LOW);
    delay(50);
    digitalWrite(LED_PIN, HIGH);
    delay(50);
  }
}
