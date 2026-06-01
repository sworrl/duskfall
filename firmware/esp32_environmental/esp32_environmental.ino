/*
 * Duskfall Environmental Sensor — BME280 + BH1750
 *
 * Reads temperature, humidity, pressure, and light level.
 * Reports to Duskfall server via HTTP POST every REPORT_INTERVAL_S seconds.
 *
 * Wiring (I2C shared bus):
 *   BME280:  SDA=21, SCL=22, VCC=3.3V
 *   BH1750:  SDA=21, SCL=22, VCC=3.3V, ADDR=GND (0x23)
 *   LED:     GPIO 2 (onboard)
 *
 * Libraries: Adafruit_BME280, BH1750, WiFi, HTTPClient, ArduinoJson
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <Adafruit_BME280.h>
#include <BH1750.h>
#include <ArduinoJson.h>

// ── Configuration ──
const char* WIFI_SSID     = "YOUR_SSID";
const char* WIFI_PASS     = "YOUR_PASSWORD";
const char* DUSKFALL_URL  = "http://YOUR_SERVER:8500";
const char* API_KEY       = "df_YOUR_DEVICE_API_KEY";

const float LATITUDE      = 0.0;   // Set your location
const float LONGITUDE     = 0.0;
const int   REPORT_INTERVAL_S = 60;

// ── Hardware ──
#define LED_PIN 2
Adafruit_BME280 bme;
BH1750 lightMeter;

bool bme_ok = false;
bool bh_ok  = false;

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);

  // I2C
  Wire.begin(21, 22);

  // BME280
  bme_ok = bme.begin(0x76);
  if (!bme_ok) bme_ok = bme.begin(0x77);  // Try alt address
  Serial.printf("[env] BME280: %s\n", bme_ok ? "OK" : "NOT FOUND");

  // BH1750
  bh_ok = lightMeter.begin(BH1750::CONTINUOUS_HIGH_RES_MODE, 0x23);
  Serial.printf("[env] BH1750: %s\n", bh_ok ? "OK" : "NOT FOUND");

  // WiFi
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("[env] WiFi");
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts++ < 30) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf(" %s (%s)\n",
    WiFi.status() == WL_CONNECTED ? "OK" : "FAIL",
    WiFi.localIP().toString().c_str());
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    WiFi.reconnect();
    delay(5000);
    return;
  }

  // Read sensors
  JsonDocument doc;
  JsonArray readings = doc["readings"].to<JsonArray>();

  if (bme_ok) {
    float temp = bme.readTemperature();
    float hum  = bme.readHumidity();
    float pres = bme.readPressure() / 100.0;  // hPa

    JsonObject r1 = readings.add<JsonObject>();
    r1["sensor_type"] = "temperature";
    r1["value"] = temp;
    r1["unit"] = "C";

    JsonObject r2 = readings.add<JsonObject>();
    r2["sensor_type"] = "humidity";
    r2["value"] = hum;
    r2["unit"] = "%";

    JsonObject r3 = readings.add<JsonObject>();
    r3["sensor_type"] = "pressure";
    r3["value"] = pres;
    r3["unit"] = "hPa";

    Serial.printf("[env] T=%.1fC H=%.0f%% P=%.1fhPa", temp, hum, pres);
  }

  if (bh_ok) {
    float lux = lightMeter.readLightLevel();
    JsonObject r4 = readings.add<JsonObject>();
    r4["sensor_type"] = "light";
    r4["value"] = lux;
    r4["unit"] = "lux";
    Serial.printf(" L=%.0flux", lux);
  }

  Serial.println();

  // Send to Duskfall
  if (readings.size() > 0) {
    // Add location to each reading
    for (JsonVariant r : readings) {
      r["node_id"] = "env-01";
      r["latitude"] = LATITUDE;
      r["longitude"] = LONGITUDE;
      r["quality"] = "good";
    }

    // Reformat for contribute API
    JsonDocument payload;
    JsonArray sensorReadings = payload.to<JsonArray>();
    for (JsonVariant r : readings) {
      sensorReadings.add(r);
    }

    String body;
    serializeJson(sensorReadings, body);

    HTTPClient http;
    String url = String(DUSKFALL_URL) + "/api/contribute/sensor";
    http.begin(url);
    http.addHeader("Content-Type", "application/json");
    http.addHeader("X-Device-Key", API_KEY);

    int code = http.POST(body);
    if (code == 200) {
      digitalWrite(LED_PIN, HIGH);
      delay(100);
      digitalWrite(LED_PIN, LOW);
      Serial.println("[env] Sent OK");
    } else {
      Serial.printf("[env] Send FAIL: %d\n", code);
      for (int i = 0; i < 3; i++) {
        digitalWrite(LED_PIN, HIGH);
        delay(100);
        digitalWrite(LED_PIN, LOW);
        delay(100);
      }
    }
    http.end();
  }

  delay(REPORT_INTERVAL_S * 1000);
}
