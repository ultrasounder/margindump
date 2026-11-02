"""Eye construction. Three kinds, never mixed on one axis without a label:

  model     DDR4 reference eye from SPD/DMI data rate (dual-Dirac jitter + Gaussian
            noise). It is a sanity reference - what a healthy DDR4-2666 eye looks like
            in the right units (UI = 1e6/data_rate ps; 2666 MT/s -> 375 ps, NOT the
            750 ps clock period).
  margin1d  a set of 1-D margins (left/right ps, up/down mV) per lane or rank: this is
            what PCIe LMR (pcilmr / pcie_lmt) and Intel RMT actually produce. Plotted
            as per-lane eye diamonds + bars against spec minimums.
  grid2d    a 2-D pass/fail or BER map (t_ps, v_mv, value) - imported from a CSV, e.g.
            an Intel Margin2D / oscilloscope export. Plotted as a heatmap.
"""
import csv
import math

import numpy as np
from scipy.special import erfc

from .pcie import LMR_MIN_TIMING_PS, LMR_MIN_VOLTAGE_MV


def _q(x):
    return 0.5 * erfc(x / math.sqrt(2.0))


def ui_ps_from_rate(data_rate_mts: float) -> float:
    return 1e6 / float(data_rate_mts)


def build_model_eye(data_rate_mts: float = 2666, vddq_mv: float = 1200,
                    target_ber: float = 1e-12, n: int = 181) -> dict:
    """Illustrative DDR4 Rx eye. Parameters are fractions of UI / VDDQ so the model
    scales with the SPD data rate; they are NOT measured on this DUT."""
    ui = ui_ps_from_rate(data_rate_mts)
    dj_pp = 0.26 * ui          # deterministic jitter, peak-peak (ISI+DCD+crosstalk)
    rj_sigma = 0.012 * ui      # random jitter, 1 sigma
    eye_h0 = 0.30 * vddq_mv    # open eye height at centre before noise (POD12 swing ~ 360 mV)
    dn_pp = 0.06 * vddq_mv     # deterministic noise, peak-peak
    vn_sigma = 0.006 * vddq_mv # random noise, 1 sigma
    t = np.linspace(-ui / 2, ui / 2, n)
    v = np.linspace(-eye_h0 * 0.9, eye_h0 * 0.9, n)
    T, V = np.meshgrid(t, v)
    # horizontal bathtub: distance from the nearest crossing
    m_t = ui / 2 - np.abs(T) - dj_pp / 2
    ber_t = _q(m_t / rj_sigma)
    # eye height closes toward the crossings (cosine-shaped envelope)
    h_t = eye_h0 * np.clip(np.cos(np.pi * T / ui), 0, None)
    m_v = h_t / 2 - np.abs(V) - dn_pp / 2
    ber_v = _q(m_v / vn_sigma)
    ber = np.clip(ber_t + ber_v, 1e-30, 0.5)   # union bound
    open_mask = ber <= target_ber
    width = float(np.ptp(t[open_mask.any(axis=0)])) if open_mask.any() else 0.0
    height = float(np.ptp(v[open_mask.any(axis=1)])) if open_mask.any() else 0.0
    return {"kind": "model", "label": "MODEL - not measured on this DUT",
            "interface": f"DDR4-{int(data_rate_mts)}", "ui_ps": ui, "vddq_mv": vddq_mv,
            "params": {"dj_pp_ps": dj_pp, "rj_sigma_ps": rj_sigma, "eye_h0_mv": eye_h0,
                       "dn_pp_mv": dn_pp, "vn_sigma_mv": vn_sigma, "target_ber": target_ber},
            "t_ps": t.tolist(), "v_mv": v.tolist(), "log10_ber": np.log10(ber).tolist(),
            "eye_width_ps": width, "eye_height_mv": height,
            "eye_width_ui": width / ui,
            # JEDEC DDR4 Rx mask is ~0.2 UI x ~130-140 mV at 2400-2666 MT/s (approx; see JESD79-4)
            "rx_mask_approx": {"t_ui": 0.2, "v_mv": 136}}


# ----------------------------------------------------------------------------- margin1d

def margin1d_from_pcilmr(bdf: str, lanes: list, cur_speed_gen: int) -> dict:
    ui = {4: 62.5, 5: 31.25, 6: 15.625}.get(cur_speed_gen, 62.5)
    entries = [{"id": f"lane{l['lane']}", "left_ps": l["left_ps"], "right_ps": l["right_ps"],
                "up_mv": l["up_mv"], "down_mv": l["down_mv"]} for l in lanes]
    return {"kind": "margin1d", "label": f"MEASURED - PCIe LMR via pcilmr on {bdf}",
            "source": "pcilmr", "unit_t": "ps", "unit_v": "mV", "ui_ps": ui,
            "min_timing_ps": LMR_MIN_TIMING_PS.get(cur_speed_gen),
            "min_voltage_mv": LMR_MIN_VOLTAGE_MV, "entries": entries}


