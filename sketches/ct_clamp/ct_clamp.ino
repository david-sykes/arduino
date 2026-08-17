// =====================================================================
// CT clamp reader: ESP32-DevKitC-32E + ADS1115 over I2C
// =====================================================================
//
// Measures mains current with an SCT-013-100 clamp. The clamp puts out
// a voltage proportional to the current through its jaws, 1 V for every
// 100 A. That voltage is AC, swinging both sides of zero, so a resistor
// divider lifts the whole thing to sit at half the supply where the
// ADS1115 can read it.
//
// Wiring:
//   3.3V --[10k]--+--[10k]-- GND
//                 |
//                 +-- A1 and one leg of the CT
//   A0 -------------- other leg of the CT
//
// A0 minus A1 gives the clamp output on its own. Noise on the midpoint
// lands on both inputs at once and cancels in the subtraction.
//
// Full explanation of how all of this works: see README.md next to this
// file.
//
// Serial output at 9600 baud. Send 'r' for a raw waveform dump.
// =====================================================================


// ---------------------------------------------------------------------
// Libraries
// ---------------------------------------------------------------------

// Wire is the Arduino I2C driver. I2C is the two-wire bus the ESP32
// uses to talk to the ADS1115: one wire for a clock, one for data.
#include <Wire.h>

// Adafruit's driver for the ADS1115. It knows the chip's register
// layout so we can say "start reading" instead of assembling raw
// 16-bit config words by hand.
#include <Adafruit_ADS1X15.h>


// ---------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------
//
// #define is a text substitution done before the code is compiled.
// Everywhere the compiler sees I2C_SDA it literally writes 21 instead.
// There is no variable and no memory used.

#define I2C_SDA 21   // GPIO pin carrying the I2C data line
#define I2C_SCL 22   // GPIO pin carrying the I2C clock line

// The ADS1115's address on the I2C bus. Several devices can share the
// two wires, so each needs a unique address. 0x48 is what this chip
// uses when its ADDR pin is tied low, which is the module default.
#define ADS_ADDR 0x48

// How long to wait between reads, in microseconds.
//
// The ADS1115 is configured for 860 conversions per second, so one
// conversion takes about 1163 us. The chip has no queue: it holds one
// result and overwrites it when the next finishes. Read faster than it
// converts and you get the same number twice. 1200 us paces us just
// past the conversion time, giving an actual rate of about 833 a
// second.
#define SAMPLE_INTERVAL_US 1200

// How long to sample for when measuring current, in milliseconds.
//
// 200 ms is exactly 10 cycles of 50 Hz mains. Using whole cycles
// matters: a partial cycle at the end would weight part of the
// waveform more than the rest and skew the answer.
#define WINDOW_MS 200

// A shorter window for the bias check. The midpoint is meant to be a
// steady DC voltage, so there is no waveform to cover and no reason to
// spend 200 ms on it.
#define BIAS_WINDOW_MS 40

// The clamp's calibration constant. An SCT-013-100 produces 1 V at
// 100 A, so multiply its output voltage by 100 to get amps.
#define AMPS_PER_VOLT 100.0

// Assumed mains voltage, used only for the rough watts figure. This is
// an assumption, not a measurement. There is no voltage sensor in this
// circuit.
#define MAINS_VOLTAGE 240.0

// Readings below this are the converter's own noise rather than real
// current, so the sketch says so instead of printing a number that
// looks meaningful.
#define NOISE_FLOOR_AMPS 0.05

// How many samples a raw dump captures.
//
// Samples are stored in memory first and printed afterwards. They have
// to be, because 9600 baud carries about 960 characters a second while
// sampling produces roughly 10,000 characters a second of output.
// Printing while sampling would stall the loop and wreck the timing.
#define RAW_SAMPLES 512


// ---------------------------------------------------------------------
// Global state
// ---------------------------------------------------------------------

// The driver object. Every call to the chip goes through this.
Adafruit_ADS1115 ads;

