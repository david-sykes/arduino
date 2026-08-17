"""Capture a raw waveform from the CT clamp sketch and plot it.

Sends 'r' to the ESP32, which buffers 512 differential samples off the
ADS1115 and dumps them as CSV. Saves the raw data alongside a three panel
plot: the whole capture, a zoom on a few mains cycles, and the spectrum.

    uv run analysis/ct_clamp/capture_waveform.py
    uv run analysis/ct_clamp/capture_waveform.py --label kettle
"""

import argparse
import glob
import io
import os
import sys
import time
from datetime import datetime

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import serial

OUTPUT_DIR = os.path.dirname(__file__)
BAUD = 9600
BOOT_WAIT_S = 2.5
CAPTURE_TIMEOUT_S = 45
ZOOM_MS = 100  # five cycles of 50 Hz


def find_port():
    ports = sorted(glob.glob("/dev/cu.usbserial*"))
    if not ports:
        raise SystemExit(
            "No /dev/cu.usbserial* device found. Plug the board in, or pass --port."
        )
    if len(ports) > 1:
        print(f"Several ports found, using {ports[0]}")
    return ports[0]


def capture(port):
    """Open the port, ask for a dump, and return (metadata, raw csv text)."""
    print(f"Opening {port} at {BAUD}...")

    with serial.Serial(port, BAUD, timeout=2) as ser:
        # Opening the port toggles DTR, which resets the ESP32. Let it boot.
        time.sleep(BOOT_WAIT_S)
        ser.reset_input_buffer()

        print("Requesting capture...")
        ser.write(b"r")
        ser.flush()

        meta = {}
        csv_lines = []
        started = False
        deadline = time.time() + CAPTURE_TIMEOUT_S

        while time.time() < deadline:
            line = ser.readline().decode("utf-8", errors="replace").strip()
            if not line:
                continue

            if line == "#RAW_BEGIN":
                started = True
                continue
            if not started:
                continue
            if line == "#RAW_END":
                break

            if line.startswith("#"):
                key, _, value = line[1:].partition("=")
                meta[key] = value
            else:
                csv_lines.append(line)
        else:
            raise SystemExit(
                "Timed out waiting for the dump. Is the ct_clamp sketch flashed?"
            )

    if not csv_lines:
        raise SystemExit("Capture started but no samples arrived.")

    print(f"Got {len(csv_lines) - 1} samples")
    return meta, "\n".join(csv_lines)


def analyse(df, meta):
    """Add volts and amps columns, and return the summary figures."""
    volts_per_count = float(meta.get("volts_per_count", 6.25e-5))
    amps_per_volt = float(meta.get("amps_per_volt", 100.0))
    mains_voltage = float(meta.get("mains_voltage", 240.0))

    df["seconds"] = df["micros"] / 1e6
    df["volts"] = df["counts"] * volts_per_count
    df["amps"] = df["volts"] * amps_per_volt

    ac = df["amps"] - df["amps"].mean()
    intervals = np.diff(df["micros"])

    summary = {
        "n": len(df),
        "duration_s": df["seconds"].iloc[-1],
        "mean_interval_us": intervals.mean(),
        "jitter_us": intervals.std(),
        "sps": 1e6 / intervals.mean(),
        "offset_amps": df["amps"].mean(),
        "rms_amps": float(np.sqrt((ac ** 2).mean())),
        "peak_to_peak_amps": df["amps"].max() - df["amps"].min(),
        "mains_voltage": mains_voltage,
    }
    summary["apparent_watts"] = summary["rms_amps"] * mains_voltage
    return ac, summary


# How far the tallest bin must stand above the typical bin before we call it
# a real frequency rather than the tallest lump in a flat noise floor.
PEAK_RATIO = 5.0


def dominant_frequency(ac, sps):
    """Return (freqs, magnitudes, peak_hz) for the AC-coupled signal.

    peak_hz is None when the spectrum is flat, which is what an empty clamp
    looks like. Without that check the tallest noise bin gets reported as if
    it meant something.
    """
    window = np.hanning(len(ac))
    spectrum = np.abs(np.fft.rfft(ac * window))
    freqs = np.fft.rfftfreq(len(ac), d=1.0 / sps)

    if len(freqs) < 2:
        return freqs, spectrum, None

    # Ignore the DC bin when looking for the peak.
    idx = np.argmax(spectrum[1:]) + 1
    median = np.median(spectrum[1:])
    if median <= 0 or spectrum[idx] / median < PEAK_RATIO:
        return freqs, spectrum, None

    return freqs, spectrum, freqs[idx]


