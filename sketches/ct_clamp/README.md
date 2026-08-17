# CT clamp energy monitor: how it all works

Measuring mains current with an SCT-013-100 clamp, an ADS1115 converter, and an
ESP32-DevKitC-32E, with a Python script on the laptop to capture and plot the
waveform.

- Sketch: [`ct_clamp.ino`](ct_clamp.ino)
- Capture and plot script: [`../../analysis/ct_clamp/capture_waveform.py`](../../analysis/ct_clamp/capture_waveform.py)

Verified against a 1250 W kettle: measured 1247 VA, clean 50 Hz sine, no
harmonic content.

---

## Hardware

| Part | Detail |
|---|---|
| Clamp | YHDC SCT-013-100, 100 A to 1 V, burden resistor built in |
| Converter | ADS1115, 16-bit, I2C address 0x48 |
| Microcontroller | ESP32-DevKitC-32E (WROOM-32E), FQBN `esp32:esp32:esp32` |
| USB bridge | Silicon Labs CP2102N, appears as `/dev/cu.usbserial-*` |
| Bias network | Two 10 kΩ resistors from 3.3 V to ground |

Wiring:

```
3.3V ──[R1 10k]──┬──[R2 10k]── GND
                 │
                 ├── ADS1115 A1
                 └── CT red wire

ADS1115 A0 ────────── CT black wire

ESP32 GPIO21 ──────── ADS1115 SDA
ESP32 GPIO22 ──────── ADS1115 SCL
```

The clamp's white wire is the unused ring conductor of the 3.5 mm plug and is
left disconnected. Verify with a multimeter: the two coil wires read tens of
ohms between them, the unused one reads open circuit against both.

There is no capacitor on the midpoint. It's optional here because the reading is
differential, and any wobble on the midpoint appears on A0 and A1 together and
cancels in the subtraction. Measured drift is 3.2 mV rms, which is common to both
inputs and therefore invisible in the result.

---

## 1. What happens inside the ADS1115

The chip contains five blocks worth knowing about.

```
 A0 ─┐   ┌───────┐   ┌───────┐   ┌─────────────┐   ┌──────────┐   ┌────────────┐   ┌───────────┐
 A1 ─┤   │  MUX  │   │  PGA  │   │ delta-sigma │   │ digital  │   │ conversion │   │    I2C    │  SDA
 A2 ─┼──▶│4 to 1 │──▶│  ×2   │──▶│  modulator  │──▶│  filter  │──▶│  register  │──▶│ interface │──▶
 A3 ─┘   └───────┘   └───────┘   └──────▲──────┘   └──────────┘   └────────────┘   └───────────┘  SCL
                                        │
                                 ┌──────┴──────┐
                                 │   4.096 V   │
                                 │  reference  │
                                 └─────────────┘
```

### The multiplexer

One converter serves all four input pins. The multiplexer decides which pin, or
which pair of pins, is connected to the amplifier at any moment. It can select
four single-ended inputs measured against ground, or four differential pairings.
We use `MUX_DIFF_0_1`, meaning "A0 minus A1".

The consequence is that **channels are never read simultaneously**. Switching the
multiplexer takes effect on the next conversion, which is why the sketch throws
away two readings after every switch.

### The programmable gain amplifier

This amplifies the input before the converter sees it. The converter always
produces a signed 16-bit number spanning its own fixed range, so amplifying a
small signal first spreads it across more of those numbers. The chip's reference
is 4.096 V, and the measurable range is that divided by the gain.

| Setting | Gain | Range | Per count | Per count in amps | Clips at |
|---|---|---|---|---|---|
| `GAIN_TWOTHIRDS` | ×⅔ | ±6.144 V | 187.5 µV | 18.8 mA | 434 A |
| `GAIN_ONE` | ×1 | ±4.096 V | 125 µV | 12.5 mA | 290 A |
| **`GAIN_TWO`** | **×2** | **±2.048 V** | **62.5 µV** | **6.25 mA** | **145 A** |
| `GAIN_FOUR` | ×4 | ±1.024 V | 31.25 µV | 3.1 mA | 72 A |
| `GAIN_EIGHT` | ×8 | ±0.512 V | 15.6 µV | 1.6 mA | 36 A |
| `GAIN_SIXTEEN` | ×16 | ±0.256 V | 7.8 µV | 0.8 mA | 18 A |

