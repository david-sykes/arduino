
#include <OneWire.h>
#include <DallasTemperature.h>

#define ONE_WIRE_BUS_1 9
#define ONE_WIRE_BUS_2 7
#define TEMPERATURE_PRECISION 12

OneWire oneWire1(ONE_WIRE_BUS_1);
OneWire oneWire2(ONE_WIRE_BUS_2);
DallasTemperature sensors1(&oneWire1);
DallasTemperature sensors2(&oneWire2);

int measurementCounter = 0;

void setup() {
    Serial.begin(9600);
    sensors1.begin();
    sensors2.begin();
    sensors1.setResolution(TEMPERATURE_PRECISION);
    sensors2.setResolution(TEMPERATURE_PRECISION);
    
    Serial.println("DS18B20 Temperature Sensor Test - Dual Sensors");
    Serial.println("===============================================");
    Serial.print("Pin 5 sensors: ");
    Serial.println(sensors1.getDeviceCount());
    Serial.print("Pin 7 sensors: ");
    Serial.println(sensors2.getDeviceCount());
    Serial.println();
}

void loop() {
    measurementCounter++;
    
    sensors1.requestTemperatures();
    sensors2.requestTemperatures();
    
    float temp1C = sensors1.getTempCByIndex(0);
    float temp1F = temp1C * 9.0 / 5.0 + 32.0;
    
    float temp2C = sensors2.getTempCByIndex(0);
    float temp2F = temp2C * 9.0 / 5.0 + 32.0;
    
    Serial.print("Reading #");
    Serial.print(measurementCounter);
    Serial.print(": Pin 9 Temp: ");
    if (temp1C != DEVICE_DISCONNECTED_C) {
        Serial.print(temp1C);
        Serial.print("°C");
    } else {
        Serial.print("Error - disconnected");
    }
    
    Serial.print(" | Pin 7 Temp: ");
    if (temp2C != DEVICE_DISCONNECTED_C) {
        Serial.print(temp2C);
        Serial.print("°C");
    } else {
        Serial.print("Error - disconnected");
    }
    Serial.println();
    
    delay(60000);
}
