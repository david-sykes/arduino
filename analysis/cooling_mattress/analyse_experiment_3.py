"""
Thermal analysis of experiment 3: cooling mattress with body load.

System: two pump loops share a ~1L reservoir (tupperware, poorly insulated).
  - Mattress loop: body → reservoir
  - Cooling loop: reservoir → cold bath (beer cooler, well insulated, 4kg ice + 5L water)

Protocol: both loops on while lying on mattress. At reading 80 (~0.66h),
cooling loop switched off, mattress loop kept running.

We fit the phase-2 (cooling-off) reservoir data to a Newton's-law exponential
to extract the body heat transfer rate and ambient heat leak coefficient,
then back-compute the cooling loop transfer rate from phase-1 data.
"""

import csv
import os
import re

import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import curve_fit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
M_RES = 1.0          # kg, reservoir water mass
M_BATH = 9.0         # kg, cold bath water mass (4 kg ice melted + 5 L water)
C_WATER = 4186.0     # J/(kg·°C)
T_AMB = 25.0         # °C, ambient room temperature
DT_READING = 30.0    # seconds between readings
PHASE2_START = 80    # reading number where cooling loop was switched off

LOGS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "logs")
OUTPUT_DIR = os.path.dirname(__file__)

# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------
def parse_log(filepath):
    readings = {}
    current_reading = None
    with open(filepath) as f:
        for line in f:
            m = re.search(r"Reading #(\d+)", line)
            if m:
                current_reading = int(m.group(1))
                continue
            m = re.match(r"\s+Sensor \d+ \[([A-F0-9]+)\]: ([\d.]+)°C", line)
            if m and current_reading is not None:
                readings.setdefault(current_reading, {})[m.group(1)] = float(m.group(2))
    return readings


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
def phase2_model(t, T_eq, tau, T_0):
    """Exponential approach to equilibrium: T(t) = T_eq - (T_eq - T_0)*exp(-t/tau)"""
    return T_eq - (T_eq - T_0) * np.exp(-t / tau)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    filepath = os.path.join(LOGS_DIR, "experiment_3_output.txt")
    raw = parse_log(filepath)

    # Build arrays — average the two reservoir sensors
    reading_nums = sorted(raw.keys())
    t_sec = np.array([(r - reading_nums[0]) * DT_READING for r in reading_nums])
    t_hours = t_sec / 3600.0

    res_temp = np.array([
        0.5 * (raw[r].get("287A1A780000003C", np.nan) +
               raw[r].get("28E7477B00000036", np.nan))
        for r in reading_nums
    ])
    bath_temp = np.array([raw[r].get("28F3A47C000000A3", np.nan) for r in reading_nums])

    reading_arr = np.array(reading_nums)

    # -----------------------------------------------------------------------
    # Phase 2 fit: readings >= PHASE2_START, cooling loop OFF
    # -----------------------------------------------------------------------
    mask2 = reading_arr >= PHASE2_START
    t2 = t_sec[mask2] - t_sec[mask2][0]  # time relative to phase-2 start
    T2 = res_temp[mask2]

    # Initial guesses: T_eq ~ 30 (above ambient if body keeps heating),
    #                  tau ~ 3000s, T_0 ~ first phase-2 reading
    p0 = [30.0, 3000.0, T2[0]]
    popt, pcov = curve_fit(phase2_model, t2, T2, p0=p0, maxfev=10000)
    T_eq_fit, tau_fit, T0_fit = popt
    perr = np.sqrt(np.diag(pcov))

    # Extract physical quantities
    h_amb = M_RES * C_WATER / tau_fit        # W/°C
    Q_body = h_amb * (T_eq_fit - T_AMB)      # W

    print("=" * 60)
    print("EXPERIMENT 3 — THERMAL ANALYSIS")
    print("=" * 60)
    print()
    print("Phase 2 fit (cooling loop OFF, readings 80–140):")
    print(f"  T_eq   = {T_eq_fit:.1f} ± {perr[0]:.1f} °C")
    print(f"  tau    = {tau_fit:.0f} ± {perr[1]:.0f} s  ({tau_fit/60:.1f} min)")
    print(f"  T_0    = {T0_fit:.2f} ± {perr[2]:.2f} °C")
    print()
    print("Derived quantities:")
    print(f"  h_amb  = m·c/tau = {h_amb:.2f} W/°C")
    print(f"           (ambient heat leak coefficient for the tupperware reservoir)")
    print(f"  Q_body = h_amb·(T_eq - T_amb) = {Q_body:.1f} W")
    print(f"           (heat transfer rate from body through mattress loop)")
    print()

    # -----------------------------------------------------------------------
    # Phase 1: readings 34–79 (near equilibrium, both loops on)
    # -----------------------------------------------------------------------
    mask1 = (reading_arr >= 34) & (reading_arr < PHASE2_START)
    t1 = t_sec[mask1]
    T1 = res_temp[mask1]

    # Compute dT/dt via central differences
    dTdt_1 = np.gradient(T1, t1)

    # Q_cool at each point: Q_body + h_amb*(T_amb - T) - m*c*dT/dt
    Q_cool_arr = Q_body + h_amb * (T_AMB - T1) - M_RES * C_WATER * dTdt_1

    Q_cool_mean = np.mean(Q_cool_arr)
    Q_cool_std = np.std(Q_cool_arr)

    print("Phase 1 (both loops on, readings 34–79):")
    print(f"  Q_cool = {Q_cool_mean:.1f} ± {Q_cool_std:.1f} W")
    print(f"           (heat transfer rate from reservoir to cold bath)")
    print()

    # -----------------------------------------------------------------------
    # Cold bath cross-check
    # -----------------------------------------------------------------------
    # Phase 1: cold bath warming rate → independent Q_cool estimate
    bath1 = bath_temp[mask1]
    dTdt_bath1 = np.gradient(bath1, t1)
    Q_cool_bath = M_BATH * C_WATER * dTdt_bath1
    Q_cool_bath_mean = np.mean(Q_cool_bath)

    # Phase 2: cold bath warming rate (should be small — insulated cooler)
    bath2 = bath_temp[mask2]
    t2_abs = t_sec[mask2]
    dTdt_bath2 = np.gradient(bath2, t2_abs)
    Q_leak_bath = M_BATH * C_WATER * np.mean(dTdt_bath2)

    print("Cold bath cross-check (beer cooler):")
    print(f"  Phase 1 cold bath warming → Q_cool ≈ {Q_cool_bath_mean:.1f} W")
    print(f"    (includes ~{Q_leak_bath:.1f} W ambient leak into cooler)")
    print(f"  Phase 2 cold bath warming → ambient leak ≈ {Q_leak_bath:.1f} W")
    print(f"  Corrected Q_cool ≈ {Q_cool_bath_mean - Q_leak_bath:.1f} W")
    print()

    # -----------------------------------------------------------------------
    # Build model for full time range
    # -----------------------------------------------------------------------
    # Phase 1 model: solve ODE  m·c·dT/dt = Q_body + h_amb*(T_amb - T) - Q_cool
    # This is T(t) = T_eq1 - (T_eq1 - T_0)*exp(-t/tau)
    # where T_eq1 = T_amb + (Q_body - Q_cool)/h_amb
    T_eq1 = T_AMB + (Q_body - Q_cool_mean) / h_amb
    model_res = np.full_like(t_sec, np.nan, dtype=float)

    # Phase 1: from first reading to phase-2 start
    for i, r in enumerate(reading_nums):
        if r < PHASE2_START:
            t_rel = t_sec[i] - t_sec[0]
            model_res[i] = T_eq1 - (T_eq1 - res_temp[0]) * np.exp(-t_rel / tau_fit)
        else:
            t_rel = t_sec[i] - t_sec[mask2][0]
            model_res[i] = T_eq_fit - (T_eq_fit - T0_fit) * np.exp(-t_rel / tau_fit)

    # -----------------------------------------------------------------------
    # CSV output
    # -----------------------------------------------------------------------
    csv_path = os.path.join(OUTPUT_DIR, "experiment_3_data.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "reading", "time_s", "time_hours",
            "reservoir_measured_C", "cold_bath_measured_C",
            "reservoir_model_C", "phase",
        ])
        for i, r in enumerate(reading_nums):
            phase = "both_loops" if r < PHASE2_START else "mattress_only"
            writer.writerow([
                r, f"{t_sec[i]:.0f}", f"{t_hours[i]:.4f}",
                f"{res_temp[i]:.2f}", f"{bath_temp[i]:.2f}",
                f"{model_res[i]:.2f}", phase,
            ])
    print(f"CSV saved to {csv_path}")

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Body → mattress → reservoir:   {Q_body:6.1f} W")
    print(f"  Ambient → reservoir:           {h_amb * (T_AMB - np.mean(T1)):6.1f} W  (at avg phase-1 reservoir temp {np.mean(T1):.1f}°C)")
    print(f"  Reservoir → cold bath:         {Q_cool_mean:6.1f} W  (from reservoir energy balance)")
    print(f"  Reservoir → cold bath:         {Q_cool_bath_mean - Q_leak_bath:6.1f} W  (from cold bath warming)")
    print()

    # -----------------------------------------------------------------------
    # Plot: data vs model
    # -----------------------------------------------------------------------
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

    # Top: temperature data + model
    ax1.plot(t_hours, res_temp, "o", color="orange", markersize=3, alpha=0.7,
             label="Reservoir (measured)")
    ax1.plot(t_hours, bath_temp, "o", color="red", markersize=3, alpha=0.7,
             label="Cold bath (measured)")
    ax1.plot(t_hours, model_res, "k-", linewidth=2, alpha=0.8,
             label=f"Reservoir model (τ={tau_fit/60:.0f}min, T_eq={T_eq_fit:.1f}°C)")

    ax1.axvline(t_sec[mask2][0] / 3600, color="gray", linestyle=":", alpha=0.7,
                label="Cooling loop OFF")
    ax1.axhline(T_AMB, color="green", linestyle=":", alpha=0.5, label=f"Ambient ({T_AMB}°C)")

    ax1.set_ylabel("Temperature (°C)")
    ax1.set_title("Experiment 3 — Data vs Model")
    ax1.legend(loc="upper right", fontsize=8)
    ax1.grid(True, alpha=0.3)

    # Bottom: energy transfer rates
    t1_hours = t1 / 3600.0
    ax2.plot(t1_hours, Q_cool_arr, color="blue", label=f"Q_cool (reservoir→bath): mean {Q_cool_mean:.0f}W")
    Q_amb_1 = h_amb * (T_AMB - T1)
    ax2.plot(t1_hours, Q_amb_1, color="green", label="Q_ambient→reservoir")
    ax2.axhline(Q_body, color="red", linestyle="--", label=f"Q_body = {Q_body:.0f}W")

    t2_hours = t_sec[mask2] / 3600.0
    Q_amb_2 = h_amb * (T_AMB - res_temp[mask2])
    ax2.plot(t2_hours, Q_amb_2, color="green")

    ax2.axvline(t_sec[mask2][0] / 3600, color="gray", linestyle=":", alpha=0.7)
    ax2.axhline(0, color="black", linewidth=0.5)
    ax2.set_xlabel("Time (hours)")
    ax2.set_ylabel("Power (W)")
    ax2.legend(loc="upper right", fontsize=8)
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    outpath = os.path.join(OUTPUT_DIR, "experiment_3_analysis.png")
    plt.savefig(outpath, dpi=150)
    print(f"Plot saved to {outpath}")


if __name__ == "__main__":
    main()
