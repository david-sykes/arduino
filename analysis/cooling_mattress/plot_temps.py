import re
import glob
import os
import pandas as pd
import matplotlib.pyplot as plt

LOGS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "logs")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "temperature_plot.png")

SENSOR_LABELS = {
    "287A1A780000003C": "Mattress feed tank",
    "28E7477B00000036": "Cooling bath",
    "28F3A47C000000A3": "Mattress feed tank",
}

SENSOR_COLORS = {
    "287A1A780000003C": "orange",
    "28E7477B00000036": "blue",
    "28F3A47C000000A3": "red",
}


def parse_log(filepath):
    rows = []
    current_reading = None
    with open(filepath) as f:
        for line in f:
            reading_match = re.match(r"Reading #(\d+)", line)
            if reading_match:
                current_reading = int(reading_match.group(1))
                continue

            sensor_match = re.match(
                r"\s+Sensor \d+ \[([A-F0-9]+)\]: ([\d.]+)°C", line
            )
            if sensor_match and current_reading is not None:
                rows.append({
                    "reading": current_reading,
                    "hours": (current_reading - 1) * 30 / 3600,
                    "sensor": sensor_match.group(1),
                    "temp_c": float(sensor_match.group(2)),
                })
    return pd.DataFrame(rows)


def main():
    log_files = sorted(glob.glob(os.path.join(LOGS_DIR, "*.txt")))
    if not log_files:
        print("No log files found in logs/")
        return

    latest = log_files[-1]
    print(f"Plotting: {latest}")

    df = parse_log(latest)
    if df.empty:
        print("No data parsed from log file")
        return

    fig, ax = plt.subplots(figsize=(12, 6))
    for sensor, group in df.groupby("sensor"):
        label = SENSOR_LABELS.get(sensor, sensor)
        color = SENSOR_COLORS.get(sensor)
        ax.plot(group["hours"], group["temp_c"], label=label, color=color)

    ax.set_xlabel("Time (hours)")
    ax.set_ylabel("Temperature (°C)")
    ax.set_title("Temperature Over Time")
    ax.legend(title="Sensor ID")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(OUTPUT_PATH, dpi=150)
    print(f"Saved plot to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
