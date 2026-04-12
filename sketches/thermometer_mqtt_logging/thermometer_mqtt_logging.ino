
#include <OneWire.h>
#include <DallasTemperature.h>
#include <Adafruit_NeoPixel.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <secrets.h>

#define ONE_WIRE_BUS 9
#define TEMPERATURE_PRECISION 12
#define RGB_BUILTIN 48
#define PUBLISH_INTERVAL 30000

OneWire oneWire(ONE_WIRE_BUS);
DallasTemperature sensors(&oneWire);
Adafruit_NeoPixel pixel(1, RGB_BUILTIN, NEO_GRB + NEO_KHZ800);

WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);

String clientId;
unsigned long lastPub = 0;
int measurementCounter = 0;
int deviceCount = 0;

const char* TOPIC_SENSORS = "thermometer/sensors";

void printAddress(DeviceAddress addr) {
    for (int i = 0; i < 8; i++) {
        if (addr[i] < 16) Serial.print("0");
        Serial.print(addr[i], HEX);
    }
}

void sprintAddress(char* buf, DeviceAddress addr) {
    for (int i = 0; i < 8; i++) {
        sprintf(buf + i * 2, "%02X", addr[i]);
    }
}

void ensureWiFi() {
    if (WiFi.status() == WL_CONNECTED) return;
    Serial.printf("[WiFi] Connecting to %s ...\n", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    uint32_t start = millis();
    while (WiFi.status() != WL_CONNECTED) {
        delay(300);
        Serial.print(".");
        if (millis() - start > 15000) {
            Serial.println("\n[WiFi] Retry...");
            WiFi.disconnect(true, true);
            delay(1000);
            WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
            start = millis();
        }
    }
    Serial.printf("\n[WiFi] Connected. IP=%s RSSI=%d dBm\n",
                  WiFi.localIP().toString().c_str(), WiFi.RSSI());
}

bool mqttConnect() {
    mqtt.setServer(MQTT_HOST, MQTT_PORT);
    bool ok = mqtt.connect(clientId.c_str());
    if (ok) {
        Serial.println("[MQTT] Connected");
    } else {
        Serial.printf("[MQTT] Connect failed, state=%d\n", mqtt.state());
    }
    return ok;
}

void ensureMqtt() {
    if (mqtt.connected()) return;
    static uint32_t lastAttempt = 0;
    uint32_t now = millis();
    if (now - lastAttempt < 2000) return;
    lastAttempt = now;
    mqttConnect();
}

void setup() {
    Serial.begin(9600);
    delay(200);

    pixel.begin();
    pixel.clear();
    pixel.show();

    sensors.begin();
    deviceCount = sensors.getDeviceCount();

    Serial.println("Thermometer MQTT Logging - DS18B20 on Pin 9");
    Serial.println("=============================================");
    Serial.print("Sensors found: ");
    Serial.println(deviceCount);

    for (int i = 0; i < deviceCount; i++) {
        DeviceAddress addr;
        if (sensors.getAddress(addr, i)) {
            sensors.setResolution(addr, TEMPERATURE_PRECISION);
            Serial.print("  Sensor ");
            Serial.print(i);
            Serial.print(": ");
            printAddress(addr);
            Serial.println();
        }
    }
    Serial.println();

    // Setup unique client ID
    uint8_t mac[6]; WiFi.macAddress(mac);
    char buf[64];
    snprintf(buf, sizeof(buf), "thermometer-%02X%02X%02X", mac[3], mac[4], mac[5]);
    clientId = String(buf);

    // Connect to WiFi and MQTT
    ensureWiFi();
    mqtt.setBufferSize(512);
    ensureMqtt();
}

void loop() {
    ensureWiFi();
    ensureMqtt();
    if (mqtt.connected()) mqtt.loop();

    unsigned long now = millis();
    if (now - lastPub < PUBLISH_INTERVAL) return;
    lastPub = now;
    measurementCounter++;

    sensors.requestTemperatures();

    Serial.print("Reading #");
    Serial.println(measurementCounter);

    // Build JSON payload with sensor array
    char payload[512];
    int pos = snprintf(payload, sizeof(payload),
        "{\"id\":\"%s\",\"measurement\":%d,\"sensors\":[",
        clientId.c_str(), measurementCounter);

    for (int i = 0; i < deviceCount; i++) {
        DeviceAddress addr;
        if (sensors.getAddress(addr, i)) {
            float tempC = sensors.getTempC(addr);
            char addrStr[17];
            sprintAddress(addrStr, addr);

            // Serial logging
            Serial.print("  Sensor ");
            Serial.print(i);
            Serial.print(" [");
            printAddress(addr);
            Serial.print("]: ");

            float reportTemp;
            if (tempC != DEVICE_DISCONNECTED_C) {
                reportTemp = tempC;
                Serial.print(tempC);
                Serial.println(" C");
            } else {
                reportTemp = -999.0;
                Serial.println("Error - disconnected");
            }

            if (i > 0) pos += snprintf(payload + pos, sizeof(payload) - pos, ",");
            pos += snprintf(payload + pos, sizeof(payload) - pos,
                "{\"addr\":\"%s\",\"temp\":%.1f}", addrStr, reportTemp);
        }
    }

    snprintf(payload + pos, sizeof(payload) - pos, "]}");

    if (mqtt.connected()) {
        bool ok = mqtt.publish(TOPIC_SENSORS, payload, false);
        Serial.printf("[MQTT] TX %s (%s)\n", TOPIC_SENSORS, ok ? "ok" : "fail");
    } else {
        Serial.println("[MQTT] Not connected, skipping publish");
    }
}
