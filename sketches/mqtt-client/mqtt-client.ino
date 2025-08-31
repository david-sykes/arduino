
#include <WiFi.h>
#include <PubSubClient.h>
#include <secrets.h>

WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);

String clientId;
unsigned long lastPub = 0;

const char* TOPIC_HELLO  = "esp32s3/hello";
const char* TOPIC_STATUS = "esp32s3/status"; // retained online/offline

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
  // LWT is optional; helpful to know if device dropped
  bool ok = mqtt.connect(
    clientId.c_str(),
    TOPIC_STATUS, 1, true, "offline"
  );
  if (ok) {
    mqtt.publish(TOPIC_STATUS, "online", true); // retained
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
  if (now - lastAttempt < 2000) return; // simple 2s backoff
  lastAttempt = now;
  mqttConnect();
}

void setup() {
  Serial.begin(9600);
  delay(200);

  // Unique-ish client id
  uint8_t mac[6]; WiFi.macAddress(mac);
  char buf[64];
  snprintf(buf, sizeof(buf), "esp32s3-%02X%02X%02X", mac[3], mac[4], mac[5]);
  clientId = String(buf);

  ensureWiFi();
  mqtt.setBufferSize(512);
  ensureMqtt();
}

void loop() {
  ensureWiFi();
  ensureMqtt();
  if (mqtt.connected()) mqtt.loop();

  unsigned long now = millis();
  if (mqtt.connected() && now - lastPub > 5000) {
    lastPub = now;
    char payload[160];
    snprintf(payload, sizeof(payload),
      "{\"id\":\"%s\",\"rssi\":%d,\"heap\":%u,\"uptime_ms\":%lu}",
      clientId.c_str(), WiFi.RSSI(), (unsigned)ESP.getFreeHeap(), now);
    bool ok = mqtt.publish(TOPIC_HELLO, payload, false);
    Serial.printf("[MQTT] TX %s -> %s (%s)\n",
                  TOPIC_HELLO, payload, ok ? "ok" : "fail");
  }
}