def plot(df, ac, summary, freqs, spectrum, peak_hz, out_path, label):
    fig, axes = plt.subplots(3, 1, figsize=(12, 11))

    title = "CT clamp raw waveform"
    if label:
        title += f" - {label}"
    fig.suptitle(title, fontsize=14)

    ms = df["seconds"] * 1000

    # --- whole capture ---
    ax = axes[0]
    ax.plot(ms, df["amps"], color="teal", linewidth=0.8)
    ax.axhline(summary["offset_amps"], color="orange", linestyle="--", linewidth=1,
               label=f"offset {summary['offset_amps']:.3f} A")
    ax.set_xlabel("Time (ms)")
    ax.set_ylabel("Current (A)")
    ax.set_title(
        f"Full capture: {summary['n']} samples over {summary['duration_s'] * 1000:.0f} ms "
        f"at {summary['sps']:.0f} SPS"
    )
    ax.legend(loc="upper right")
    ax.grid(True, alpha=0.3)

    # --- zoom, with the individual samples visible ---
    ax = axes[1]
    zoom = df[ms <= ZOOM_MS]
    ax.plot(zoom["seconds"] * 1000, zoom["amps"], color="teal", linewidth=1.2)
    ax.plot(zoom["seconds"] * 1000, zoom["amps"], "o", color="darkorange",
            markersize=3, label="ADC samples")
    ax.axhline(summary["offset_amps"], color="orange", linestyle="--", linewidth=1)
    ax.set_xlabel("Time (ms)")
    ax.set_ylabel("Current (A)")
    ax.set_title(f"First {ZOOM_MS} ms, every sample shown")
    ax.legend(loc="upper right")
    ax.grid(True, alpha=0.3)

    # --- spectrum ---
    ax = axes[2]
    ax.plot(freqs, spectrum, color="firebrick", linewidth=0.9)
    if peak_hz is None:
        ax.plot([], [], " ", label="flat, no dominant frequency")
    else:
        ax.axvline(peak_hz, color="orange", linestyle="--", linewidth=1,
                   label=f"peak at {peak_hz:.1f} Hz")
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("Magnitude")
    ax.set_title("Spectrum (anything above half the sample rate is aliased)")
    ax.set_xlim(0, summary["sps"] / 2)
    ax.legend(loc="upper right")
    ax.grid(True, alpha=0.3)

    plt.tight_layout(rect=[0, 0, 1, 0.97])
    plt.savefig(out_path, dpi=150)
    print(f"Saved plot to {out_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="Serial port, otherwise auto-detected")
    parser.add_argument("--label", default="", help="Name for this capture, e.g. kettle")
    args = parser.parse_args()

    port = args.port or find_port()
    meta, csv_text = capture(port)

    df = pd.read_csv(io.StringIO(csv_text))
    ac, summary = analyse(df, meta)
    freqs, spectrum, peak_hz = dominant_frequency(ac, summary["sps"])

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = f"{stamp}_{args.label}" if args.label else stamp
    csv_path = os.path.join(OUTPUT_DIR, f"waveform_{slug}.csv")
    png_path = os.path.join(OUTPUT_DIR, f"waveform_{slug}.png")

    df.to_csv(csv_path, index=False)
    print(f"Saved data to {csv_path}")

    print()
    print(f"  Samples          {summary['n']}")
    print(f"  Sample rate      {summary['sps']:.1f} SPS "
          f"(interval {summary['mean_interval_us']:.0f} us, "
          f"jitter {summary['jitter_us']:.1f} us)")
    print(f"  DC offset        {summary['offset_amps']:.4f} A "
          f"(should be near zero on a differential read)")
    print(f"  RMS current      {summary['rms_amps']:.3f} A")
    print(f"  Peak to peak     {summary['peak_to_peak_amps']:.3f} A")
    freq_text = "none, spectrum is flat" if peak_hz is None else f"{peak_hz:.1f} Hz"
    print(f"  Dominant freq    {freq_text}")
    print(f"  Apparent power   {summary['apparent_watts']:.0f} VA "
          f"at {summary['mains_voltage']:.0f} V")
    print()

    plot(df, ac, summary, freqs, spectrum, peak_hz, png_path, args.label)


if __name__ == "__main__":
    main()