This is real extra resolution rather than bigger numbers, because the
amplification happens in the analogue domain before quantisation. Going past the
range makes the reading saturate at ±32767, which flattens the peaks of the
waveform and makes the RMS read low without any obvious warning.

Note that `GAIN_TWOTHIRDS` showing ±6.144 V is a scaling factor and not
permission to apply 6 V. Inputs must still stay between ground and VDD.

### The delta-sigma converter

This works nothing like the successive-approximation converters in most
microcontrollers, which compare the input against a ladder of reference voltages
one bit at a time.

Instead, a single-bit comparator runs at a rate far above the output rate. Its
output feeds back through a one-bit DAC and gets subtracted from the input, and
the running error accumulates in an integrator. The result is a stream of ones
and zeroes whose *density* tracks the input voltage. Feed in half of full scale
and roughly three quarters of the bits come out as ones.

A digital filter then averages that bitstream over a window and produces the
16-bit result. This is where the data rate setting bites: a slower rate averages
over a longer window and gives a quieter reading. At 8 SPS the ADS1115 is
genuinely 16-bit-quiet; at our 860 SPS the last bit or so is noise, which matches
what the empty-clamp capture showed (readings bouncing between adjacent counts).

### The registers

Four 16-bit registers, addressed by a pointer byte written before each access.

| Pointer | Register | Purpose |
|---|---|---|
| 0x00 | Conversion | The latest result, read-only |
| 0x01 | Config | Multiplexer, gain, mode, data rate, comparator |
| 0x02 | Lo threshold | Comparator, unused here |
| 0x03 | Hi threshold | Comparator, unused here |

**There is no FIFO.** One conversion register, overwritten every time a
conversion finishes. Read late and you get the newest value; the ones in between
are gone. This is why the sketch paces its reads to the conversion rate instead
of reading as fast as it can.

The config register layout:

| Bits | Field | Our value |
|---|---|---|
| 15 | OS, start a conversion | 1 |
| 14:12 | MUX | 000, differential A0−A1 |
| 11:9 | PGA | 010, ±2.048 V |
| 8 | MODE | 0, continuous |
| 7:5 | DR, data rate | 111, 860 SPS |
| 4 | COMP_MODE | 0 |
| 3 | COMP_POL | 0 |
| 2 | COMP_LAT | 0 |
| 1:0 | COMP_QUE | 00 |

Which comes out as **0x84E0**, the value the library writes when the sketch calls
`startADCReading`.

### Continuous versus single-shot mode

In single-shot mode the chip converts once when asked and then powers down. In
continuous mode it converts back to back forever. We use continuous, because
single-shot adds a config write before every reading and we want the samples
evenly spaced.

---

## 2. The signal's journey

```
  Mains conductor, 5.19 A rms
        │   magnetic field
        ▼
  CT ferrite core
        │   2000:1 turns ratio
        ▼
  Secondary current, 2.6 mA rms
        │   20 Ω burden resistor
        ▼
  Differential voltage, 51.9 mV rms          ← floating, no reference
        │   10k/10k bias divider
        ▼
  Same voltage, anchored at 1.65 V
        │   PGA ×2
        ▼
  Amplified analogue signal
        │   delta-sigma modulator, then digital filter
        ▼
  16-bit signed counts, about 830 rms
        │   I2C at 400 kHz, two bytes
        ▼
  int16_t in ESP32 RAM
        │   running sum and sum of squares
        ▼
  RMS in counts
        │   × volts_per_count × amps_per_volt
        ▼
  Amps, as a double
        │   Serial.print
        ▼
  ASCII decimal text
        │   UART, 9600 8N1
        ▼
  CP2102N bridge
        │   USB
        ▼
  /dev/cu.usbserial-143410
        │   pyserial
        ▼
  Python string
        │   pandas
        ▼
  DataFrame
        │   numpy FFT, matplotlib
        ▼
  PNG plot
```

