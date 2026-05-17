MQTT_DIR := mqtt_server
MQTT_LOG := $(MQTT_DIR)/data/mqtt.jsonl

.PHONY: mqtt-up mqtt-down mqtt-logs mqtt-tail mqtt-status

mqtt-up:
	cd $(MQTT_DIR) && docker compose up -d

mqtt-down:
	cd $(MQTT_DIR) && docker compose down

mqtt-status:
	cd $(MQTT_DIR) && docker compose ps

mqtt-logs:
	cd $(MQTT_DIR) && docker compose logs -f mosq-logger

mqtt-tail:
	tail -f $(MQTT_LOG)
