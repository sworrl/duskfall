# Duskfall Sensor Firmware

Arduino/ESP32 firmware for IoT sensor nodes that report to the Duskfall backend.

## Supported Boards

| Board | Use Case | Notes |
|-------|----------|-------|
| ESP32 DevKit | General sensors | WiFi built-in, ADC, plenty of GPIO |
| ESP32-S3 | Camera + sensors | USB-OTG, more RAM for image processing |
| Arduino Nano 33 IoT | Low power sensors | WiFi + BLE, small form factor |
| ESP8266 (D1 Mini) | Budget sensors | WiFi, limited ADC |
| Raspberry Pi Pico W | Complex sensors | WiFi, dual-core, good ADC |

## Sensor Modules

| Sensor | Module | Type | Typical Pin |
|--------|--------|------|-------------|
| Temperature/Humidity | DHT22 / BME280 | temperature, humidity | GPIO 4 / I2C |
| Air Quality | MQ-135 / PMS5003 | air_quality, gas | ADC / UART |
| Radiation | SBM-20 + RadBoard | radiation | Interrupt |
| Motion | PIR HC-SR501 | motion | Digital |
| Sound | MAX4466 / INMP441 | sound_level | ADC / I2S |
| Light | BH1750 / LDR | light | I2C / ADC |
| Wind | Anemometer | wind_speed | Interrupt |
| Soil | Capacitive sensor | soil_moisture | ADC |
| Gas | MQ-2 / MQ-7 | gas | ADC |
| Vibration | ADXL345 / SW-420 | vibration | I2C / Digital |

## Setup

1. Register your node with Duskfall:
   ```bash
   curl -X POST "http://duskfall:8500/api/sensors/register?node_id=esp32-01&name=Backyard&node_type=esp32&lat=39.8&lon=-98.5&capabilities=[\"temperature\",\"humidity\",\"radiation\"]"
   ```

2. Copy the returned `api_key` into your firmware's `DUSKFALL_API_KEY`

3. Set WiFi credentials and Duskfall URL in the firmware

4. Flash via Arduino IDE or PlatformIO

## Available Firmware

- **esp32_sensor/** — General multi-sensor node (DHT22 + MQ-135 + PIR)
- **esp32_geiger/** — Dedicated Geiger counter node (SBM-20 tube)

## Data Flow

```
ESP32 Sensor -> HTTP POST /api/sensors/batch -> Duskfall Backend -> Map Layer
                     (every 60s)                  (PostGIS)         (Leaflet)
```

## Power Options

- USB (5V) for permanent installations
- 18650 Li-ion + solar panel for remote nodes
- Deep sleep mode between readings for battery life (modify firmware loop)