Stage by stage, with the numbers from the kettle capture.

### 1. Magnetic field to secondary current

Current in the conductor creates a circular magnetic field around it. The split
ferrite core of the clamp concentrates that field and guides it through the
secondary winding, which has 2000 turns. Transformer action means the secondary
carries 1/2000th of the primary current.

At 5.19 A through the jaws, the secondary carries 2.6 mA.

**Why the clamp must go around one conductor only.** Live and neutral carry equal
currents in opposite directions. Enclose both and their fields cancel, and the
core sees nothing. This is the same physics an RCD uses to detect earth leakage.

### 2. Secondary current to voltage

The 20 Ω burden resistor inside the clamp housing converts that current into a
voltage: 2.6 mA × 20 Ω = 51.9 mV. Working it through for the rated case, 100 A
gives 50 mA gives 1.00 V, which is where the "100 A to 1 V" specification comes
from and why `AMPS_PER_VOLT` is 100.

At this point the signal is a **voltage difference between the two clamp wires,
floating with respect to everything else**. That last part is the problem the
next stage solves.

### 3. Anchoring the pair

The coil connects the two wires to each other, so they always sit at the same
potential plus or minus the coil's output. But nothing decides what that
potential is relative to the ESP32's ground, so the pair drifts, and it drifted
by about 4 V when we first tried it, spending part of each cycle below ground
where the converter cannot follow.

The divider fixes that by pinning one end. 165 µA flows continuously from 3.3 V
through both resistors to ground, and since they're equal each drops half the
supply, putting their joint at 1.65 V. A1 is wired to that joint, so A0 has
nowhere to go but 1.65 V plus whatever the coil produces.

Almost no current flows into the chip's inputs, well under a microamp, so the
clamp isn't loaded and its full voltage appears across A0 and A1.

### 4. Analogue to digital

The multiplexer selects A0 minus A1, the amplifier doubles it, and the converter
turns it into a signed 16-bit count where 62.5 µV is one unit. The 51.9 mV rms
signal becomes about 830 counts rms, against a noise floor of one count.

### 5. Over I2C to the ESP32

I2C is two wires, a clock and a data line, both open-drain with pull-up
resistors so any device can hold them low. The ESP32 is the master and drives the
clock at 400 kHz.

Reading one conversion is two transactions:

```
START | 0x48+W | 0x00 (pointer to conversion register) | STOP
START | 0x48+R | read MSB | read LSB | STOP
```

Every byte gets acknowledged by the receiver pulling the data line low for a
ninth clock. The two bytes are big-endian and represent a signed 16-bit value.

At 400 kHz this takes well under 100 µs, so the bus is nowhere near the
bottleneck. The converter is the slow part at 860 conversions per second.

### 6. Counts to amps

The sketch accumulates statistics as it samples, then converts once at the end.
See the RMS algorithm below.

### 7. Down the wire to the laptop

The numbers are formatted as ASCII decimal text and pushed out of the ESP32's
UART at 9600 baud. The CP2102N chip on the DevKitC converts that to USB, macOS
exposes it as a character device, and Python reads it. Covered in section 4.

---

## 3. The sketch, line by line

### Configuration, lines 20 to 48

```cpp
#include <Wire.h>                  // ESP32 I2C driver
#include <Adafruit_ADS1X15.h>      // ADS1115 register wrangling
```

- **23 to 25** — I2C pins and the chip's address. 0x48 is what the ADS1115 uses
  when its ADDR pin is tied low or left at the module's default.
