// CT clamp reader: ESP32-DevKitC-32E + ADS1115 over I2C.
//
// Clamp is an SCT-013-100, 100 A to 1 V, burden resistor already fitted
// inside the plug. Its output is AC and swings both sides of zero, so the
// pair is biased to half the supply to keep both ADS1115 inputs above
// ground.
//
// Wiring:
//   3.3V --[10k]--+--[10k]-- GND      10 uF from the midpoint to GND
//                 |
//                 +-- A1 and one leg of the CT
//   A0 -------------- other leg of the CT
//
// A0 minus A1 reads the clamp output. Noise on the bias rail lands on both
// inputs equally and cancels in the difference.
//
// The ADS1115 holds one conversion at a time and overwrites it when the
// next finishes, so reads are paced to the conversion rate.

#include <Wire.h>
#include <Adafruit_ADS1X15.h>

#define I2C_SDA 21
#define I2C_SCL 22
#define ADS_ADDR 0x48

#define SAMPLE_INTERVAL_US 1200  // A conversion at 860 SPS takes ~1163 us
#define WINDOW_MS 200            // 10 whole cycles of 50 Hz mains
#define BIAS_WINDOW_MS 40

#define AMPS_PER_VOLT 100.0      // SCT-013-100: 1 V out at 100 A through the jaws
#define MAINS_VOLTAGE 240.0      // Only used for the rough watts figure

// Below this the reading is chip noise rather than current.
#define NOISE_FLOOR_AMPS 0.05

// Send 'r' over serial to dump a raw capture instead of a summary. Samples
// are buffered first and printed afterwards, because 9600 baud is nowhere
// near fast enough to print while sampling at 833 a second.
#define RAW_SAMPLES 512

Adafruit_ADS1115 ads;
bool adsReady = false;
float voltsPerCount = 0;
int readingCounter = 0;

int16_t rawCounts[RAW_SAMPLES];
uint32_t rawMicros[RAW_SAMPLES];

struct Stats {
    long n;
    double meanCounts;
    double acRmsCounts;
    long peakToPeakCounts;
};

void scanI2C() {
    int found = 0;

    Serial.println("Scanning I2C bus...");
    for (uint8_t addr = 1; addr < 127; addr++) {
        Wire.beginTransmission(addr);
        if (Wire.endTransmission() == 0) {
            Serial.print("  Device at 0x");
            if (addr < 16) Serial.print("0");
            Serial.print(addr, HEX);
            if (addr >= 0x48 && addr <= 0x4B) {
                Serial.print("  (ADS1115 address range)");
            }
            Serial.println();
            found++;
        }
    }

    if (found == 0) {
        Serial.println("  Nothing responded. Check wiring, power and pull-ups.");
    }
    Serial.println();
}

Stats sampleMux(uint16_t mux, unsigned long windowMs) {
    Stats s = {0, 0, 0, 0};

    ads.startADCReading(mux, true);

    // A mux change only takes effect on the following conversion, so bin
    // the first two results before trusting anything.
    delayMicroseconds(SAMPLE_INTERVAL_US * 2);
    ads.getLastConversionResults();
    delayMicroseconds(SAMPLE_INTERVAL_US);
    ads.getLastConversionResults();

    int16_t minCounts = 32767;
    int16_t maxCounts = -32768;
    double sum = 0;
    double sumSq = 0;
    long n = 0;

    unsigned long startMs = millis();
    unsigned long nextUs = micros();

    while (millis() - startMs < windowMs) {
        while ((long)(micros() - nextUs) < 0) {
            // Wait out the conversion so we don't re-read the same value.
        }
        nextUs += SAMPLE_INTERVAL_US;

        int16_t counts = ads.getLastConversionResults();
        if (counts < minCounts) minCounts = counts;
        if (counts > maxCounts) maxCounts = counts;
        sum += counts;
        sumSq += (double)counts * (double)counts;
        n++;
    }

    if (n == 0) return s;

    double mean = sum / n;
    double variance = (sumSq / n) - (mean * mean);
    if (variance < 0) variance = 0;

    s.n = n;
    s.meanCounts = mean;
    s.acRmsCounts = sqrt(variance);
    s.peakToPeakCounts = (long)maxCounts - (long)minCounts;
    return s;
}

