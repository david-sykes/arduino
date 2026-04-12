"""
Analyse experiment 4: cooling mattress with MQTT sensor data.

Reads mqtt.jsonl (copied to avoid blocking writes), converts to CSV,
and plots temperature over time with phase annotations from the timelog.
"""

import json
import os
import re
import shutil

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(__file__)
MQTT_SOURCE = os.path.join(SCRIPT_DIR, "..", "..", "mqtt_server", "data", "mqtt.jsonl")
MQTT_COPY = os.path.join(SCRIPT_DIR, "experiment_4_mqtt.jsonl")
CSV_PATH = os.path.join(SCRIPT_DIR, "experiment_4_data.csv")
PLOT_PATH = os.path.join(SCRIPT_DIR, "experiment_4_analysis.png")
TIMELOG_PATH = os.path.join(SCRIPT_DIR, "experiment_4_timelog.txt")

SENSOR_LABELS = {
    "287A1A780000003C": "Cooling bath",
    "28F3A47C000000A3": "Mattress reservoir",
    "28E7477B00000036": "Ambient air",
}

SENSOR_COLORS = {
    "287A1A780000003C": "blue",
    "28F3A47C000000A3": "orange",
    "28E7477B00000036": "green",
}

# Phase boundaries (measurement numbers)
PHASE_BOUNDARIES = {
    1: 55,  # reservoir_cool: A3 starts dropping
    2: 73,  # ambient_warmup: no pumps, no human, reservoir warms to ambient
    3: 139, # slug_mixing: warm stagnant loop water mixes with cold reservoir (not modelled)
    4: 141, # human_heat: mattress loop on, cooling off, human on mattress
}

# ---------------------------------------------------------------------------
# Model parameters
# ---------------------------------------------------------------------------
M_RESERVOIR = 3.0       # kg, reservoir water mass
C_WATER = 4186.0        # J/(kg·K)
H_COOL = 59.0           # W/K, cooling loop coil-to-bath heat transfer (to fit)
H_AMB = 1.0             # W/K, reservoir ambient heat leak (fitted from phase 2)
H_LINE = 0.0            # W/K, cooling loop line loss — heat gained from ambient in return tubing (to fit)
H_BODY = 5.0            # W/K, body-to-mattress heat transfer (to fit)
T_SKIN = 33.0           # °C, skin surface temperature assumption
FLOW_RATE = 0.0625      # kg/s, pump flow rate (measured: 500ml in 8s)
DT_STEP = 30.0          # seconds between readings


def parse_timelog(path):
    """Parse timelog for phase boundaries. Returns list of (phase_name, description)."""
    phases = []
    with open(path) as f:
        for line in f:
            m = re.match(r"Phase (\d+) \((\w+)\): (.+)", line)
            if m:
                phases.append({
                    "phase_num": int(m.group(1)),
                    "phase_name": m.group(2),
                    "description": m.group(3).strip(),
                })
    return phases


def load_mqtt_data():
    """Copy mqtt.jsonl and parse into a DataFrame."""
    shutil.copy2(MQTT_SOURCE, MQTT_COPY)
    print(f"Copied MQTT data to {MQTT_COPY}")

    rows = []
    with open(MQTT_COPY) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
            if msg["topic"] != "thermometer/sensors":
                continue
            payload = json.loads(msg["payload"])
            ts = pd.Timestamp(msg["ts"])
            measurement = payload["measurement"]
            for sensor in payload["sensors"]:
                rows.append({
                    "timestamp": ts,
                    "measurement": measurement,
                    "sensor_addr": sensor["addr"],
                    "temp_c": sensor["temp"],
                })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # Compute time relative to first reading
    t0 = df["timestamp"].min()
    df["time_s"] = (df["timestamp"] - t0).dt.total_seconds()
    df["time_hours"] = df["time_s"] / 3600.0
    df["phase"] = "precool"

    return df


def save_csv(df):
    """Pivot sensor readings into columns and save as CSV."""
    pivot = df.pivot_table(
        index=["measurement", "timestamp", "time_s", "time_hours", "phase"],
        columns="sensor_addr",
        values="temp_c",
    ).reset_index()

    # Flatten column names
    pivot.columns = [
        SENSOR_LABELS.get(c, c).lower().replace(" ", "_") if c in SENSOR_LABELS else c
        for c in pivot.columns
    ]

    pivot.to_csv(CSV_PATH, index=False)
    print(f"CSV saved to {CSV_PATH} ({len(pivot)} rows)")
    return pivot


