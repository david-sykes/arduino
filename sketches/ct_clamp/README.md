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

The amplifier behind the multiplexer has two inputs, a positive and a negative,
and it only ever cares about the voltage difference between them. The
multiplexer is an array of solid-state switches (CMOS transmission gates) that
decide which physical pin gets connected to each of those two inputs. Nothing is
being "selected" in software terms; actual switches close.

There are eight valid switch settings:

| Setting | Positive input connects to | Negative input connects to |
|---|---|---|
| `MUX_DIFF_0_1` | A0 | A1 |
| `MUX_DIFF_0_3` | A0 | A3 |
| `MUX_DIFF_1_3` | A1 | A3 |
| `MUX_DIFF_2_3` | A2 | A3 |
| `MUX_SINGLE_0` | A0 | internal ground |
| `MUX_SINGLE_1` | A1 | internal ground |
| `MUX_SINGLE_2` | A2 | internal ground |
| `MUX_SINGLE_3` | A3 | internal ground |

So a "single-ended" reading isn't a different kind of measurement. It's still a
difference, with the negative side switched to the chip's own ground instead of
to a pin.

The sketch uses two of these. `MUX_DIFF_0_1` measures the clamp, giving A0 minus
A1. `MUX_SINGLE_1` measures A1 against ground, which is how it checks the bias
voltage.

Two consequences follow from there being one set of switches and one converter:

- **Channels are never measured at the same instant.** Reading A0 and A1
  separately gives you two snapshots about a millisecond apart, not a
  simultaneous pair.
- **A switch change only takes effect on the next conversion.** The conversion
  already in progress finishes using the old switch positions. That's why the
  sketch discards two readings after every switch, in both `sampleMux` and
  `captureRaw`.

### The programmable gain amplifier

**The gain is a plain multiplication of voltage.** Gain of 2 means the amplifier
puts out twice the voltage difference presented at its inputs. Gain of 16 means
sixteen times.

The reason that matters is what sits behind it. The modulator can only digitise
voltages up to its own reference of 4.096 V, and that limit is fixed in silicon.
So the amplifier's output has to land inside ±4.096 V, which means the largest
input you can present is:

```
max input = 4.096 V / gain
```

**That number is an input range, measured at the pins.** It is not an output
range and it has nothing to do with the size of the numbers you get back. The
datasheet calls it the full scale range, or FSR, and it's what the table below
lists.

| Setting | Amplifier gain | Max input at the pins (FSR) | Volts per count | Amps per count | Max current, rms |
|---|---|---|---|---|---|
| `GAIN_TWOTHIRDS` | ×⅔ | ±6.144 V | 187.5 µV | 18.8 mA | 434 A |
| `GAIN_ONE` | ×1 | ±4.096 V | 125 µV | 12.5 mA | 290 A |
| **`GAIN_TWO`** | **×2** | **±2.048 V** | **62.5 µV** | **6.25 mA** | **145 A** |
| `GAIN_FOUR` | ×4 | ±1.024 V | 31.25 µV | 3.1 mA | 72 A |
| `GAIN_EIGHT` | ×8 | ±0.512 V | 15.6 µV | 1.6 mA | 36 A |
| `GAIN_SIXTEEN` | ×16 | ±0.256 V | 7.8 µV | 0.8 mA | 18 A |

**The output range never changes.** Whatever the gain, the result is a signed
16-bit integer from −32768 to +32767, where +32767 means "input reached the
positive FSR" and −32768 means it reached the negative FSR. So:

```
one count = FSR / 32768
counts    = input volts × 32768 / FSR
```

Worked through at our setting of `GAIN_TWO`, FSR ±2.048 V:

| Input at the pins | Amplifier output | Fraction of ±4.096 V | Counts |
|---|---|---|---|
| 2.048 V | 4.096 V | 100% | +32767 (full scale) |
| 1.000 V | 2.000 V | 48.8% | +16000 |
| 51.9 mV | 103.8 mV | 2.5% | +830 |
| 62.5 µV | 125 µV | 0.003% | +1 (one count) |
| 0 V | 0 V | 0% | 0 |
| −1.000 V | −2.000 V | −48.8% | −16000 |

Turning the gain up gives real extra resolution rather than just bigger numbers,
because the multiplication happens in the analogue domain before anything is
rounded to an integer. Doubling in software afterwards would give you 32000
instead of 16000, but with the same underlying uncertainty. Doubling in the
amplifier gives you 32000 counts that each mean half as much voltage.