- **27** `SAMPLE_INTERVAL_US 1200` — a conversion at 860 SPS takes about 1163 µs.
  Reading faster than that returns the same value twice, so this paces the reads
  just past the conversion time. Actual achieved rate is 833 SPS.
- **28** `WINDOW_MS 200` — the measurement window. 200 ms is exactly ten cycles
  of 50 Hz, so the RMS covers whole cycles and has no partial cycle to skew it.
- **31** `AMPS_PER_VOLT 100.0` — the clamp's calibration constant.
- **32** `MAINS_VOLTAGE 240.0` — only used for the rough watts figure. This is an
  assumption, not a measurement.
- **35** `NOISE_FLOOR_AMPS 0.05` — below this the reading is chip noise, so the
  sketch says so rather than printing a meaningless number.
- **40** `RAW_SAMPLES 512` — the raw dump buffer size.
- **47 to 48** — the buffers. 512 samples costs 1 KB for the counts and 2 KB for
  the timestamps, which is nothing against the ESP32's 320 KB.

### `scanI2C`, lines 57 to 79

Walks every address from 1 to 126, starts a transmission to each, and checks
whether anything acknowledged. `Wire.endTransmission()` returning 0 means a
device pulled the data line low in response to its address.

This exists so that a dead or miswired chip is obvious immediately rather than
showing up as strange numbers later.

### `sampleMux`, lines 81 to 127

The core measurement routine. Takes a multiplexer setting and a window length,
returns statistics.

- **84** `startADCReading(mux, true)` — writes 0x84E0 to the config register,
  which sets the multiplexer, gain and rate, and puts the chip into continuous
  conversion.
- **88 to 91** — a multiplexer change only takes effect on the following
  conversion, so the first two results still reflect the old channel. These two
  reads throw them away.
- **93 to 97** — accumulators. `sum` and `sumSq` are `double` because the sum of
  squares gets large.
- **102** — loop until the window elapses.
- **103 to 105** — the pacing loop. `(long)(micros() - nextUs) < 0` handles the
  32-bit counter wrapping correctly, because the subtraction wraps too and the
  cast interprets the result as a signed offset. Comparing `micros() < nextUs`
  directly would break every 71 minutes when the counter rolls over.
- **106** — advance the target by a fixed step rather than from "now", so timing
  errors don't accumulate over the window.
- **108 to 113** — read the conversion register and update the running totals.
- **118 to 124** — the statistics, explained below.

#### The RMS algorithm

RMS means root mean square: square every sample, take the mean, take the square
root. But we want the RMS of the *AC part*, with any DC offset removed, because a
constant offset carries no current information.

The obvious approach needs two passes: one to find the mean, another to sum the
squared deviations from it. That would mean storing every sample.

Instead the sketch uses the identity

```
variance = mean(x²) − mean(x)²
```

which lets it accumulate `sum` and `sumSq` in a single pass and compute the
variance at the end (line 119). The square root of the variance is exactly the
RMS of the signal with its mean removed, which is what we want.

Line 120 clamps negative variance to zero. That can't happen mathematically, but
floating point rounding can produce a tiny negative number when the true variance
is near zero, and `sqrt` of that gives NaN.

**Known weakness.** This form loses precision when the mean is large compared
with the spread, because it subtracts two nearly equal large numbers. For the
differential channel the mean is near zero so it's fine. For the bias channel the
mean is around 26,500 counts and the spread is about 53, so the subtraction
discards perhaps six of the fifteen significant digits a `double` carries. Still
plenty here, but Welford's online algorithm would be the robust replacement if
this ever moved to `float` or to much larger offsets.

### `captureRaw`, lines 129 to 164

Same sampling loop, but storing every sample instead of summarising.

- **137 to 147** — fill the buffers, recording the actual microsecond offset of
  each sample so the timing can be checked afterwards. Measured jitter is 0.6 µs.
- **149 to 155** — a header block. The calibration constants travel with the data
  so the Python side never has to hardcode them.
- **157 to 161** — the dump.