def model_phase1(df, h_cool=None, h_amb=None, h_line=None):
    """Phase 1 model: reservoir cools via cooling loop, warms via ambient + line loss.

    m*c*dT/dt = -h_cool*(T_res - T_bath) + h_amb*(T_amb - T_res) + h_line*(T_amb - T_bath)

    h_line term: cold return water in tubing gains heat from ambient air,
    proportional to (T_amb - T_bath). Reduces net cooling.
    """
    if h_cool is None:
        h_cool = H_COOL
    if h_amb is None:
        h_amb = H_AMB
    if h_line is None:
        h_line = H_LINE

    phase1_start = PHASE_BOUNDARIES[1]
    phase1_end = PHASE_BOUNDARIES.get(2, df["measurement"].max() + 1)

    bath = df[df["sensor_addr"] == "287A1A780000003C"].set_index("measurement")
    res = df[df["sensor_addr"] == "28F3A47C000000A3"].set_index("measurement")
    amb = df[df["sensor_addr"] == "28E7477B00000036"].set_index("measurement")

    measurements = sorted(
        m for m in bath.index.intersection(res.index).intersection(amb.index)
        if phase1_start <= m < phase1_end
    )
    if not measurements:
        return pd.DataFrame()

    T_res_model = res.loc[measurements[0], "temp_c"]
    results = []

    for m in measurements:
        T_bath_meas = bath.loc[m, "temp_c"]
        T_amb_meas = amb.loc[m, "temp_c"]
        t_hours = bath.loc[m, "time_hours"]

        results.append({
            "measurement": m,
            "time_hours": t_hours,
            "temp_model": T_res_model,
        })

        dTdt = (
            -h_cool * (T_res_model - T_bath_meas)
            + h_amb * (T_amb_meas - T_res_model)
            + h_line * (T_amb_meas - T_bath_meas)
        ) / (M_RESERVOIR * C_WATER)
        T_res_model += dTdt * DT_STEP

    return pd.DataFrame(results)


def model_phase2(df, h_amb=None):
    """Phase 2 model: reservoir warms via ambient only (no pumps).

    dT_res/dt = h_amb * (T_amb - T_res) / (m * c)
    """
    if h_amb is None:
        h_amb = H_AMB

    phase2_start = PHASE_BOUNDARIES[2]
    # Phase 2 runs to end of data (or next phase if added later)
    phase2_end = PHASE_BOUNDARIES.get(3, df["measurement"].max() + 1)

    res = df[df["sensor_addr"] == "28F3A47C000000A3"].set_index("measurement")
    amb = df[df["sensor_addr"] == "28E7477B00000036"].set_index("measurement")

    measurements = sorted(
        m for m in res.index.intersection(amb.index)
        if phase2_start <= m < phase2_end
    )
    if not measurements:
        return pd.DataFrame()

    T_res_model = res.loc[measurements[0], "temp_c"]
    results = []

    for m in measurements:
        T_amb_meas = amb.loc[m, "temp_c"]
        t_hours = res.loc[m, "time_hours"]

        results.append({
            "measurement": m,
            "time_hours": t_hours,
            "temp_model": T_res_model,
        })

        dTdt = h_amb * (T_amb_meas - T_res_model) / (M_RESERVOIR * C_WATER)
        T_res_model += dTdt * DT_STEP

    return pd.DataFrame(results)