Going past the FSR makes the reading stick at ±32767. The waveform comes back
with flat tops and the RMS reads low, with nothing in the output to warn you.

One trap: `GAIN_TWOTHIRDS` lists ±6.144 V, which is larger than the 3.3 V supply.
That is arithmetic from 4.096 ÷ ⅔ and not permission to apply 6 V to the pins.
The absolute limit is still the supply rails, regardless of gain.

### The delta-sigma converter

This works nothing like the successive-approximation converter inside the ESP32,
which compares the input against a ladder of reference voltages one bit at a
time and needs a sample-and-hold circuit to keep the input still while it does.

A delta-sigma converter has no ladder. It has three parts in a feedback loop:

- an **integrator**, which is a running total
- a **one-bit comparator**, which asks "is the running total above zero?"
- a **one-bit DAC**, which feeds either +full-scale or −full-scale back to be
  subtracted from the input

Each tick of its internal clock, the loop does this:

```
bit   = (accumulator >= 0) ? 1 : 0
accumulator += input − (bit ? +full_scale : −full_scale)
```

The feedback constantly drags the accumulator back towards zero, so the
comparator has to emit ones and zeroes in whatever mixture cancels the input. The
result is a stream of bits whose **proportion of ones encodes the voltage**.

Here it is running with an input at half of full scale, on a scale where full
scale is 1.0:

| Tick | Accumulator before | Bit out | Feedback | Accumulator after |
|---|---|---|---|---|
| 1 | 0.0 | 1 | −1.0 | −0.5 |
| 2 | −0.5 | 0 | +1.0 | +1.0 |
| 3 | +1.0 | 1 | −1.0 | +0.5 |
| 4 | +0.5 | 1 | −1.0 | 0.0 |
| 5 | 0.0 | 1 | −1.0 | −0.5 |
| 6 | −0.5 | 0 | +1.0 | +1.0 |
| 7 | +1.0 | 1 | −1.0 | +0.5 |
| 8 | +0.5 | 1 | −1.0 | 0.0 |

It settles into `1110` repeating. Three ones in every four, a density of 0.75.
Reading that back out, `2 × 0.75 − 1 = 0.5`, which is the input we put in. Feed
in zero and you get `1010` alternating, a density of 0.5, which maps to 0.
Feed in full scale and you get solid ones.

The **digital filter** is what turns that bitstream into a number. It counts the
ones over a window and scales the result to 16 bits. No single bit carries any
precision at all; the precision comes entirely from averaging thousands of them.

This is where the data rate setting bites. The modulator runs at a fixed rate
regardless of your setting, so a slower output rate simply averages more bits per
result:

- At **8 SPS** the filter averages roughly 107 times as many modulator bits as at
  860 SPS, and the reading is quiet to nearly the full 16 bits.
- At **860 SPS**, which is what we use, the last bit or so is noise.

That last point is exactly what the empty-clamp capture showed: readings hopping
between adjacent counts, 6.25 mA apart, with nothing in between.

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

Here is the whole circuit with every node labelled:

```
      ESP32 3V3   3.300 V
            ●
            │
          ┌─┴─┐
          │R1 │ 10 kΩ          165 µA flows down through both
          └─┬─┘                resistors continuously. This is DC
            │                  and carries no signal.
            ├────────────────────────────●  A1    1.650 V, never moves
            │
   node M   │      ┌──────────────────┐
   1.650 V  ├──────┤ CT burden, 20 Ω  ├──●  A0    1.650 V + v(t)
            │      │   v(t) across it │
            │      └──────────────────┘
          ┌─┴─┐
          │R2 │ 10 kΩ
          └─┬─┘
            │
            ●   GND   0.000 V
```

The whole vertical run between R1 and R2 is a single node, with the A1 pin and
one leg of the clamp both tapped off it.

#### Why anything needs anchoring

Voltage is always a difference between two points. There is no such thing as the
voltage *at* a place. The clamp produces a difference between its two wires and
says nothing about where either wire sits relative to the ESP32's ground. Both
could be at 1 V, or both at 400 V, and the clamp would be equally content,
because it only controls the gap.

The ADS1115 is not so relaxed. Both its inputs must stay between 0 V and 3.3 V.
So something has to decide where the pair sits, and the clamp will not do it.

#### How the anchoring physically works

Two 10 kΩ resistors in series across 3.3 V carry `3.3 V ÷ 20 kΩ = 165 µA`. The
same current runs through both, so each drops `165 µA × 10 kΩ = 1.65 V`, putting
their joint at 1.65 V.