// Set true once the chip has answered on the bus. If it never does,
// loop() stays quiet rather than printing meaningless numbers.
bool adsReady = false;

// How many volts one ADC count represents. Filled in during setup()
// by asking the library, based on the gain setting. Everything
// downstream scales from this one number, so changing the gain
// automatically corrects the whole sketch.
float voltsPerCount = 0;

// Counts up so each printed reading has an identifying number.
int readingCounter = 0;

// Buffers for a raw dump.
//
// int16_t means a signed 16-bit integer, which is exactly what the
// ADS1115 produces: -32768 to +32767. uint32_t is an unsigned 32-bit
// integer, big enough for the microsecond timestamps.
//
// Together these use 3 KB, against the ESP32's 320 KB of RAM.
int16_t rawCounts[RAW_SAMPLES];
uint32_t rawMicros[RAW_SAMPLES];


// ---------------------------------------------------------------------
// Stats: a bundle of numbers describing one measurement window
// ---------------------------------------------------------------------
//
// A C function can only return one value. sampleMux() needs to hand
// back four, so we define a struct: a custom type that glues several
// values together under one name. Think of it as a named box with
// four labelled compartments.
//
// After this definition, Stats is usable as a type just like int or
// float, and s.acRmsCounts reads the compartment called acRmsCounts.

struct Stats {
    long n;                   // how many samples were taken
    double meanCounts;        // their average, the DC level
    double acRmsCounts;       // RMS after removing that average
    long peakToPeakCounts;    // largest sample minus smallest
};


// ---------------------------------------------------------------------
// scanI2C: report every device answering on the bus
// ---------------------------------------------------------------------
//
// Exists so a dead or miswired chip is obvious straight away, instead
// of showing up later as strange numbers.
//
// The trick: address every possible device in turn and see who
// answers. On I2C, a device whose address matches acknowledges by
// pulling the data line low. beginTransmission/endTransmission sends
// an address and reports whether anything did.

