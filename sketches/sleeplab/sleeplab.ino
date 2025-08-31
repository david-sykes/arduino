
#include <OneWire.h>
#include <DallasTemperature.h>
#include <DHT.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <secrets.h>

#define DHT_PIN 17
#define DHT_TYPE DHT22
#define ONE_WIRE_BUS_1 9
#define ONE_WIRE_BUS_2 7
#define TEMPERATURE_PRECISION 12

OneWire oneWire1(ONE_WIRE_BUS_1);
OneWire oneWire2(ONE_WIRE_BUS_2);
DallasTemperature sensors1(&oneWire1);
DallasTemperature sensors2(&oneWire2);

DHT dht(DHT_PIN, DHT_TYPE);

WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);

String clientId;
unsigned long lastPub = 0;
int measurementCounter = 0;

const char* TOPIC_SENSORS = "sleeplab/sensors";

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
  Serial.printf("\n[WiFi] Connected. IP=%s\n",
                WiFi.localIP().toString().c_str());
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
    
    // Initialize sensors
    dht.begin();
    sensors1.begin();
    sensors2.begin();
    sensors1.setResolution(TEMPERATURE_PRECISION);
    sensors2.setResolution(TEMPERATURE_PRECISION);
    
    // Setup unique client ID
    uint8_t mac[6]; WiFi.macAddress(mac);
    char buf[64];
    snprintf(buf, sizeof(buf), "sleeplab-%02X%02X%02X", mac[3], mac[4], mac[5]);
    clientId = String(buf);
    
    // Connect to WiFi and MQTT
    ensureWiFi();
    mqtt.setBufferSize(512);
    ensureMqtt();
    
    Serial.println("Sleeplab Sensor Monitor - DHT22 + DS18B20");
    Serial.println("==========================================");
    Serial.print("DHT22 on pin: ");
    Serial.println(DHT_PIN);
    Serial.print("DS18B20 pin 9 sensors: ");
    Serial.println(sensors1.getDeviceCount());
    Serial.print("DS18B20 pin 7 sensors: ");
    Serial.println(sensors2.getDeviceCount());
    Serial.println();
}

void loop() {
    ensureWiFi();
    ensureMqtt();
    if (mqtt.connected()) mqtt.loop();
    
    unsigned long now = millis();
    if (mqtt.connected() && now - lastPub > 30000) {
        lastPub = now;
        measurementCounter++;
        
        // Read all sensors
        float dhtTemp = dht.readTemperature();
        float dhtHumidity = dht.readHumidity();
        
        sensors1.requestTemperatures();
        float temp1C = sensors1.getTempCByIndex(0);
        
        sensors2.requestTemperatures();
        float temp2C = sensors2.getTempCByIndex(0);
        
        // Create JSON payload
        char payload[256];
        snprintf(payload, sizeof(payload),
            "{\"id\":\"%s\",\"measurement\":%d,\"dht22_temp\":%.1f,\"dht22_humidity\":%.1f,\"ds18b20_pin7\":%.1f,\"ds18b20_pin9\":%.1f}",
            clientId.c_str(), measurementCounter,
            isnan(dhtTemp) ? -999.0 : dhtTemp,
            isnan(dhtHumidity) ? -999.0 : dhtHumidity,
            (temp2C == DEVICE_DISCONNECTED_C) ? -999.0 : temp2C,
            (temp1C == DEVICE_DISCONNECTED_C) ? -999.0 : temp1C);
        
        // Publish to MQTT
        bool ok = mqtt.publish(TOPIC_SENSORS, payload, false);
        Serial.printf("[MQTT] TX %s -> %s (%s)\n",
                      TOPIC_SENSORS, payload, ok ? "ok" : "fail");
    }
}
