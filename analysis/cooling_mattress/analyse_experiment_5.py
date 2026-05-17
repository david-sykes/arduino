"""
Analyse experiment 5 — energy-flow decomposition.

Rather than try to model the cooling coil (whose effective heat transfer
depends on bath stratification we can't predict from sensor data), we:

  1. Fit physical heat-transfer coefficients on the three "isolated" segments
     where each term is the only thing happening:
        h_leak       ← cool_off_mattress_off          (just insulation)
        UA_pad_amb   ← cool_off_mattress_on           (pad ↔ ambient via flow)
        UA_pad_body  ← cool_off_mattress_on_human     (pad ↔ body via flow)

  2. Compute each timestep's heat flows directly:
        Q_amb        = h_leak · (T_amb − T_res)                       always
        Q_pad_amb    = ε_pad_amb · ṁ_matt·c · (T_amb − T_res)         when mattress=True ∧ ¬human
        Q_pad_body   = ε_pad_body · ṁ_matt·c · (T_skin − T_res)       when mattress=True ∧ human

  3. Where cooling is active, BACK OUT the cooling watts from the energy
     balance against the measured reservoir temperature:
        Q_total_empirical = m_res·c · ΔT_res_meas/Δt                  every step
        Q_cool_empirical  = Q_total_empirical − (Q_amb + Q_pad_*)     when cool=True
     This sidesteps the bath stratification problem entirely — we don't
     predict cooling, we observe it.

Two-panel plot: measured temperatures (top), heat-flow components (bottom).
"""

import json
import os
import shutil
from dataclasses import dataclass
from math import exp

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(__file__)
MQTT_SOURCE = os.path.join(SCRIPT_DIR, "..", "..", "mqtt_server", "data", "mqtt.jsonl")
MQTT_COPY = os.path.join(SCRIPT_DIR, "experiment_5_mqtt.jsonl")
CSV_PATH = os.path.join(SCRIPT_DIR, "experiment_5_data.csv")
PLOT_PATH = os.path.join(SCRIPT_DIR, "experiment_5_analysis.png")

ADDR_BATH = "287A1A780000003C"
ADDR_RES = "28F3A47C000000A3"
ADDR_AMB = "28E7477B00000036"

C_WATER = 4186.0                  # J/(kg·K)
M_RES = 4.0                       # kg
T_SKIN = 33.0                     # °C
DT_STEP = 30.0                    # s
COOLING_FLOW_RATE = 1.0 / 30.0    # kg/s (used for context only — Q_cool is backed out)
MATTRESS_FLOW_RATE = 1.0 / 30.0   # kg/s

PARAM_DEFAULTS = {
    "h_leak":      1.0,           # W/K, reservoir → ambient
    "UA_pad_amb":  5.0,           # W/K, pad surface → ambient (advective)
    "UA_pad_body": 30.0,          # W/K, pad → body skin (advective)
}
PARAM_ORDER = list(PARAM_DEFAULTS.keys())


@dataclass
class Segment:
    name: str | None
    start: int | None
    cool: bool = False
    mattress: bool = False
    human: bool = False
    fit: bool = True


# Fit only segments where exactly the term we want is isolated — no cooling and
# no other compound. Cool segments are NOT fit; we back out their Q_cool from data.
SEGMENTS: list[Segment] = [
    Segment("cool_off_mattress_off_precool", start=0,                                                     fit=False),
    Segment("cool_on_mattress_off",          start=33,  cool=True,                                        fit=False),
    Segment("cool_off_mattress_off",         start=63,                                                    fit=True),
    Segment(None,                            start=98,  mattress=True,                                    fit=False),  # slug
    Segment("cool_off_mattress_on",          start=104, mattress=True,                                    fit=True),
    Segment("cool_on_mattress_off_2",        start=168, cool=True,                                        fit=False),
    Segment(None,                            start=202, mattress=True, human=True,                        fit=False),  # slug
    Segment("cool_off_mattress_on_human",    start=208, mattress=True, human=True,                        fit=True),
    Segment("cool_on_mattress_on_human",     start=242, cool=True, mattress=True, human=True,             fit=False),
]


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def load_mqtt_data() -> pd.DataFrame:
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
            for sensor in payload["sensors"]:
                rows.append({
                    "timestamp": ts,
                    "measurement": payload["measurement"],
                    "sensor_addr": sensor["addr"],
                    "temp_c": sensor["temp"],
                })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    t0 = df["timestamp"].min()
    df["time_s"] = (df["timestamp"] - t0).dt.total_seconds()
    df["time_hours"] = df["time_s"] / 3600.0
    return df