**Why it buffers first.** At 9600 baud the serial port carries about 960
characters per second. Each sample line is roughly 13 characters, and we produce
833 samples per second, needing over 10,000 characters per second. Printing
while sampling would stall the loop and wreck the timing. So it records into RAM
at full speed and spends about seven seconds printing afterwards.

### `setup`, lines 166 to 210

- **179** `Wire.begin(21, 22)` — the ESP32 can route I2C to almost any pin, so
  the pins are given explicitly.
- **180** `Wire.setClock(400000)` — I2C fast mode. The default 100 kHz would also
  work, this just leaves more headroom.
- **184** — `ads.begin()` returns false if nothing acknowledges at 0x48, in which
  case `adsReady` stays false and the sketch prints nothing misleading.
- **191 to 192** — gain and data rate.
- **193** `computeVolts(1)` — asks the library what one count is worth in volts,
  given the gain just set. Everything downstream scales from this single value,
  so changing line 191 is enough to reconfigure the whole sketch.

### `loop`, lines 212 to 272

- **213 to 216** — if the chip never answered, idle rather than print rubbish.
- **218 to 224** — check for an incoming request. `Serial.available()` reports how
  many bytes are waiting and returns immediately, so this polls rather than
  blocks. The `return` on 222 skips the normal summary for that pass, so a
  seven-second dump isn't immediately followed by a reading.
- **228** — measure A1 against ground for 40 ms. This is the bias health check.
- **229** — measure A0 minus A1 for 200 ms. This is the actual current.
- **231 to 234** — convert counts to millivolts, then to amps.
- **244 to 246** — flag an out-of-range bias. If the midpoint isn't sitting where
  it should, the current figure below it is meaningless, and this says so.
- **257 to 260** — refuse to report a current below the noise floor.
- **271** — one second between readings.

---

## 4. Laptop to ESP32: the protocol stack

Four layers, none of which know about each other.

### Physical: USB to UART

The ESP32 speaks UART, a two-wire asynchronous serial protocol at 3.3 V logic
levels. Your laptop speaks USB. The CP2102N chip on the DevKitC bridges the two.

macOS has a built-in driver for it and exposes it as two character devices:

- `/dev/cu.usbserial-143410` — "call up", opens immediately
- `/dev/tty.usbserial-143410` — blocks on open waiting for carrier detect

Always use the `cu` one for this. The `tty` variant is a leftover from dial-up
modems and will hang.

### Framing: 8N1 at 9600 baud

Each byte goes out as a start bit, eight data bits least significant first, no
parity bit, and one stop bit. Ten bits per byte, so 9600 baud carries 960 bytes
per second.

There is no addressing, no error detection, no retransmission, and no flow
control. It is a raw byte pipe in both directions at once, and either end can
transmit whenever it likes without disturbing the other.

### The reset side-channel

USB-to-serial chips carry two control lines left over from modems, DTR and RTS.
On ESP32 dev boards these are wired through a pair of transistors to the chip's
EN (reset) and IO0 (boot mode) pins.

This has two consequences:

- **Opening the port resets the board.** The Python script sleeps 2.5 seconds
  after opening to let it boot.
- **`arduino-cli upload` uses the same trick** to drop the chip into its ROM
  bootloader, then talks a SLIP-framed packet protocol to write flash.

### Application layer: our own line protocol

Everything above is generic. The bit specific to this project is the convention
the sketch and the script agree on.

| Element | Meaning |
|---|---|
| `r` sent to the board | Request a raw dump |
| `#RAW_BEGIN` | Everything before this is ignored |
| `#key=value` | Metadata, one per line |
| `micros,counts` | CSV header |
| `123,4567` | Sample rows |
| `#RAW_END` | Stop reading |

The markers matter because **the stream never stops**. The board keeps printing
its once-a-second summaries throughout, and the request may arrive mid-summary.
The script doesn't control the stream, it filters it: discard everything until
`#RAW_BEGIN`, keep what follows, stop at `#RAW_END`.

