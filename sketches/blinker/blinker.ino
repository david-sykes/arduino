
#include <Adafruit_NeoPixel.h>

#define RGB_BUILTIN  48   // Most ESP32-S3 dev boards use GPIO 48 for the onboard RGB
#define NUM_PIXELS   1    // Only one LED on board

Adafruit_NeoPixel pixels(NUM_PIXELS, RGB_BUILTIN, NEO_GRB + NEO_KHZ800);

void setup() {
    Serial.begin(9600);
    pixels.begin();
    pixels.show();
}

void loop() {
    Serial.println("Setting color to RED");
    pixels.setPixelColor(0, pixels.Color(255, 0, 0));
    pixels.show();
    delay(2000);
  
    Serial.println("Setting color to GREEN");
    pixels.setPixelColor(0, pixels.Color(0, 255, 0));
    pixels.show();
    delay(2000);
    
    Serial.println("Setting color to BLUE");
    pixels.setPixelColor(0, pixels.Color(0, 0, 255));
    pixels.show();
    delay(2000);
    
    Serial.println("Setting color to YELLOW");
    pixels.setPixelColor(0, pixels.Color(255, 255, 0));
    pixels.show();
    delay(2000);
    
    Serial.println("Setting color to PURPLE");
    pixels.setPixelColor(0, pixels.Color(128, 0, 128));
    pixels.show();
    delay(2000);
    
    Serial.println("Setting color to CYAN");
    pixels.setPixelColor(0, pixels.Color(0, 255, 255));
    pixels.show();
    delay(2000);
}
