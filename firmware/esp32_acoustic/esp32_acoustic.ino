/*
 * Duskfall Acoustic Monitor — INMP441 I2S Microphone
 *
 * Continuously samples audio, computes dBA level, detects impulse events
 * (gunshots, explosions) via amplitude spike detection.
 *
 * Wiring (I2S):
 *   INMP441:  WS=GPIO 25, SCK=GPIO 26, SD=GPIO 33, VDD=3.3V, GND, L/R=GND
 *   LED:      GPIO 2
 *
 * Libraries: WiFi, HTTPClient, ArduinoJson, driver/i2s
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <driver/i2s.h>
#include <math.h>

// ── Configuration ──
const char* WIFI_SSID     = "YOUR_SSID";
const char* WIFI_PASS     = "YOUR_PASSWORD";
const char* DUSKFALL_URL  = "http://YOUR_SERVER:8500";
const char* API_KEY       = "df_YOUR_DEVICE_API_KEY";

const float LATITUDE      = 0.0;
const float LONGITUDE     = 0.0;
const int   REPORT_INTERVAL_S = 30;

// ── I2S pins ──
#define I2S_WS   25
#define I2S_SCK  26
#define I2S_SD   33
#define LED_PIN  2

// ── Audio config ──
#define SAMPLE_RATE    16000
#define SAMPLE_BITS    32
#define BLOCK_SIZE     512
#define IMPULSE_THRESHOLD_DB 85.0  // dBA spike threshold for gunshot/explosion

// ── State ──
float currentDbA = 0;
float maxDbA = 0;
int impulseCount = 0;
unsigned long lastReport = 0;

void setupI2S() {
  i2s_config_t config = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
    .sample_rate = SAMPLE_RATE,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 4,
    .dma_buf_len = BLOCK_SIZE,
    .use_apll = false,
    .tx_desc_auto_clear = false,
    .fixed_mclk = 0
  };

  i2s_pin_config_t pins = {
    .bck_io_num = I2S_SCK,
    .ws_io_num = I2S_WS,
    .data_out_num = I2S_PIN_NO_CHANGE,
    .data_in_num = I2S_SD
  };

  i2s_driver_install(I2S_NUM_0, &config, 0, NULL);
  i2s_set_pin(I2S_NUM_0, &pins);
}

float readDbA() {
  int32_t samples[BLOCK_SIZE];
  size_t bytesRead = 0;

  i2s_read(I2S_NUM_0, samples, sizeof(samples), &bytesRead, portMAX_DELAY);
  int sampleCount = bytesRead / sizeof(int32_t);

  if (sampleCount == 0) return 0;

  // RMS calculation
  double sumSquares = 0;
  for (int i = 0; i < sampleCount; i++) {
    double s = (double)(samples[i] >> 8);  // Shift to 24-bit
    sumSquares += s * s;
  }
  double rms = sqrt(sumSquares / sampleCount);

  // Convert to approximate dBA (calibration-dependent)
  // Reference: INMP441 sensitivity ~ -26 dBFS at 94 dB SPL
  if (rms < 1) return 20.0;  // Noise floor
  float db = 20.0 * log10(rms) - 100.0 + 94.0;  // Rough calibration
  return constrain(db, 20.0, 140.0);
}

void sendAlert(const char* eventType, const char* severity, float value) {
  if (WiFi.status() != WL_CONNECTED) return;

  JsonDocument doc;
  JsonArray arr = doc.to<JsonArray>();
  JsonObject ev = arr.add<JsonObject>();
  ev["uid"] = String("acoustic-") + millis();
  ev["feed_type"] = "sensor_alert";
  ev["title"] = String("[ACOUSTIC] ") + eventType;
  ev["description"] = String("Sound level: ") + value + " dBA";
  ev["latitude"] = LATITUDE;
  ev["longitude"] = LONGITUDE;
  ev["severity"] = severity;

  String body;
  serializeJson(arr, body);

  HTTPClient http;
  http.begin(String(DUSKFALL_URL) + "/api/contribute/feed");
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Key", API_KEY);
  http.POST(body);
  http.end();
}

void sendReadings() {
  if (WiFi.status() != WL_CONNECTED) return;

  JsonDocument doc;
  JsonArray readings = doc.to<JsonArray>();

  JsonObject r1 = readings.add<JsonObject>();
  r1["node_id"] = "acoustic-01";
  r1["sensor_type"] = "sound_level";
  r1["value"] = currentDbA;
  r1["unit"] = "dBA";
  r1["latitude"] = LATITUDE;
  r1["longitude"] = LONGITUDE;
  r1["quality"] = "good";

  JsonObject r2 = readings.add<JsonObject>();
  r2["node_id"] = "acoustic-01";
  r2["sensor_type"] = "sound_peak";
  r2["value"] = maxDbA;
  r2["unit"] = "dBA";
  r2["latitude"] = LATITUDE;
  r2["longitude"] = LONGITUDE;
  r2["quality"] = "good";

  String body;
  serializeJson(readings, body);

  HTTPClient http;
  http.begin(String(DUSKFALL_URL) + "/api/contribute/sensor");
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Key", API_KEY);

  int code = http.POST(body);
  Serial.printf("[acoustic] dBA=%.1f peak=%.1f impulses=%d -> %d\n",
    currentDbA, maxDbA, impulseCount, code);

  if (code == 200) {
    digitalWrite(LED_PIN, HIGH);
    delay(50);
    digitalWrite(LED_PIN, LOW);
  }
  http.end();

  // Reset peaks
  maxDbA = 0;
  impulseCount = 0;
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);

  setupI2S();
  Serial.println("[acoustic] I2S microphone initialized");

  WiFi.begin(WIFI_SSID, WIFI_PASS);
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts++ < 30) {
    delay(500);
  }
  Serial.printf("[acoustic] WiFi %s\n",
    WiFi.status() == WL_CONNECTED ? "OK" : "FAIL");
}

void loop() {
  // Continuous sampling
  currentDbA = readDbA();

  if (currentDbA > maxDbA) {
    maxDbA = currentDbA;
  }

  // Impulse detection (gunshot/explosion)
  if (currentDbA > IMPULSE_THRESHOLD_DB) {
    impulseCount++;
    Serial.printf("[acoustic] IMPULSE DETECTED: %.1f dBA\n", currentDbA);
    sendAlert("impulse_detected", "critical", currentDbA);

    // Flash LED rapidly
    for (int i = 0; i < 5; i++) {
      digitalWrite(LED_PIN, HIGH);
      delay(50);
      digitalWrite(LED_PIN, LOW);
      delay(50);
    }
  }

  // Periodic report
  unsigned long now = millis();
  if (now - lastReport > (unsigned long)REPORT_INTERVAL_S * 1000) {
    lastReport = now;
    sendReadings();
  }

  delay(10);  // ~100 samples/sec effective
}
