// Board bring-up blink test.
//
// ESP32-S3 dev boards drive the onboard addressable RGB LED on GPIO 48.
// The classic ESP32 DevKitC (WROOM-32) has no RGB LED, so it toggles a
// plain digital pin on GPIO 2 instead. Both print to serial, so the board
// can be confirmed alive even when no LED is fitted.

#if CONFIG_IDF_TARGET_ESP32S3

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

#else

#define LED_PIN 2   // Classic ESP32 DevKitC user LED

int blinkCounter = 0;

void setup() {
    Serial.begin(9600);
    delay(500);

    pinMode(LED_PIN, OUTPUT);

    Serial.println();
    Serial.println("Blink test");
    Serial.println("==========");
    Serial.print("LED pin: GPIO ");
    Serial.println(LED_PIN);
    Serial.println();
}

void loop() {
    blinkCounter++;

    digitalWrite(LED_PIN, HIGH);
    Serial.print("Blink #");
    Serial.print(blinkCounter);
    Serial.println(" - ON");
    delay(500);

    digitalWrite(LED_PIN, LOW);
    Serial.println("           OFF");
    delay(500);
}

#endif