That joint behaves like a spring anchored at 1.65 V. Push it up and the current
through R1 falls while the current through R2 rises, and the imbalance produces a
restoring drop. The stiffness of the spring is the two resistors in parallel,
5 kΩ:

```
to move node M by 1 mV    you must inject   200 nA
to move node M by 100 mV  you must inject    20 µA
```

So it isn't rigid. It's a spring whose strength you can calculate.

#### Two loops, sharing the burden resistor

The question this raises is whether the clamp pushes that spring around. It
barely does, and the reason is about where current goes rather than how stiff the
spring is. There are two current loops, and they share the burden resistor:

```
  Loop 1, inside the clamp housing:
      coil  →  burden resistor  →  back to coil
      2.6 mA at 5.19 A through the jaws. This creates the voltage.

  Loop 2, out to the chip:
      burden  →  A0  →  chip's 5 MΩ input  →  A1  →  back to burden
      10 nA. This measures the voltage.
```

Loop 2 passes through node M, but the current entering M from the chip equals the
current leaving M into the clamp. **Net current into the divider is zero.** The
signal current circulates in its own ring and the spring is never pushed.

Another way to see the same thing: the chip's input sits in parallel with the
burden resistor, and 5 MΩ in parallel with 20 Ω is 19.99992 Ω. The chip is
electrically almost not there. It draws one part in 250,000 of what's already
circulating, so measuring doesn't change what's being measured.

Note where the divider sits in that picture. It's in neither loop. It carries no
signal current and is not part of the measurement path. Its only job is to hold
node M at a height where both ends of the burden land inside the window the chip
can read.

#### When the node does move, it cancels

Stray capacitance from nearby mains wiring does push a little current into node M.
Measured drift is 3.2 mV rms, which works out at about 640 nA against the 5 kΩ
spring, and 3.2 mV is roughly 51 counts.

Yet the clamp channel with empty jaws reads 2 to 4 counts. Fifty-one counts of
movement on the midpoint produced three counts in the answer, because when M
shifts, A0 shifts by the same amount at the same instant. The subtraction removes
it.

**The counter-example is how this project started.** With no divider, node M was
still tied to A0 through the clamp, but nothing set the pair's absolute level. A
few picoamps of stray coupling dragged the whole thing around by 3915 mV while
the difference between A0 and A1 stayed at 3 counts. The clamp was working
perfectly throughout. What was missing was any statement about where the pair
should sit.

The picture worth holding: the clamp is a rigid rod of a certain length. The
divider decides where one end of it is. The other end goes wherever the rod's
length puts it. Moving the whole rod doesn't change its length.

### 4. Analogue to digital

The multiplexer selects A0 minus A1, the amplifier doubles it, and the converter
turns it into a signed 16-bit count where 62.5 µV is one unit.

**The 51.9 mV is an RMS figure, not a fixed voltage.** It summarises a whole
cycle. The actual voltage is a 50 Hz sine that crosses zero a hundred times a
second and peaks at `51.9 × √2 = 73.4 mV`. Here is the circuit at four moments:

| Moment | Across the burden | A1 | A0 | A0 − A1 | Counts |
|---|---|---|---|---|---|
| Positive peak | +73.4 mV | 1.650 V | 1.7234 V | +73.4 mV | +1174 |
| Zero crossing | 0 mV | 1.650 V | 1.650 V | 0 mV | 0 |
| Negative peak | −73.4 mV | 1.650 V | 1.5766 V | −73.4 mV | −1174 |
| Whole cycle, rms | 51.9 mV | 1.650 V | — | 51.9 mV | 831 |

A1 is identical in every row. A0 does all the moving, which is exactly why the
pair has to sit at 1.65 V rather than at 0 V: at 0 V the negative half of every
cycle would fall below ground and the chip would lose it.

The last row is not a moment in time like the others. It's a property of the whole
cycle, computed from 167 samples spanning ten cycles.

Against a noise floor of one count, 831 counts rms is a strong signal.

### 5. Over I2C to the ESP32

I2C is two wires shared by every device on the bus: **SCL** carries the clock,
**SDA** carries the data. Both go to GPIO21 and GPIO22 on the ESP32.

**Why the pull-up resistors matter.** No device on an I2C bus is ever allowed to
drive a wire high. Each one can only either pull it down to ground or let go.
This is called open-drain, and the wire is held high by a resistor to 3.3 V
whenever nobody is pulling. The effect is that the line reads high only when
every device has let go, so two devices talking at once can never fight and
short each other out. The ADS1115 breakout board has these resistors fitted
already, which is why you didn't need to add any.