def model_phase3(df, h_amb=None, h_body=None):
    """Phase 3 model: mattress loop on, human on mattress, no cooling.

    m*c*dT/dt = h_amb*(T_amb - T_res) + h_body*(T_skin - T_res)
    """
    if h_amb is None:
        h_amb = H_AMB
    if h_body is None:
        h_body = H_BODY

    phase3_start = PHASE_BOUNDARIES[4]
    phase3_end = PHASE_BOUNDARIES.get(5, df["measurement"].max() + 1)

    res = df[df["sensor_addr"] == "28F3A47C000000A3"].set_index("measurement")
    amb = df[df["sensor_addr"] == "28E7477B00000036"].set_index("measurement")

    measurements = sorted(
        m for m in res.index.intersection(amb.index)
        if phase3_start <= m < phase3_end
    )
    if not measurements:
        return pd.DataFrame()

    T_res_model = res.loc[measurements[0], "temp_c"]
    results = []

    for m in measurements:
        T_amb_meas = amb.loc[m, "temp_c"]
        t_hours = res.loc[m, "time_hours"]

        results.append({
            "measurement": m,
            "time_hours": t_hours,
            "temp_model": T_res_model,
        })

        dTdt = (
            h_amb * (T_amb_meas - T_res_model)
            + h_body * (T_SKIN - T_res_model)
        ) / (M_RESERVOIR * C_WATER)
        T_res_model += dTdt * DT_STEP

    return pd.DataFrame(results)


def plot(df, h_cool, h_amb, h_line, h_body):
    """Plot temperature over time for each sensor."""
    phases = parse_timelog(TIMELOG_PATH)

    fig, ax = plt.subplots(figsize=(12, 6))

    for sensor_addr, group in df.groupby("sensor_addr"):
        label = SENSOR_LABELS.get(sensor_addr, sensor_addr)
        color = SENSOR_COLORS.get(sensor_addr)
        ax.plot(
            group["time_hours"], group["temp_c"],
            "o-", color=color, markersize=3, alpha=0.7, label=label,
        )

    # Model overlays — combine phase 1 and 2 into one series
    model1 = model_phase1(df, h_cool=h_cool, h_amb=h_amb, h_line=h_line)
    model2 = model_phase2(df, h_amb=h_amb)
    model3 = model_phase3(df, h_amb=h_amb, h_body=h_body)
    model_all = pd.concat([model1, model2, model3], ignore_index=True)
    if not model_all.empty:
        ax.plot(
            model_all["time_hours"], model_all["temp_model"],
            "x--", color="gray", markersize=4, alpha=0.8,
            label=f"Model (h_cool={h_cool:.0f}, h_line={h_line:.0f} W/K)",
        )

    # Draw phase boundaries
    for phase_num, measurement in PHASE_BOUNDARIES.items():
        phase_rows = df[df["measurement"] == measurement]
        if phase_rows.empty:
            continue
        t_hours = phase_rows["time_hours"].iloc[0]
        phase_info = next((p for p in phases if p["phase_num"] == phase_num), None)
        phase_label = phase_info["phase_name"] if phase_info else f"phase {phase_num}"
        ax.axvline(t_hours, color="gray", linestyle="--", alpha=0.7)
        ax.text(
            t_hours, ax.get_ylim()[1], f"  {phase_label}",
            fontsize=8, color="gray", verticalalignment="top",
        )

    ax.set_xlabel("Time (hours)")
    ax.set_ylabel("Temperature (°C)")
    ax.set_title("Experiment 4 — Temperature Over Time")
    ax.legend(loc="lower left", fontsize=9)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(PLOT_PATH, dpi=150)
    print(f"Plot saved to {PLOT_PATH}")


