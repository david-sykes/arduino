
#include <OneWire.h>
#include <DallasTemperature.h>
#include <Adafruit_NeoPixel.h>

#define ONE_WIRE_BUS 9
#define TEMPERATURE_PRECISION 12
#define RGB_BUILTIN 48

OneWire oneWire(ONE_WIRE_BUS);
DallasTemperature sensors(&oneWire);
Adafruit_NeoPixel pixel(1, RGB_BUILTIN, NEO_GRB + NEO_KHZ800);

int measurementCounter = 0;
int deviceCount = 0;

void printAddress(DeviceAddress addr) {
    for (int i = 0; i < 8; i++) {
        if (addr[i] < 16) Serial.print("0");
        Serial.print(addr[i], HEX);
    }
}

void setup() {
    Serial.begin(9600);

    pixel.begin();
    pixel.clear();
    pixel.show();

    sensors.begin();
    deviceCount = sensors.getDeviceCount();

    Serial.println("DS18B20 Temperature Sensors - Pin 9");
    Serial.println("====================================");
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
}

void loop() {
    measurementCounter++;

    sensors.requestTemperatures();

    Serial.print("Reading #");
    Serial.println(measurementCounter);

    for (int i = 0; i < deviceCount; i++) {
        DeviceAddress addr;
        if (sensors.getAddress(addr, i)) {
            float tempC = sensors.getTempC(addr);
            Serial.print("  Sensor ");
            Serial.print(i);
            Serial.print(" [");
            printAddress(addr);
            Serial.print("]: ");
            if (tempC != DEVICE_DISCONNECTED_C) {
                Serial.print(tempC);
                Serial.println("°C");
            } else {
                Serial.println("Error - disconnected");
            }
        }
    }

    delay(30000);
}
