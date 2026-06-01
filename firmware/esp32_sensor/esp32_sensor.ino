/*
 * Duskfall ESP32 Sensor Node Firmware
 *
 * Reports sensor readings to Duskfall backend via HTTP POST.
 * Supports: temperature, humidity, radiation, air quality, motion, gas, etc.
 *
 * Wiring (example with DHT22 + MQ-135 + PIR):
 *   DHT22 data  -> GPIO 4
 *   MQ-135 AO   -> GPIO 34 (ADC)
 *   PIR signal   -> GPIO 27
 *   LED status   -> GPIO 2 (built-in)
 *
 * Configuration:
 *   1. Register node at POST /api/sensors/register
 *   2. Set DUSKFALL_API_KEY below
 *   3. Set WiFi credentials
 *   4. Flash and deploy
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

// ============ CONFIGURATION ============
const char* WIFI_SSID     = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* DUSKFALL_URL  = "http://YOUR_DUSKFALL_HOST:8500";
const char* DUSKFALL_API_KEY = "YOUR_API_KEY_FROM_REGISTRATION";
const char* NODE_ID       = "esp32-sensor-01";

// GPS coordinates of this sensor (set manually or use GPS module)
const float LATITUDE  = 0.0;
const float LONGITUDE = 0.0;

// Reporting interval in seconds
const int REPORT_INTERVAL = 60;

// Sensor pins
const int DHT_PIN    = 4;
const int MQ135_PIN  = 34;
const int PIR_PIN    = 27;
const int LED_PIN    = 2;

// ============ DHT22 (simplified — use DHT library in production) ============
// Install: "DHT sensor library" by Adafruit
#ifdef USE_DHT
#include <DHT.h>
DHT dht(DHT_PIN, DHT22);
#endif

// ============ GLOBALS ============
unsigned long lastReport = 0;
int motionCount = 0;

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  pinMode(PIR_PIN, INPUT);
  pinMode(MQ135_PIN, INPUT);

  #ifdef USE_DHT
  dht.begin();
  #endif

  // Connect WiFi
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    digitalWrite(LED_PIN, !digitalRead(LED_PIN));
  }
  Serial.println("\nConnected: " + WiFi.localIP().toString());
  digitalWrite(LED_PIN, HIGH);
}

void loop() {
  // Count motion events between reports
  if (digitalRead(PIR_PIN) == HIGH) {
    motionCount++;
    delay(200); // debounce
  }

  // Report at interval
  if (millis() - lastReport >= REPORT_INTERVAL * 1000UL) {
    lastReport = millis();
    reportReadings();
    motionCount = 0;
  }

  delay(100);
}

void reportReadings() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi disconnected, skipping report");
    return;
  }

  // Build batch payload
  JsonDocument doc;
  JsonArray readings = doc["readings"].to<JsonArray>();

  #ifdef USE_DHT
  float temp = dht.readTemperature();
  float hum  = dht.readHumidity();

  if (!isnan(temp)) {
    JsonObject r = readings.add<JsonObject>();
    r["sensor_type"] = "temperature";
    r["value"] = temp;
    r["unit"] = "C";
  }
  if (!isnan(hum)) {
    JsonObject r = readings.add<JsonObject>();
    r["sensor_type"] = "humidity";
    r["value"] = hum;
    r["unit"] = "%";
  }
  #endif

  // MQ-135 air quality (raw ADC -> approximate AQI)
  int gasRaw = analogRead(MQ135_PIN);
  float gasPPM = map(gasRaw, 0, 4095, 0, 500); // rough calibration
  {
    JsonObject r = readings.add<JsonObject>();
    r["sensor_type"] = "gas";
    r["value"] = gasPPM;
    r["unit"] = "ppm";
  }

  // Motion events since last report
  if (motionCount > 0) {
    JsonObject r = readings.add<JsonObject>();
    r["sensor_type"] = "motion";
    r["value"] = motionCount;
    r["unit"] = "events";
  }

  // Send batch
  String payload;
  serializeJson(doc, payload);

  HTTPClient http;
  String url = String(DUSKFALL_URL) + "/api/sensors/batch";
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Sensor-Key", DUSKFALL_API_KEY);

  int httpCode = http.POST(payload);
  if (httpCode == 200) {
    Serial.println("Report sent OK");
    blinkLED(1);
  } else {
    Serial.printf("Report failed: %d\n", httpCode);
    blinkLED(3);
  }
  http.end();
}

void blinkLED(int times) {
  for (int i = 0; i < times; i++) {
    digitalWrite(LED_PIN, LOW);
    delay(100);
    digitalWrite(LED_PIN, HIGH);
    delay(100);
  }
}