def fit_model(df):
    """Fit h_amb from phase 2, then h_cool from phase 1 with h_amb fixed."""
    from scipy.optimize import minimize_scalar

    res = df[df["sensor_addr"] == "28F3A47C000000A3"].set_index("measurement")

    # Step 1: fit h_amb from phase 2 (ambient warmup only)
    def rmse_phase2(h_amb):
        if h_amb < 0:
            return 1e6
        model_df = model_phase2(df, h_amb=h_amb)
        if model_df.empty:
            return 1e6
        errors = []
        for _, row in model_df.iterrows():
            m = row["measurement"]
            if m in res.index:
                errors.append(row["temp_model"] - res.loc[m, "temp_c"])
        return np.sqrt(np.mean(np.array(errors)**2))

    result_amb = minimize_scalar(rmse_phase2, bounds=(0.1, 50), method="bounded")
    h_amb_fit = result_amb.x
    print(f"\nPhase 2 fit (h_amb only):")
    print(f"  h_amb  = {h_amb_fit:.2f} W/K")
    print(f"  RMSE   = {result_amb.fun:.2f} °C")

    # Step 2: fit h_cool and q_pump from phase 1 with h_amb fixed
    from scipy.optimize import minimize

    from scipy.optimize import minimize

    def rmse_phase1(params):
        h_cool, h_line = params
        if h_cool < 0 or h_line < 0:
            return 1e6
        model_df = model_phase1(df, h_cool=h_cool, h_amb=h_amb_fit, h_line=h_line)
        if model_df.empty:
            return 1e6
        errors = []
        for _, row in model_df.iterrows():
            m = row["measurement"]
            if m in res.index:
                errors.append(row["temp_model"] - res.loc[m, "temp_c"])
        return np.sqrt(np.mean(np.array(errors)**2))

    result_cool = minimize(rmse_phase1, [H_COOL, 5.0], method="Nelder-Mead")
    h_cool_fit, h_line_fit = result_cool.x
    print(f"\nPhase 1 fit (h_cool + h_line, h_amb fixed):")
    h_max = FLOW_RATE * C_WATER
    effectiveness = h_cool_fit / h_max

    print(f"  h_cool = {h_cool_fit:.1f} W/K  (coil to bath)")
    print(f"  h_line = {h_line_fit:.1f} W/K  (line loss from ambient)")
    print(f"  RMSE   = {result_cool.fun:.2f} °C")
    print(f"\n  Flow rate:    {FLOW_RATE*1000:.1f} ml/s ({FLOW_RATE*60*1000:.0f} ml/min)")
    print(f"  h_max (F*c):  {h_max:.0f} W/K  (perfect coil)")
    print(f"  Coil eff:     {effectiveness:.0%}")

    # Step 3: fit h_body from phase 3 with h_amb fixed
    def rmse_phase3(h_body):
        if h_body < 0:
            return 1e6
        model_df = model_phase3(df, h_amb=h_amb_fit, h_body=h_body)
        if model_df.empty:
            return 1e6
        errors = []
        for _, row in model_df.iterrows():
            m = row["measurement"]
            if m in res.index:
                errors.append(row["temp_model"] - res.loc[m, "temp_c"])
        return np.sqrt(np.mean(np.array(errors)**2))

    result_body = minimize_scalar(rmse_phase3, bounds=(0.1, 100), method="bounded")
    h_body_fit = result_body.x
    q_body_initial = h_body_fit * (T_SKIN - res.loc[PHASE_BOUNDARIES[4], "temp_c"])
    print(f"\nPhase 3 fit (h_body, h_amb fixed):")
    print(f"  h_body = {h_body_fit:.2f} W/K  (body to mattress)")
    print(f"  T_skin = {T_SKIN:.0f} °C     (assumed)")
    print(f"  Q_body = {q_body_initial:.0f} W at start of phase 3  (h_body × (T_skin - T_res))")
    print(f"  RMSE   = {result_body.fun:.2f} °C")

    return h_cool_fit, h_amb_fit, h_line_fit, h_body_fit


def evaluate_model(df, h_cool, h_amb, h_line):
    """Print model vs measured error for phase 1."""
    model_df = model_phase1(df, h_cool=h_cool, h_amb=h_amb, h_line=h_line)
    if model_df.empty:
        return

    res = df[df["sensor_addr"] == "28F3A47C000000A3"].set_index("measurement")
    errors = []
    for _, row in model_df.iterrows():
        m = row["measurement"]
        if m in res.index:
            errors.append(row["temp_model"] - res.loc[m, "temp_c"])

    errors = np.array(errors)
    print(f"\nPhase 1 model error (h_cool={h_cool:.1f}, h_amb={h_amb:.1f} W/K):")
    print(f"  RMSE:       {np.sqrt(np.mean(errors**2)):.2f} °C")
    print(f"  Mean error: {np.mean(errors):+.2f} °C")
    print(f"  Max error:  {np.max(np.abs(errors)):.2f} °C")


def main():
    df = load_mqtt_data()
    if df.empty:
        print("No MQTT data found")
        return

    print(f"Loaded {len(df)} sensor readings")
    save_csv(df)
    h_cool_fit, h_amb_fit, h_line_fit, h_body_fit = fit_model(df)
    evaluate_model(df, h_cool_fit, h_amb_fit, h_line_fit)
    plot(df, h_cool_fit, h_amb_fit, h_line_fit, h_body_fit)


if __name__ == "__main__":
    main()