**Who drives what.** The ESP32 is the master. It generates every clock pulse on
SCL and starts and ends every transaction. The ADS1115 never speaks unless
spoken to, and it has no way to interrupt or volunteer data.

**How a device gets addressed.** Each device has a 7-bit address, 0x48 for our
chip. The master sends that address followed by one more bit saying whether it
wants to read or write. Every device on the bus compares the address with its
own, and the one that matches pulls SDA low for a ninth clock pulse to say "that's
me". That acknowledgement is how `scanI2C` works: it sends an address and checks
whether anything pulled the line down.

**START and STOP** are the two illegal-looking signals that bracket a
transaction. Normally SDA is only allowed to change while SCL is low. A START is
SDA falling *while SCL is high*, and a STOP is SDA rising while SCL is high.
Because those transitions can't occur during ordinary data, every device
recognises them unambiguously.

**The pointer register.** The ADS1115 has four registers but no address bus to
pick between them. Instead it has a pointer: a small internal value saying which
register the next read or write will land on. So reading a conversion is two
transactions, one to aim the pointer and one to collect the data:

```
START │ 0x48 + write bit │ ACK │ 0x00  (aim pointer at conversion register) │ ACK │ STOP
START │ 0x48 + read bit  │ ACK │ read high byte │ ACK │ read low byte │ NACK │ STOP
```

The two data bytes arrive most significant first and together form a signed
16-bit two's complement number. The master sends NACK rather than ACK after the
last byte, which is how it tells the chip to stop sending.

At 400 kHz each bit takes 2.5 µs, so the whole exchange above is roughly 40 bits
and finishes in about 100 µs. **The bus is nowhere near the bottleneck.** The
converter is, at 860 conversions per second, one every 1163 µs. This is the
opposite of what most people assume, and it's why the sketch deliberately slows
its reads down to match the converter rather than reading as fast as it can.

### 6. Counts to amps

**Why RMS and not the average.** The average of a sine wave over a whole cycle is
zero, because it spends as long negative as positive. Averaging the samples tells
you nothing about how much current is flowing. RMS is defined so that it answers
a physical question: what steady DC current would heat a resistor at the same
rate? That's the number that relates to power, so it's the one worth computing.

The sketch keeps running totals while sampling and does all the conversion once
at the end, in three multiplications:

```
831 counts rms                          the AC part, from the running totals
  × 62.5 µV per count      = 51.9 mV    volts at the ADS1115 pins
  × 100 A per volt         = 5.19 A     current through the jaws
  × 240 V (assumed)        = 1246 VA    apparent power
```

Every one of those constants comes from somewhere specific. The 62.5 µV is read
back from the library by `computeVolts(1)` based on the gain setting, so it
tracks automatically if you change the `setGain` call. The 100 comes from the clamp's own
specification. The 240 is hardcoded and is the weakest link, since it's an
assumption about your supply rather than anything measured.

How the 831 is arrived at is the interesting part, covered under the RMS
algorithm below.

### 7. Down the wire to the laptop

The numbers are formatted as ASCII decimal text and pushed out of the ESP32's
UART at 9600 baud. The CP2102N chip on the DevKitC converts that to USB, macOS
exposes it as a character device, and Python reads it. Covered in section 4.

---

## 3. The sketch

[`ct_clamp.ino`](ct_clamp.ino) is commented throughout, line by line, so this
section doesn't repeat what the comments already say. It covers the four things
that need more room than a comment can reasonably take.

The sketch has five functions:

| Function | Job |
|---|---|
| `scanI2C` | Report every device answering on the bus, so a miswired chip is obvious |
| `sampleMux` | Sample one input for a window and return summary statistics |
| `captureRaw` | Same sampling loop, but keep every sample and print them as CSV |
| `setup` | Runs once: start the bus, configure the chip, print a banner |
| `loop` | Runs forever: check for a request, take a reading, print it, wait a second |

`sampleMux` is where all the real work happens. Everything else is presentation.

### `Stats`, and why it exists

A C function can only return one value, and `sampleMux` needs to hand back four.
A `struct` solves that: it defines a new type gluing several values together
under one name.

```cpp
struct Stats {
    long n;                   // how many samples were taken
    double meanCounts;        // their average, the DC level
    double acRmsCounts;       // RMS after removing that average
    long peakToPeakCounts;    // largest sample minus smallest
};
```