The request can take up to about a second and a half to take effect, because the
sketch only glances at the receive buffer once per pass through `loop`, and a
pass includes 240 ms of sampling plus a one-second delay.

---

## 5. What the Python script does

[`capture_waveform.py`](../../analysis/ct_clamp/capture_waveform.py). Run it
with:

```bash
uv run analysis/ct_clamp/capture_waveform.py --label kettle
```

### `find_port`

Globs `/dev/cu.usbserial*` and takes the first match. Overridable with `--port`.

### `capture`

Opens the port at 9600, sleeps through the DTR-triggered reset, clears whatever
accumulated in the operating system's receive buffer, then writes a single `r`
byte and reads lines until it sees the end marker. Returns the metadata as a dict
and the CSV rows as one string.

`reset_input_buffer()` matters more than it looks. macOS buffers incoming serial
data whether or not any program is reading, so by the time the script starts
there can be several seconds of stale summary text queued.

### `analyse`

Converts counts to volts using `volts_per_count` from the header, then to amps
using `amps_per_volt`. Subtracts the mean to get the AC component, then computes
the same RMS the sketch does, this time with numpy over the full array.

Also reports the mean and standard deviation of the gaps between sample
timestamps, which is how we know the pacing loop holds 1200 µs to within 0.6 µs.

### `dominant_frequency`

Applies a Hann window, then a real FFT.

The window is there to stop spectral leakage. An FFT assumes the signal repeats
forever, so if the capture doesn't contain a whole number of cycles there's a
discontinuity at the join, which smears energy across every frequency bin. The
Hann window tapers both ends to zero and removes the discontinuity.

It then finds the tallest bin, ignoring DC, and compares it against the median
bin. If the peak isn't at least five times the median the spectrum is flat and it
returns `None` rather than naming a frequency. Without that check an empty clamp
reports the tallest lump of noise as if it were a real signal.

**Frequency resolution** is the sample rate divided by the sample count, so
833 ÷ 512, about 1.63 Hz per bin. That's why a 50 Hz signal reports as 50.5 Hz.
The grid isn't drifting, the bins just aren't finer than that.

### `plot`

Three panels:

1. The whole 613 ms capture
2. The first 100 ms with every ADC sample drawn as a dot, showing the roughly 17
   samples per cycle that 833 SPS gives against 50 Hz
3. The spectrum, with the x-axis stopping at half the sample rate

### `main`

Wires it together, writes a timestamped CSV and PNG next to the script, and
prints a summary. Note that `.gitignore` excludes `*.png`, so the CSVs are
committed and the plots are not.

---

## Known limitations

**It measures amps, not watts.** The clamp sees the size of the current and
nothing about its timing relative to the mains voltage, because there's no
voltage reference in the circuit. The watts figure assumes a power factor of 1.
That's close enough for kettles, heaters and filament lamps. It will overstate a
fridge compressor, a washing machine motor or a cheap LED driver, and at 90° of
phase shift a load draws full current while consuming nothing at all.

Measuring real power needs a voltage sensor, and that runs into the single shared
converter: voltage and current would be sampled about a millisecond apart, which
at 50 Hz is 18° of phase error to correct for in software.

**Harmonics above 417 Hz alias.** Half of 833 SPS. Anything faster folds back and
appears at the wrong frequency. Fine for a resistive load, misleading for
anything with a switching supply.

**The gain is set conservatively.** `GAIN_TWO` clips at 145 A, beyond what the
clamp is even rated for. `GAIN_EIGHT` would clip at 36 A and improve resolution
from 6.25 mA to 1.6 mA per count, which would make standby loads of a watt or two
visible. One-line change at line 191.

**Mains voltage is assumed, not measured.** `MAINS_VOLTAGE` is hardcoded at 240.

**No capacitor on the bias midpoint.** Justified by the differential reading, but
worth adding if the drift figure ever climbs into the tens of millivolts.