void captureRaw() {
    ads.startADCReading(ADS1X15_REG_CONFIG_MUX_DIFF_0_1, true);

    delayMicroseconds(SAMPLE_INTERVAL_US * 2);
    ads.getLastConversionResults();
    delayMicroseconds(SAMPLE_INTERVAL_US);
    ads.getLastConversionResults();

    uint32_t startUs = micros();
    uint32_t nextUs = startUs;

    for (int i = 0; i < RAW_SAMPLES; i++) {
        while ((long)(micros() - nextUs) < 0) {
            // Wait out the conversion so we don't re-read the same value.
        }
        nextUs += SAMPLE_INTERVAL_US;
        rawMicros[i] = micros() - startUs;
        rawCounts[i] = ads.getLastConversionResults();
    }

    Serial.println("#RAW_BEGIN");
    Serial.print("#samples=");         Serial.println(RAW_SAMPLES);
    Serial.print("#interval_us=");     Serial.println(SAMPLE_INTERVAL_US);
    Serial.print("#volts_per_count="); Serial.println(voltsPerCount, 9);
    Serial.print("#amps_per_volt=");   Serial.println(AMPS_PER_VOLT, 2);
    Serial.print("#mains_voltage=");   Serial.println(MAINS_VOLTAGE, 1);
    Serial.println("micros,counts");

    for (int i = 0; i < RAW_SAMPLES; i++) {
        Serial.print(rawMicros[i]);
        Serial.print(",");
        Serial.println(rawCounts[i]);
    }

    Serial.println("#RAW_END");
}

void setup() {
    Serial.begin(9600);
    delay(500);

    Serial.println();
    Serial.println("CT clamp reader - SCT-013-100 via ADS1115");
    Serial.println("=========================================");
    Serial.print("I2C: SDA=GPIO ");
    Serial.print(I2C_SDA);
    Serial.print("  SCL=GPIO ");
    Serial.println(I2C_SCL);
    Serial.println();

    Wire.begin(I2C_SDA, I2C_SCL);
    Wire.setClock(400000);

    scanI2C();

    if (!ads.begin(ADS_ADDR, &Wire)) {
        Serial.println("ADS1115 did not answer. Halting.");
        return;
    }

    // +/- 2.048 V covers the clamp's 1.41 V peak at its full 100 A rating.
    // Drop to GAIN_FOUR for finer resolution if nothing above ~70 A matters.
    ads.setGain(GAIN_TWO);
    ads.setDataRate(RATE_ADS1115_860SPS);
    voltsPerCount = ads.computeVolts(1);
    adsReady = true;

    Serial.println("ADS1115 ready.");
    Serial.print("  Range: +/- 2.048 V   Resolution: ");
    Serial.print(voltsPerCount * 1000000.0, 1);
    Serial.println(" uV per count");
    Serial.print("  Rate: 860 SPS   Window: ");
    Serial.print(WINDOW_MS);
    Serial.println(" ms (10 cycles at 50 Hz)");
    Serial.println();
    Serial.println("Bias should read close to 1650 mV. If it drifts or swings,");
    Serial.println("the divider is not connected and the current figure is junk.");
    Serial.print("Send 'r' for a raw ");
    Serial.print(RAW_SAMPLES);
    Serial.println("-sample waveform dump.");
    Serial.println();
}

void loop() {
    if (!adsReady) {
        delay(5000);
        return;
    }

    if (Serial.available()) {
        int c = Serial.read();
        if (c == 'r' || c == 'R') {
            captureRaw();
            return;
        }
    }

    readingCounter++;

    Stats bias = sampleMux(ADS1X15_REG_CONFIG_MUX_SINGLE_1, BIAS_WINDOW_MS);
    Stats ct = sampleMux(ADS1X15_REG_CONFIG_MUX_DIFF_0_1, WINDOW_MS);

    double biasMv = bias.meanCounts * voltsPerCount * 1000.0;
    double biasSwingMv = bias.acRmsCounts * voltsPerCount * 1000.0;
    double ctRmsVolts = ct.acRmsCounts * voltsPerCount;
    double amps = ctRmsVolts * AMPS_PER_VOLT;

    Serial.print("Reading #");
    Serial.println(readingCounter);

    Serial.print("  Bias:    ");
    Serial.print(biasMv, 1);
    Serial.print(" mV (drift ");
    Serial.print(biasSwingMv, 1);
    Serial.print(" mV rms)");
    if (biasMv < 1400 || biasMv > 1900 || biasSwingMv > 50) {
        Serial.print("   <-- CHECK THE DIVIDER");
    }
    Serial.println();

    Serial.print("  Clamp:   ");
    Serial.print(ctRmsVolts * 1000.0, 3);
    Serial.print(" mV rms   p-p=");
    Serial.print(ct.peakToPeakCounts);
    Serial.print(" counts   n=");
    Serial.println(ct.n);

    Serial.print("  Current: ");
    if (amps < NOISE_FLOOR_AMPS) {
        Serial.print("below noise floor (< ");
        Serial.print(NOISE_FLOOR_AMPS, 2);
        Serial.println(" A)");
    } else {
        Serial.print(amps, 2);
        Serial.print(" A   approx ");
        Serial.print(amps * MAINS_VOLTAGE, 0);
        Serial.print(" W at ");
        Serial.print(MAINS_VOLTAGE, 0);
        Serial.println(" V, unity power factor assumed");
    }

    Serial.println();
    delay(1000);
}