After that definition `Stats` is a type like `int` or `float`, so
`Stats ct = sampleMux(...)` declares one and `ct.acRmsCounts` reads a
compartment out of it. No behaviour, just four labelled boxes travelling
together.

### The RMS algorithm

RMS means root mean square: square every sample, take the mean, take the square
root. But we want the RMS of the *AC part*, with any DC offset removed, because a
constant offset carries no current information.

The obvious approach needs two passes: one to find the mean, another to sum the
squared deviations from it. That would mean storing every sample.

Instead the sketch uses the identity

```
variance = mean(x²) − mean(x)²
```

which lets it accumulate `sum` and `sumSq` in a single pass and work the variance
out at the end:

```cpp
double mean     = sum / n;
double variance = (sumSq / n) - (mean * mean);
```

The square root of the variance is exactly the RMS of the signal with its mean
removed. That falls straight out of the definitions:

```
AC RMS = sqrt( mean( (x − mean)² ) ) = sqrt( variance ) = standard deviation
```

So **the AC RMS of a signal and the standard deviation of its samples are the
same quantity**, with two names depending on whether you're doing electronics or
statistics. Once you see that, the single-pass trick is just the standard
computational formula for variance that any statistics library uses.

The `if (variance < 0) variance = 0;` line after it looks pointless, since
variance can never truly be negative. It's there because floating point rounding
can produce a tiny negative value when the real variance is near zero, and
`sqrt()` of a negative number returns NaN, which would then poison every
calculation downstream.

**Known weakness.** This form loses precision when the mean is large compared
with the spread, because it subtracts two nearly equal large numbers. For the
differential channel the mean is near zero so it's fine. For the bias channel the
mean is around 26,500 counts and the spread is about 53, so the subtraction
discards perhaps six of the fifteen significant digits a `double` carries. Still
plenty here, but Welford's online algorithm would be the robust replacement if
this ever moved to `float` or to much larger offsets.

### The pacing loop, and a cast that looks wrong

The line that stops the sketch reading the same conversion twice is this:

```cpp
while ((long)(micros() - nextUs) < 0) {
    // Wait out the conversion so we don't re-read the same value.
}
```

The obvious version would be `while (micros() < nextUs)`, and it would work
almost all the time. `micros()` counts microseconds since the board powered up in
a 32-bit unsigned integer, which runs out after about 71 minutes and wraps back
to zero. When it does, `micros()` becomes a tiny number while `nextUs` is still
huge, the comparison stays true, and the sketch sits in that loop for the next 71
minutes.

Subtracting first fixes it. The subtraction wraps in exactly the same way the
counter does, so `micros() - nextUs` gives the correct gap either side of the
wrap. Casting that to a signed `long` lets it be read as "how far past due are
we", negative before the deadline and positive after. This is the standard
Arduino idiom for timing, and it is worth recognising because it looks like a
pointless cast until you know what it's protecting against.

The next line matters too:

```cpp
nextUs += SAMPLE_INTERVAL_US;
```

The deadline advances by a fixed step rather than being recalculated from the
current time. If it were set to `micros() + SAMPLE_INTERVAL_US` instead, every
small overshoot would be baked in and the errors would pile up across the window.
Stepping a fixed amount means the schedule stays absolute and drift can't
accumulate. Measured jitter over 512 samples is 0.6 µs.

### Why `captureRaw` buffers before printing

It records all 512 samples into RAM first and prints them afterwards, which looks
like an unnecessary complication until you compare the two rates.

```
serial port at 9600 baud   ~960 characters per second
sampling at 833 SPS        ~13 characters per sample = ~10,800 per second
```

Printing while sampling would block the loop waiting for the serial port to
drain, the pacing would collapse, and the timing of every sample after the first
would be meaningless. So it samples flat out for 0.6 seconds and then spends
about seven seconds trickling the result out.

The header block it prints first carries the calibration constants with the data:

```
#volts_per_count=0.000062500
#amps_per_volt=100.00
```

That way the Python script never hardcodes values that live in the sketch. Change
the gain and the script follows automatically.

### One value that configures everything

In `setup`, after the gain is set:

```cpp
ads.setGain(GAIN_TWO);
voltsPerCount = ads.computeVolts(1);
```

`computeVolts(1)` asks the library what a single count is worth in volts at
whatever gain was just selected. Every conversion from counts to real units
downstream goes through that one number, so changing the `setGain` line is enough
to reconfigure the whole sketch. Nothing else needs touching.

---