def margin1d_from_csv(path: str, label: str = None) -> dict:
    """CSV contract: id,left_ps,right_ps,up_mv,down_mv[,min_t_ps,min_v_mv]
    (e.g. Intel RMT per-rank RxDqs-/RxDqs+/RxV-/RxV+ converted to ps/mV, or any
    pcie_lmt / ocp-diag-pci_lmt export flattened to one row per lane)."""
    entries, min_t, min_v = [], None, None
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            entries.append({"id": row["id"], "left_ps": float(row["left_ps"]),
                            "right_ps": float(row["right_ps"]), "up_mv": float(row["up_mv"]),
                            "down_mv": float(row["down_mv"])})
            min_t = float(row["min_t_ps"]) if row.get("min_t_ps") else min_t
            min_v = float(row["min_v_mv"]) if row.get("min_v_mv") else min_v
    return {"kind": "margin1d", "label": label or f"IMPORTED - {path}", "source": path,
            "unit_t": "ps", "unit_v": "mV", "min_timing_ps": min_t, "min_voltage_mv": min_v,
            "entries": entries}


def margin1d_simulated(kind: str = "pcie_gen4_x16", seed: int = 7) -> dict:
    """What the measured output looks like on hardware that can produce it."""
    rng = np.random.default_rng(seed)
    if kind.startswith("pcie"):
        n = 16 if "x16" in kind else 4
        ui = 62.5
        entries = []
        for i in range(n):
            base_t = rng.normal(24, 3)
            base_v = rng.normal(45, 8)
            if i == 11 and n == 16:   # one weak lane for the story
                base_t, base_v = 16.5, 22
            entries.append({"id": f"lane{i}", "left_ps": round(base_t + rng.normal(0, 1), 2),
                            "right_ps": round(base_t + rng.normal(0, 1), 2),
                            "up_mv": round(base_v + rng.normal(0, 2), 1),
                            "down_mv": round(base_v + rng.normal(0, 2), 1)})
        return {"kind": "margin1d", "label": f"SIMULATED - PCIe Gen4 {kind.split('_')[-1]} LMR",
                "source": "simulated", "unit_t": "ps", "unit_v": "mV", "ui_ps": ui,
                "min_timing_ps": LMR_MIN_TIMING_PS[4], "min_voltage_mv": LMR_MIN_VOLTAGE_MV,
                "entries": entries}
    # Intel RMT-style: per rank x byte lane, RxDqs +/- in ps and RxV +/- in mV
    ui = ui_ps_from_rate(2666)
    entries = []
    for rank in range(2):
        for byte in range(8):
            t = rng.normal(0.30 * ui, 0.02 * ui)
            v = rng.normal(95, 9)
            entries.append({"id": f"r{rank}b{byte}", "left_ps": round(t + rng.normal(0, 3), 1),
                            "right_ps": round(t + rng.normal(0, 3), 1),
                            "up_mv": round(v + rng.normal(0, 4), 1),
                            "down_mv": round(v + rng.normal(0, 4), 1)})
    return {"kind": "margin1d", "label": "SIMULATED - Intel RMT (DDR4-2666 Rx, per rank/byte)",
            "source": "simulated", "unit_t": "ps", "unit_v": "mV", "ui_ps": ui,
            "min_timing_ps": 0.2 * ui, "min_voltage_mv": 60.0, "entries": entries}


def summarize_margin1d(m: dict) -> dict:
    e = m["entries"]
    if not e:
        return {"count": 0}
    widths = np.array([x["left_ps"] + x["right_ps"] for x in e])
    heights = np.array([x["up_mv"] + x["down_mv"] for x in e])
    out = {"count": len(e), "width_ps_min": float(widths.min()), "width_ps_mean": float(widths.mean()),
           "height_mv_min": float(heights.min()), "height_mv_mean": float(heights.mean()),
           "weakest_timing": e[int(widths.argmin())]["id"],
           "weakest_voltage": e[int(heights.argmin())]["id"]}
    mt, mv = m.get("min_timing_ps"), m.get("min_voltage_mv")
    fails = []
    for x in e:
        if mt is not None and (x["left_ps"] < mt or x["right_ps"] < mt):
            fails.append(x["id"])
        elif mv is not None and (x["up_mv"] < mv or x["down_mv"] < mv):
            fails.append(x["id"])
    out["below_minimum"] = fails
    out["verdict"] = "FAIL" if fails else "PASS"
    return out


# ----------------------------------------------------------------------------- grid2d

def grid2d_from_csv(path: str) -> dict:
    """CSV contract: t_ps,v_mv,value  (value = 1 pass / 0 fail, or a BER, or an error
    count). Irregular points are binned onto the unique t/v values found."""
    ts, vs, vals = [], [], []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            ts.append(float(row["t_ps"]))
            vs.append(float(row["v_mv"]))
            vals.append(float(row.get("value", row.get("margin", row.get("ber", "nan")))))
    t_axis, v_axis = sorted(set(ts)), sorted(set(vs))
    grid = np.full((len(v_axis), len(t_axis)), np.nan)
    ti = {t: i for i, t in enumerate(t_axis)}
    vi = {v: i for i, v in enumerate(v_axis)}
    for t, v, val in zip(ts, vs, vals):
        grid[vi[v], ti[t]] = val
    return {"kind": "grid2d", "label": f"IMPORTED 2-D - {path}", "source": path,
            "t_ps": t_axis, "v_mv": v_axis, "value": grid.tolist()}