def widen(df: pd.DataFrame) -> pd.DataFrame:
    out = (
        df.pivot_table(index="measurement", columns="sensor_addr", values="temp_c").rename(
            columns={ADDR_BATH: "T_bath", ADDR_RES: "T_res", ADDR_AMB: "T_amb"}
        )
    )
    out["time_hours"] = df.groupby("measurement")["time_hours"].first()
    return out.dropna(subset=["T_bath", "T_res", "T_amb", "time_hours"]).sort_index()


def save_csv(wide: pd.DataFrame) -> None:
    wide.to_csv(CSV_PATH)
    print(f"CSV saved to {CSV_PATH} ({len(wide)} rows)")


def segment_for(measurement: int) -> Segment:
    current = SEGMENTS[0]
    for seg in SEGMENTS:
        if seg.start is not None and seg.start <= measurement:
            current = seg
    return current


def _segment_measurements(seg: Segment, idx: int, wide: pd.DataFrame) -> list[int]:
    if seg.start is None:
        return []
    end = None
    for nxt in SEGMENTS[idx + 1:]:
        if nxt.start is not None:
            end = nxt.start
            break
    return [m for m in wide.index if m >= seg.start and (end is None or m < end)]


# ---------------------------------------------------------------------------
# Physics terms (always evaluated against measured T_res)
# ---------------------------------------------------------------------------
def q_amb(T_amb, T_res, params):
    return params["h_leak"] * (T_amb - T_res)


def q_pad_amb(T_amb, T_res, params):
    eps = 1.0 - exp(-params["UA_pad_amb"] / (MATTRESS_FLOW_RATE * C_WATER))
    return eps * MATTRESS_FLOW_RATE * C_WATER * (T_amb - T_res)


def q_pad_body(T_res, params):
    eps = 1.0 - exp(-params["UA_pad_body"] / (MATTRESS_FLOW_RATE * C_WATER))
    return eps * MATTRESS_FLOW_RATE * C_WATER * (T_SKIN - T_res)