## 4. Laptop to ESP32: the protocol stack

Four layers, none of which know about each other.

### Physical: USB to UART

The ESP32 speaks UART, an asynchronous serial protocol at 3.3 V logic levels
carried on a transmit line, a receive line, and a shared ground. Your laptop
speaks USB. The CP2102N chip on the DevKitC bridges the two.

macOS has a built-in driver for it and exposes it as two character devices:

- `/dev/cu.usbserial-143410` — "call up", opens immediately
- `/dev/tty.usbserial-143410` — blocks on open waiting for carrier detect

Always use the `cu` one for this. The `tty` variant is a leftover from dial-up
modems and will hang.

### Framing: 8N1 at 9600 baud

**Asynchronous means there is no clock wire.** Unlike I2C, where the master
supplies a clock pulse for every bit, UART has only a transmit line, a receive
line, and a shared ground. Both ends have to already agree how long a bit lasts,
and that agreement is the baud rate. Set 9600 on one end and 115200 on the other
and you get garbage, because the receiver samples at the wrong moments.

The line idles high. Each byte is framed like this:

```
idle    start   b0  b1  b2  b3  b4  b5  b6  b7   stop   idle
─────┐        ┌───┬───┬───┬───┬───┬───┬───┬───┐        ┌─────
     └────────┤   │   │   │   │   │   │   │   ├────────┘
      1 bit                8 data bits           1 bit
```

The **start bit** is the whole trick. It's a single low bit that breaks the idle
high state, and its falling edge tells the receiver "a byte begins now". The
receiver then counts off bit periods from that edge using its own local clock,
sampling in the middle of each one. Data goes least significant bit first. The
**stop bit** returns the line high so the next start bit has an edge to make.

So 8N1 means eight data bits, no parity bit, one stop bit. Add the start bit and
that's ten bits on the wire per byte of payload, which is why 9600 baud carries
960 bytes per second rather than 1200.

Each end only needs its clock accurate to within a few percent, because
resynchronisation happens at every start bit and there are only ten bit periods
to drift across before the next one.

There is no addressing, no error detection, no retransmission, and no flow
control. It's a raw byte pipe running in both directions at once, and either end
can transmit whenever it likes without disturbing the other.

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

**What an FFT actually does.** Any repeating signal, however lumpy, can be
written as a sum of pure sine waves at different frequencies. An FFT takes your
512 samples and works out how much of each frequency is present, returning one
magnitude per frequency. A clean 50 Hz sine gives one tall value at 50 Hz and
near-nothing elsewhere, which is exactly what the kettle capture produced. A
signal with distortion would show extra peaks at multiples of 50.

The frequencies it reports are not arbitrary. They come out evenly spaced from
0 Hz up to half the sample rate, and the spacing is:

```
bin spacing = sample rate / number of samples = 833 / 512 = 1.63 Hz
```

Those are called bins, and nothing between them can be distinguished. The nearest
bin to 50 Hz sits at 50.4, which is why the capture reports 50.5 Hz. **The grid
isn't drifting**, the resolution just isn't finer than 1.63 Hz. Capturing more
samples would narrow the bins.

**Why it stops at half the sample rate.** You need at least two samples per cycle
to tell a wave is oscillating at all. Above half the sample rate a signal
produces exactly the same set of samples as some lower frequency would, so the
converter cannot tell them apart and the higher one shows up disguised as a lower
one. That's aliasing, and half the sample rate is the Nyquist limit. Here it's
417 Hz.

**Why the Hann window.** An FFT assumes the 512 samples repeat forever, joined
end to end. If the capture doesn't contain a whole number of cycles, the last
sample doesn't line up with the first and there's a sharp step at the join. That
step is not in your signal, but the FFT sees it and smears energy across every
bin, which is called spectral leakage. Multiplying the samples by a Hann window,
a raised cosine that tapers to zero at both ends, removes the step so the peak
stays sharp.

**The flat-spectrum check.** It finds the tallest bin, ignoring DC, and compares
it against the median bin. If the peak isn't at least five times the median, the
spectrum is just noise and it returns `None` rather than naming a frequency.
Without that check, an empty clamp reports the tallest lump of noise as though it
meant something, which is what the first capture did before this was added.

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
visible. One-line change to the `setGain` call in `setup`.

**Mains voltage is assumed, not measured.** `MAINS_VOLTAGE` is hardcoded at 240.

**No capacitor on the bias midpoint.** Justified by the differential reading, but
worth adding if the drift figure ever climbs into the tens of millivolts.
