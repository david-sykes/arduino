
#include <DHT.h>
#include <OneWire.h>
#include <DallasTemperature.h>

#define DHT_PIN 17
#define DHT_TYPE DHT22
#define ONE_WIRE_BUS_1 7
#define ONE_WIRE_BUS_2 9
#define TEMPERATURE_PRECISION 12

OneWire oneWire1(ONE_WIRE_BUS_1);
OneWire oneWire2(ONE_WIRE_BUS_2);
DallasTemperature sensors1(&oneWire1);
DallasTemperature sensors2(&oneWire2);


DHT dht(DHT_PIN, DHT_TYPE);

int measurementCounter = 0;

void setup() {
    Serial.begin(9600);
    dht.begin();
    sensors1.begin();
    sensors2.begin();
    sensors1.setResolution(TEMPERATURE_PRECISION);
    sensors2.setResolution(TEMPERATURE_PRECISION);

    Serial.println("DHT22 Temperature and Humidity Sensor Test");
    Serial.println("==========================================");
    Serial.println();
}

void loop() {
    measurementCounter++;
    
    float temperature = dht.readTemperature();
    float humidity = dht.readHumidity();

    sensors1.requestTemperatures();
    float temp1C = sensors1.getTempCByIndex(0);

    sensors2.requestTemperatures();
    float temp2C = sensors2.getTempCByIndex(0);

    
    Serial.print("Reading #");
    Serial.print(measurementCounter);
    Serial.print(": ");
    
    if (isnan(temperature) || isnan(humidity)) {
        Serial.println("Error - Failed to read from DHT22 sensor");
    } else {
        Serial.print("Temperature (DHT22): ");
        Serial.print(temperature);
        Serial.print("°C | Humidity (DHT22): ");
        Serial.print(humidity);
        Serial.print("% | ");
    }

    Serial.print("Temperature (DS18B20) Pin 7: ");
    if (temp1C != DEVICE_DISCONNECTED_C) {
        
        Serial.print(temp1C);
        Serial.print("°C | ");
  
    } else {
        Serial.print("Error - disconnected");
    }
    
    Serial.print("Temperature (DS18B20) Pin 9: ");
    if (temp2C != DEVICE_DISCONNECTED_C) {
        
        Serial.print(temp2C);
        Serial.println("°C");
    } else {
        Serial.println("Error - disconnected");
    }
    
    delay(2000);
}