# ---------------------------------------------------------------------------
# Per-segment T_res prediction (no cooling term — used only for fit segments)
# ---------------------------------------------------------------------------
def simulate_segment(seg: Segment, idx: int, wide: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Integrate T_res across one segment using only h_leak + pad terms.
    Used for fitting the three isolated segments; cool segments are not
    simulated here (their dT_res is observed and Q_cool is the residual)."""
    ms = _segment_measurements(seg, idx, wide)
    if not ms:
        return pd.DataFrame()

    T_res = float(wide.loc[ms[0], "T_res"])
    res_c = M_RES * C_WATER

    rows = []
    for m in ms:
        T_amb = float(wide.loc[m, "T_amb"])
        rows.append({"measurement": m, "T_res_pred": T_res})

        Q = q_amb(T_amb, T_res, params)
        if seg.mattress:
            if seg.human:
                Q += q_pad_body(T_res, params)
            else:
                Q += q_pad_amb(T_amb, T_res, params)
        # No cool term in the prediction — by design.

        T_res += Q * DT_STEP / res_c

    return pd.DataFrame(rows).set_index("measurement")


# ---------------------------------------------------------------------------
# Joint fit on the three isolated segments
# ---------------------------------------------------------------------------
def joint_fit(wide: pd.DataFrame, params0: dict) -> dict:
    from scipy.optimize import minimize

    fit_segs = [(idx, seg) for idx, seg in enumerate(SEGMENTS)
                if seg.fit and seg.name is not None and seg.start is not None]

    def cost(x):
        if any(v < 1e-3 for v in x):
            return 1e9
        trial = {name: float(v) for name, v in zip(PARAM_ORDER, x)}
        sse, n = 0.0, 0
        for idx, seg in fit_segs:
            pred = simulate_segment(seg, idx, wide, trial)
            if pred.empty:
                continue
            diff = pred["T_res_pred"].values - wide.loc[pred.index, "T_res"].values
            sse += float(np.sum(diff ** 2))
            n += len(diff)
        return float(np.sqrt(sse / n)) if n > 0 else 1e9

    x0 = [params0[name] for name in PARAM_ORDER]
    result = minimize(cost, x0, method="Nelder-Mead",
                      options={"xatol": 1e-4, "fatol": 1e-4, "maxiter": 10000})
    fitted = {name: float(v) for name, v in zip(PARAM_ORDER, result.x)}
    print(f"\nJoint-fit RMSE on T_res = {result.fun:.3f} °C  ({len(fit_segs)} segments)")
    return fitted


# ---------------------------------------------------------------------------
# Per-timestep heat-flow decomposition (the main output)
# ---------------------------------------------------------------------------
def compute_flows(wide: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Decompose total dT_res/dt into model terms (Q_amb, Q_pad_*) and the
    residual Q_cool (only meaningful when cool=True). All evaluated against
    MEASURED T_res — no integration."""
    res_c = M_RES * C_WATER
    measurements = list(wide.index)
    T_res_vals = wide["T_res"].values

    out = []
    for i, m in enumerate(measurements):
        seg = segment_for(m)
        T_amb = float(wide.loc[m, "T_amb"])
        T_res = float(T_res_vals[i])

        Q_amb_val = q_amb(T_amb, T_res, params)
        Q_pad_val = 0.0
        if seg.mattress:
            Q_pad_val = q_pad_body(T_res, params) if seg.human else q_pad_amb(T_amb, T_res, params)

        # Empirical total from finite difference of measured T_res
        if i == 0:
            Q_emp_total = float("nan")
        else:
            Q_emp_total = res_c * (T_res_vals[i] - T_res_vals[i - 1]) / DT_STEP

        # Back-out Q_cool wherever cool is active
        Q_cool_val = float("nan")
        if seg.cool and not np.isnan(Q_emp_total):
            Q_cool_val = Q_emp_total - Q_amb_val - Q_pad_val

        out.append({
            "measurement": m,
            "Q_amb": Q_amb_val,
            "Q_pad": Q_pad_val,
            "Q_cool": Q_cool_val,
            "Q_emp_total": Q_emp_total,
        })

    return pd.DataFrame(out).set_index("measurement")


def report_segment_rmse(wide: pd.DataFrame) -> None:
    """For fit segments only — report how well the (non-cool) physics matches measured T_res."""
    print("\nFit-segment T_res RMSE:")
    for idx, seg in enumerate(SEGMENTS):
        if seg.name is None or seg.start is None or not seg.fit:
            continue
        pred = simulate_segment(seg, idx, wide, PARAMS_GLOBAL)
        if pred.empty:
            continue
        diff = pred["T_res_pred"].values - wide.loc[pred.index, "T_res"].values
        rmse = float(np.sqrt(np.mean(diff ** 2)))
        print(f"  {seg.name:38s}: RMSE = {rmse:.3f} °C")


PARAMS_GLOBAL: dict = {}  # filled in main()


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------
def plot(wide: pd.DataFrame, flows: pd.DataFrame, params: dict) -> None:
    fig, (ax_t, ax_q_small, ax_q_big) = plt.subplots(
        3, 1, figsize=(13, 11), sharex=True,
        gridspec_kw={"height_ratios": [3, 2, 2]},
    )
    t = wide["time_hours"].values

    # ---- Top: temperatures + model overlay on fit segments ----
    ax_t.plot(t, wide["T_bath"].values, "o-", color="blue", markersize=3, alpha=0.7, label="Bath (meas)")
    ax_t.plot(t, wide["T_res"].values, "o-", color="orange", markersize=3, alpha=0.8, label="Reservoir (meas)")
    ax_t.plot(t, wide["T_amb"].values, "o-", color="green", markersize=3, alpha=0.7, label="Ambient (meas)")

    # Overlay the model T_res prediction for the three FIT segments only
    fit_label_shown = False
    for idx, seg in enumerate(SEGMENTS):
        if not seg.fit or seg.name is None or seg.start is None:
            continue
        pred = simulate_segment(seg, idx, wide, params)
        if pred.empty:
            continue
        ts = [wide.loc[m, "time_hours"] for m in pred.index]
        ax_t.plot(ts, pred["T_res_pred"].values, "x--", color="black",
                  markersize=4, alpha=0.8,
                  label="Reservoir (model, fit segs)" if not fit_label_shown else None)
        fit_label_shown = True

    ax_t.set_ylabel("Temperature (°C)")
    ax_t.legend(loc="lower left", fontsize=9)
    ax_t.grid(True, alpha=0.3)
    param_str = ", ".join(f"{k}={v:.2f}" for k, v in params.items())
    ax_t.set_title(f"Experiment 5 — energy-flow decomposition\n{param_str}")

    # ---- Middle: small flows (Q_amb, Q_pad) ----
    ax_q_small.axhline(0, color="black", linewidth=0.5)
    ax_q_small.plot(t, flows["Q_amb"].values, "-", color="green", label="Q_amb (model)", linewidth=1.5)
    ax_q_small.plot(t, flows["Q_pad"].values, "-", color="red",   label="Q_pad (model)", linewidth=1.5)
    ax_q_small.set_ylabel("Heat flow (W)\n[ambient + pad]")
    ax_q_small.legend(loc="lower left", fontsize=9)
    ax_q_small.grid(True, alpha=0.3)

    # ---- Bottom: big flows (Q_cool, Q_total) ----
    ax_q_big.axhline(0, color="black", linewidth=0.5)
    ax_q_big.plot(t, flows["Q_cool"].values,      "-", color="blue",  label="Q_cool (back-out)",    linewidth=1.5)
    ax_q_big.plot(t, flows["Q_emp_total"].values, "--", color="black", alpha=0.6,
                  label="Q_total (empirical)", linewidth=1.0)
    ax_q_big.set_xlabel("Time (hours)")
    ax_q_big.set_ylabel("Heat flow (W)\n[cooling + total]")
    ax_q_big.legend(loc="lower left", fontsize=9)
    ax_q_big.grid(True, alpha=0.3)

    # Segment markers on all panels
    for seg in SEGMENTS:
        if seg.name is None or seg.start is None or seg.start not in wide.index:
            continue
        t_h = wide.loc[seg.start, "time_hours"]
        for ax in (ax_t, ax_q_small, ax_q_big):
            ax.axvline(t_h, color="gray", linestyle="--", alpha=0.4)
        ax_t.text(t_h, ax_t.get_ylim()[1], f"  {seg.name}",
                  fontsize=8, color="gray", verticalalignment="top")

    plt.tight_layout()
    plt.savefig(PLOT_PATH, dpi=150)
    print(f"\nPlot saved to {PLOT_PATH}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    df = load_mqtt_data()
    if df.empty:
        print("No MQTT data found")
        return
    print(f"Loaded {len(df)} sensor readings ({df['measurement'].nunique()} measurements)")

    wide = widen(df)
    save_csv(wide)

    params = joint_fit(wide, PARAM_DEFAULTS)
    global PARAMS_GLOBAL
    PARAMS_GLOBAL = params

    print("\nFitted physical parameters:")
    for name, value in params.items():
        print(f"  {name:12s} = {value:7.3f} W/K")

    # Derived effective coefficients at this experiment's mattress flow rate
    mc_matt = MATTRESS_FLOW_RATE * C_WATER
    eps_pad_amb = 1.0 - np.exp(-params["UA_pad_amb"] / mc_matt)
    eps_pad_body = 1.0 - np.exp(-params["UA_pad_body"] / mc_matt)
    print(f"\n  mattress loop ṁ·c       = {mc_matt:6.1f} W/K  (h_max)")
    print(f"  ε_pad_amb               = {eps_pad_amb:.3f}  → effective h_pad  = {eps_pad_amb*mc_matt:6.2f} W/K")
    print(f"  ε_pad_body              = {eps_pad_body:.3f}  → effective h_body = {eps_pad_body*mc_matt:6.2f} W/K")

    report_segment_rmse(wide)

    flows = compute_flows(wide, params)

    # Summary of cooling watts in segments where it was backed out
    print("\nBacked-out cooling power (Q_cool) by segment:")
    for idx, seg in enumerate(SEGMENTS):
        if seg.name is None or seg.start is None or not seg.cool:
            continue
        ms = _segment_measurements(seg, idx, wide)
        if not ms:
            continue
        q_vals = flows.loc[ms, "Q_cool"].dropna().values
        if len(q_vals) == 0:
            continue
        print(f"  {seg.name:38s}: mean = {q_vals.mean():7.1f} W, "
              f"min = {q_vals.min():7.1f} W, max = {q_vals.max():7.1f} W")

    plot(wide, flows, params)


if __name__ == "__main__":
    main()