void scanI2C() {
    int found = 0;

    Serial.println("Scanning I2C bus...");

    // Addresses 0 and 127 are reserved, so scan 1 to 126.
    for (uint8_t addr = 1; addr < 127; addr++) {
        Wire.beginTransmission(addr);

        // Returns 0 when a device acknowledged, non-zero otherwise.
        if (Wire.endTransmission() == 0) {
            Serial.print("  Device at 0x");

            // Serial.print with HEX drops the leading zero, so
            // addresses under 0x10 need one added by hand.
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


// ---------------------------------------------------------------------
// sampleMux: sample one input for a while and summarise it
// ---------------------------------------------------------------------
//
// This is the heart of the sketch. Everything else is presentation.
//
// Parameters:
//   mux       which pins to measure. "mux" is short for multiplexer,
//             the bank of electronic switches inside the ADS1115 that
//             connects pins to the converter. The value is a bit
//             pattern the chip understands. We use two of them:
//               ADS1X15_REG_CONFIG_MUX_DIFF_0_1  -> A0 minus A1
//               ADS1X15_REG_CONFIG_MUX_SINGLE_1  -> A1 against ground
//   windowMs  how long to keep sampling, in milliseconds
//
// Returns a Stats describing what it saw.

Stats sampleMux(uint16_t mux, unsigned long windowMs) {
    // Start with everything zeroed, so an early return is still safe
    // to read.
    Stats s = {0, 0, 0, 0};

    // Tell the chip which pins to measure and to convert continuously
    // rather than once on request. Under the hood this writes a single
    // 16-bit word to the chip's config register carrying the mux
    // setting, the gain, the data rate and the mode all at once.
    ads.startADCReading(mux, true);

    // Throw away the first two results.
    //
    // A mux change only takes effect on the conversion *after* the one
    // already in progress, so the next result still reflects the old
    // pins. delayMicroseconds() waits the given number of microseconds
    // before continuing, doing nothing else in the meantime.
    delayMicroseconds(SAMPLE_INTERVAL_US * 2);
    ads.getLastConversionResults();
    delayMicroseconds(SAMPLE_INTERVAL_US);
    ads.getLastConversionResults();

    // Running totals. We never store the individual samples, only
    // enough summary to work out the answer at the end.
    //
    // min starts at the largest possible value and max at the smallest,
    // so the first real sample replaces both.
    int16_t minCounts = 32767;
    int16_t maxCounts = -32768;
    double sum = 0;      // running total of samples
    double sumSq = 0;    // running total of samples squared
    long n = 0;          // how many samples so far

    // millis() and micros() return how long the board has been running,
    // in milliseconds and microseconds. They are the standard Arduino
    // way of measuring elapsed time.
    unsigned long startMs = millis();
    unsigned long nextUs = micros();   // when the next sample is due

    while (millis() - startMs < windowMs) {

        // Busy-wait until the next sample is due. An empty loop body
        // is deliberate: we are just burning time until the chip has
        // finished converting.
        //
        // The cast to (long) handles counter rollover. micros() wraps
        // back to zero roughly every 71 minutes. The subtraction wraps
        // with it, and reading the result as a signed number gives the
        // correct offset either side of the wrap. Writing
        // "micros() < nextUs" instead would hang for 71 minutes every
        // time the counter rolled over.
        while ((long)(micros() - nextUs) < 0) {
            // Wait out the conversion so we don't re-read the same value.
        }

        // Advance the target by a fixed step rather than measuring from
        // now. Small delays then cannot accumulate across the window.
        nextUs += SAMPLE_INTERVAL_US;

        // Fetch the latest result. This reads the chip's conversion
        // register over I2C and returns a signed 16-bit count.
        int16_t counts = ads.getLastConversionResults();

        if (counts < minCounts) minCounts = counts;
        if (counts > maxCounts) maxCounts = counts;

        sum += counts;

        // Cast to double before multiplying. Two int16_t values
        // multiplied together would be computed as 32-bit integers and
        // could overflow; doing the maths in floating point avoids it.
        sumSq += (double)counts * (double)counts;

        n++;
    }

    // Guard against dividing by zero if somehow nothing was sampled.
    if (n == 0) return s;

    double mean = sum / n;

    // Variance in one pass, using the identity
    //     variance = mean(x squared) - mean(x) squared
    // The obvious method needs two passes: one to find the mean, then
    // another to total the squared distances from it. That would mean
    // keeping every sample. This gets the same answer from the two
    // running totals we already have.
    double variance = (sumSq / n) - (mean * mean);

    // Variance can never truly be negative, but floating point rounding
    // can produce a tiny negative value when the real variance is close
    // to zero, and sqrt() of that gives NaN.
    if (variance < 0) variance = 0;

    s.n = n;
    s.meanCounts = mean;

    // The square root of the variance is the RMS of the signal with its
    // DC level removed, which is the number we want. Statisticians call
    // the same quantity the standard deviation.
    s.acRmsCounts = sqrt(variance);

    // Cast to long before subtracting: two int16_t values could
    // overflow their type here, since the difference can exceed 32767.
    s.peakToPeakCounts = (long)maxCounts - (long)minCounts;

    return s;
}


// ---------------------------------------------------------------------
// captureRaw: dump individual samples as CSV
// ---------------------------------------------------------------------
//
// Same sampling loop as above, but keeping every sample instead of
// summarising. Triggered by sending 'r' over serial. The companion
// script analysis/ct_clamp/capture_waveform.py reads the output and
// plots it.

void captureRaw() {
    ads.startADCReading(ADS1X15_REG_CONFIG_MUX_DIFF_0_1, true);

    // Discard two results after the mux change, as in sampleMux.
    delayMicroseconds(SAMPLE_INTERVAL_US * 2);
    ads.getLastConversionResults();
    delayMicroseconds(SAMPLE_INTERVAL_US);
    ads.getLastConversionResults();

    uint32_t startUs = micros();
    uint32_t nextUs = startUs;

    // Phase one: collect. Nothing is printed here, because printing is
    // far too slow to keep up with sampling.
    for (int i = 0; i < RAW_SAMPLES; i++) {
        while ((long)(micros() - nextUs) < 0) {
            // Wait out the conversion so we don't re-read the same value.
        }
        nextUs += SAMPLE_INTERVAL_US;

        // Store when this sample was taken, relative to the start, so
        // the timing can be checked afterwards rather than assumed.
        rawMicros[i] = micros() - startUs;
        rawCounts[i] = ads.getLastConversionResults();
    }

    // Phase two: print. Takes about seven seconds at 9600 baud.
    //
    // The markers below let the Python script find this block in a
    // stream that also contains ordinary readings. It ignores
    // everything until #RAW_BEGIN and stops at #RAW_END.
    Serial.println("#RAW_BEGIN");

    // Send the calibration constants with the data, so the script never
    // has to hardcode values that live in this file.
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


// ---------------------------------------------------------------------
// setup: runs once at power-on or reset
// ---------------------------------------------------------------------

void setup() {
    // Open the serial port at 9600 bits per second. Both ends must
    // agree on this number or the text arrives as garbage.
    Serial.begin(9600);

    // Give the port a moment to come up before printing.
    delay(500);

    Serial.println();
    Serial.println("CT clamp reader - SCT-013-100 via ADS1115");
    Serial.println("=========================================");
    Serial.print("I2C: SDA=GPIO ");
    Serial.print(I2C_SDA);
    Serial.print("  SCL=GPIO ");
    Serial.println(I2C_SCL);
    Serial.println();

    // Start the I2C bus. The ESP32 can route I2C to almost any pin, so
    // unlike most Arduino boards the pins have to be stated.
    Wire.begin(I2C_SDA, I2C_SCL);

    // 400 kHz, known as fast mode. The default 100 kHz would work
    // equally well here; this just leaves more headroom.
    Wire.setClock(400000);

    scanI2C();

    // begin() returns false if nothing acknowledges at 0x48. Bailing
    // out here leaves adsReady false, so loop() stays quiet rather than
    // printing nonsense.
    if (!ads.begin(ADS_ADDR, &Wire)) {
        Serial.println("ADS1115 did not answer. Halting.");
        return;
    }

    // Set the amplifier gain, which decides the largest voltage the
    // chip can accept at its pins and therefore how fine each count is.
    //
    //   GAIN_TWOTHIRDS  +/- 6.144 V   187.5 uV per count
    //   GAIN_ONE        +/- 4.096 V   125 uV
    //   GAIN_TWO        +/- 2.048 V   62.5 uV     <-- in use
    //   GAIN_FOUR       +/- 1.024 V   31.25 uV
    //   GAIN_EIGHT      +/- 0.512 V   15.6 uV
    //   GAIN_SIXTEEN    +/- 0.256 V   7.8 uV
    //
    // +/- 2.048 V covers the clamp's 1.41 V peak at its full 100 A
    // rating. GAIN_EIGHT would clip at 36 A but resolve four times
    // finer, which suits watching a single appliance on a 13 A plug.
    ads.setGain(GAIN_TWO);

    // 860 conversions per second, the chip's fastest. Slower rates
    // average the internal bitstream over longer and are quieter, but
    // 860 is needed to trace a 50 Hz waveform properly: it gives about
    // 17 samples per cycle.
    ads.setDataRate(RATE_ADS1115_860SPS);

    // Ask the library what one count is worth at the gain just set.
    // Doing it this way rather than hardcoding 62.5 uV means changing
    // the gain above automatically corrects every calculation below.
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


// ---------------------------------------------------------------------
// loop: runs over and over, forever, after setup finishes
// ---------------------------------------------------------------------
//
// One pass takes about 1.5 seconds: 40 ms measuring the bias, 200 ms
// measuring the clamp, a moment printing, then a one second pause.

void loop() {
    // Nothing useful to do if the chip never answered.
    if (!adsReady) {
        delay(5000);
        return;
    }

    // Has the laptop asked for a raw dump?
    //
    // Serial.available() reports how many bytes have arrived and are
    // waiting to be read. It returns straight away either way, so this
    // is a glance rather than a wait, and the sketch carries on when
    // nothing has come in.
    //
    // Incoming bytes sit in a buffer until read, so a request can wait
    // up to a full pass of this loop before being noticed.
    if (Serial.available()) {
        // Serial.read() takes one byte. It is stored in an int rather
        // than a char because it returns -1 when the buffer is empty.
        int c = Serial.read();

        if (c == 'r' || c == 'R') {
            captureRaw();

            // Skip the normal reading this time round, so a seven
            // second dump is not immediately followed by a summary.
            return;
        }
    }

    readingCounter++;

    // Measure A1 against ground. This is the health check on the bias
    // divider: it should read about 1650 mV and barely move.
    Stats bias = sampleMux(ADS1X15_REG_CONFIG_MUX_SINGLE_1, BIAS_WINDOW_MS);

    // Measure A0 minus A1. This is the clamp output, and the number we
    // actually care about.
    Stats ct = sampleMux(ADS1X15_REG_CONFIG_MUX_DIFF_0_1, WINDOW_MS);

    // Turn counts into real units.
    //
    // The bias is a DC level, so its mean is the useful figure and its
    // AC part tells us how much it wobbles. The clamp is pure AC, so
    // its mean should be near zero and the AC part is the signal.
    double biasMv       = bias.meanCounts   * voltsPerCount * 1000.0;
    double biasSwingMv  = bias.acRmsCounts  * voltsPerCount * 1000.0;
    double ctRmsVolts   = ct.acRmsCounts    * voltsPerCount;
    double amps         = ctRmsVolts * AMPS_PER_VOLT;

    Serial.print("Reading #");
    Serial.println(readingCounter);

    // --- bias line ---
    Serial.print("  Bias:    ");
    Serial.print(biasMv, 1);          // the 1 means one decimal place
    Serial.print(" mV (drift ");
    Serial.print(biasSwingMv, 1);
    Serial.print(" mV rms)");

    // If the midpoint is not where it should be, the current figure
    // below it means nothing, so say so rather than let it mislead.
    if (biasMv < 1400 || biasMv > 1900 || biasSwingMv > 50) {
        Serial.print("   <-- CHECK THE DIVIDER");
    }
    Serial.println();

    // --- clamp line, in raw-ish terms for diagnostics ---
    Serial.print("  Clamp:   ");
    Serial.print(ctRmsVolts * 1000.0, 3);
    Serial.print(" mV rms   p-p=");
    Serial.print(ct.peakToPeakCounts);
    Serial.print(" counts   n=");
    Serial.println(ct.n);

    // --- the actual answer ---
    Serial.print("  Current: ");
    if (amps < NOISE_FLOOR_AMPS) {
        Serial.print("below noise floor (< ");
        Serial.print(NOISE_FLOOR_AMPS, 2);
        Serial.println(" A)");
    } else {
        Serial.print(amps, 2);
        Serial.print(" A   approx ");

        // Amps times volts gives apparent power in VA. Calling it watts
        // assumes the current is in step with the mains voltage, which
        // is true for heaters and kettles and wrong for motors and
        // switching supplies. See README.md.
        Serial.print(amps * MAINS_VOLTAGE, 0);
        Serial.print(" W at ");
        Serial.print(MAINS_VOLTAGE, 0);
        Serial.println(" V, unity power factor assumed");
    }

    Serial.println();

    // Wait a second before the next reading. delay() blocks: nothing
    // else happens during it, which is fine here since there is nothing
    // else to do.
    delay(1000);
}
