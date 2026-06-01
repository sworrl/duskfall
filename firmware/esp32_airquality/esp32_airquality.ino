/*
 * Duskfall Air Quality Sensor — PMS5003 + MQ-135
 *
 * Reads PM2.5, PM10 particulate matter and gas/VOC levels.
 * Reports to Duskfall server via HTTP POST.
 *
 * Wiring:
 *   PMS5003:  TX=GPIO 16 (RX2), VCC=5V, GND, SET=3.3V
 *   MQ-135:   AOUT=GPIO 34 (ADC), VCC=5V, GND
 *   LED:      GPIO 2
 *
 * Libraries: WiFi, HTTPClient, ArduinoJson, HardwareSerial
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
const int   REPORT_INTERVAL_S = 60;

// ── Pins ──
#define MQ135_PIN 34
#define LED_PIN   2
#define PMS_RX    16  // ESP32 RX2 <- PMS5003 TX

// ── PMS5003 data structure ──
struct PMSData {
  uint16_t pm10_standard, pm25_standard, pm100_standard;
  uint16_t pm10_env, pm25_env, pm100_env;
  uint16_t particles_03, particles_05, particles_10;
  uint16_t particles_25, particles_50, particles_100;
};

HardwareSerial pmsSerial(2);  // UART2
bool pms_ok = false;

bool readPMS(PMSData* data) {
  if (!pmsSerial.available()) return false;

  // Sync to start byte
  while (pmsSerial.available()) {
    if (pmsSerial.read() == 0x42) {
      if (pmsSerial.peek() == 0x4D) {
        pmsSerial.read();  // consume 0x4D

        uint8_t buf[30];
        if (pmsSerial.readBytes(buf, 30) != 30) return false;

        uint16_t frameLen = (buf[0] << 8) | buf[1];
        if (frameLen != 28) return false;

        // Checksum
        uint16_t sum = 0x42 + 0x4D;
        for (int i = 0; i < 28; i++) sum += buf[i];
        uint16_t check = (buf[28] << 8) | buf[29];
        if (sum != check) return false;

        // Parse (standard atmosphere values)
        data->pm10_standard  = (buf[2] << 8)  | buf[3];
        data->pm25_standard  = (buf[4] << 8)  | buf[5];
        data->pm100_standard = (buf[6] << 8)  | buf[7];
        // Environmental values
        data->pm10_env  = (buf[8] << 8)  | buf[9];
        data->pm25_env  = (buf[10] << 8) | buf[11];
        data->pm100_env = (buf[12] << 8) | buf[13];

        return true;
      }
    }
  }
  return false;
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  pinMode(MQ135_PIN, INPUT);

  // PMS5003 UART (9600 baud)
  pmsSerial.begin(9600, SERIAL_8N1, PMS_RX, -1);  // RX only

  // WiFi
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts++ < 30) {
    delay(500);
  }
  Serial.printf("[aq] WiFi %s\n",
    WiFi.status() == WL_CONNECTED ? "OK" : "FAIL");

  // Warm up MQ-135 (needs ~2 min, but we start reporting immediately)
  Serial.println("[aq] MQ-135 warming up...");
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    WiFi.reconnect();
    delay(5000);
    return;
  }

  JsonDocument doc;
  JsonArray readings = doc.to<JsonArray>();

  // PMS5003
  PMSData pms;
  // Try reading for up to 2 seconds
  unsigned long start = millis();
  while (millis() - start < 2000) {
    if (readPMS(&pms)) {
      pms_ok = true;
      break;
    }
    delay(50);
  }

  if (pms_ok) {
    JsonObject r1 = readings.add<JsonObject>();
    r1["node_id"] = "aq-01";
    r1["sensor_type"] = "pm25";
    r1["value"] = pms.pm25_env;
    r1["unit"] = "ug/m3";
    r1["latitude"] = LATITUDE;
    r1["longitude"] = LONGITUDE;
    r1["quality"] = pms.pm25_env < 500 ? "good" : "degraded";

    JsonObject r2 = readings.add<JsonObject>();
    r2["node_id"] = "aq-01";
    r2["sensor_type"] = "pm10";
    r2["value"] = pms.pm100_env;
    r2["unit"] = "ug/m3";
    r2["latitude"] = LATITUDE;
    r2["longitude"] = LONGITUDE;
    r2["quality"] = "good";

    Serial.printf("[aq] PM2.5=%d PM10=%d ug/m3", pms.pm25_env, pms.pm100_env);
  }

  // MQ-135 (gas/VOC)
  int gasRaw = analogRead(MQ135_PIN);
  float gasPpm = map(gasRaw, 0, 4095, 0, 1000);  // Rough PPM estimate

  JsonObject r3 = readings.add<JsonObject>();
  r3["node_id"] = "aq-01";
  r3["sensor_type"] = "gas";
  r3["value"] = gasPpm;
  r3["unit"] = "ppm";
  r3["latitude"] = LATITUDE;
  r3["longitude"] = LONGITUDE;
  r3["quality"] = gasPpm < 800 ? "good" : "degraded";

  Serial.printf(" Gas=%dppm (raw=%d)\n", (int)gasPpm, gasRaw);

  // Send
  if (readings.size() > 0) {
    String body;
    serializeJson(readings, body);

    HTTPClient http;
    http.begin(String(DUSKFALL_URL) + "/api/contribute/sensor");
    http.addHeader("Content-Type", "application/json");
    http.addHeader("X-Device-Key", API_KEY);

    int code = http.POST(body);
    Serial.printf("[aq] -> %d\n", code);

    if (code == 200) {
      digitalWrite(LED_PIN, HIGH);
      delay(100);
      digitalWrite(LED_PIN, LOW);
    }
    http.end();
  }

  delay(REPORT_INTERVAL_S * 1000);
}
