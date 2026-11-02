"""Renderers (matplotlib, Agg). Every figure title states MODEL / MEASURED /
SIMULATED / FUNCTIONAL so a screenshot can never be mistaken for a measured eye."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LogNorm, ListedColormap  # noqa: E402
from matplotlib.patches import Polygon, Rectangle  # noqa: E402

INK, MUTED, GRID = "#1f2430", "#6b7280", "#e5e7eb"
BLUE, BLUE_DARK, AMBER, RED, GREEN = "#3b6fd6", "#1d3f8f", "#d97706", "#c2410c", "#15803d"

plt.rcParams.update({"font.size": 9, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
                     "xtick.color": INK, "ytick.color": INK, "axes.titlesize": 10,
                     "axes.titleweight": "bold", "grid.color": GRID, "grid.linewidth": 0.6})


def render_model_eye(eye: dict, out_path: str):
    t, v = np.array(eye["t_ps"]), np.array(eye["v_mv"])
    lb = np.array(eye["log10_ber"])
    p = eye["params"]
    fig, ax = plt.subplots(figsize=(8, 5.6))
    floor = np.log10(p["target_ber"]) - 3
    pcm = ax.pcolormesh(t, v, np.clip(10 ** lb, 10 ** floor, 1.0), cmap="Blues_r",
                        norm=LogNorm(vmin=10 ** floor, vmax=1.0), shading="auto")
    fig.colorbar(pcm, ax=ax, label="BER (log scale, lighter = more open)")
    try:
        ax.contour(t, v, 10 ** lb, levels=[p["target_ber"]], colors=[AMBER], linewidths=2)
    except (ValueError, IndexError):
        pass
    mw, mh = eye["rx_mask_approx"]["t_ui"] * eye["ui_ps"], eye["rx_mask_approx"]["v_mv"]
    ax.add_patch(Rectangle((-mw / 2, -mh / 2), mw, mh, fill=False, ec=RED, lw=1.4, ls="--"))
    ax.axvline(0, color=MUTED, lw=0.5)
    ax.axhline(0, color=MUTED, lw=0.5)
    ax.set_xlabel(f"sampling offset from eye centre (ps)   [1 UI = {eye['ui_ps']:.0f} ps]")
    ax.set_ylabel("Vref offset (mV)")
    ax.set_title(f"{eye['label']}\n{eye['interface']} reference Rx eye - dual-Dirac Dj "
                 f"{p['dj_pp_ps']:.0f} ps pp, Rj {p['rj_sigma_ps']:.1f} ps rms; noise Dn "
                 f"{p['dn_pp_mv']:.0f} mV pp, Rn {p['vn_sigma_mv']:.0f} mV rms\n"
                 f"eye @ BER {p['target_ber']:.0e}: {eye['eye_width_ps']:.0f} ps "
                 f"({100 * eye['eye_width_ui']:.0f}% UI) x {eye['eye_height_mv']:.0f} mV   "
                 f"| amber = BER contour, red dashed = approx JEDEC Rx mask", fontsize=8.5)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def render_margin1d(m: dict, summary: dict, out_path: str):
    e = m["entries"]
    ids = [x["id"] for x in e]
    left = np.array([x["left_ps"] for x in e])
    right = np.array([x["right_ps"] for x in e])
    up = np.array([x["up_mv"] for x in e])
    down = np.array([x["down_mv"] for x in e])
    mt, mv = m.get("min_timing_ps"), m.get("min_voltage_mv")
    weak = set(summary.get("below_minimum", []))

    fig = plt.figure(figsize=(12, 5.2))
    gs = fig.add_gridspec(1, 3, width_ratios=[1.25, 1, 1])
    ax0, ax1, ax2 = fig.add_subplot(gs[0]), fig.add_subplot(gs[1]), fig.add_subplot(gs[2])

    # (1) per-lane eye diamonds (left/right ps, up/down mV) overlaid
    for x in e:
        poly = [(-x["left_ps"], 0), (0, x["up_mv"]), (x["right_ps"], 0), (0, -x["down_mv"])]
        bad = x["id"] in weak
        ax0.add_patch(Polygon(poly, closed=True, fill=False,
                              ec=RED if bad else BLUE, lw=1.8 if bad else 1.0,
                              alpha=1.0 if bad else 0.55, zorder=3 if bad else 2))
    if mt is not None and mv is not None:
        ax0.add_patch(Polygon([(-mt, 0), (0, mv), (mt, 0), (0, -mv)], closed=True, fill=True,
                              fc=GRID, ec=MUTED, lw=1, ls="--", alpha=0.8, zorder=1))
    ax0.set_xlim(-max(left.max(), right.max()) * 1.15, max(left.max(), right.max()) * 1.15)
    ax0.set_ylim(-max(up.max(), down.max()) * 1.15, max(up.max(), down.max()) * 1.15)
    ax0.axhline(0, color=MUTED, lw=0.5)
    ax0.axvline(0, color=MUTED, lw=0.5)
    ax0.set_xlabel(f"timing margin ({m['unit_t']})")
    ax0.set_ylabel(f"voltage margin ({m['unit_v']})")
    ax0.set_title(f"per-lane margin diamonds (n={len(e)})\n"
                  f"grey = spec minimum, red = below minimum", fontsize=9)
    ax0.grid(True, alpha=0.5)
    if weak:
        ax0.text(0.02, 0.98, "below min: " + ", ".join(sorted(weak)), transform=ax0.transAxes,
                 va="top", ha="left", color=RED, fontsize=8, fontweight="bold")

    # (2) timing: min(left,right) per lane
    y = np.arange(len(ids))
    tmin = np.minimum(left, right)
    ax1.barh(y, tmin, color=[RED if i in weak else BLUE for i in ids], height=0.7)
    if mt is not None:
        ax1.axvline(mt, color=AMBER, lw=1.6, ls="--")
        ax1.text(mt, len(ids) - 0.4, f" min {mt:g} {m['unit_t']}", color=AMBER, fontsize=8, va="top")
    ax1.set_yticks(y)
    ax1.set_yticklabels(ids, fontsize=7)
    ax1.invert_yaxis()
    ax1.set_xlabel(f"worst-side timing margin ({m['unit_t']})")
    ax1.set_title(f"timing  (weakest: {summary.get('weakest_timing')})", fontsize=9)
    ax1.grid(True, axis="x", alpha=0.5)

    # (3) voltage: min(up,down) per lane
    vmin_ = np.minimum(up, down)
    ax2.barh(y, vmin_, color=[RED if i in weak else BLUE_DARK for i in ids], height=0.7)
    if mv is not None:
        ax2.axvline(mv, color=AMBER, lw=1.6, ls="--")
        ax2.text(mv, len(ids) - 0.4, f" min {mv:g} {m['unit_v']}", color=AMBER, fontsize=8, va="top")
    ax2.set_yticks(y)
    ax2.set_yticklabels(ids, fontsize=7)
    ax2.invert_yaxis()
    ax2.set_xlabel(f"worst-side voltage margin ({m['unit_v']})")
    ax2.set_title(f"voltage  (weakest: {summary.get('weakest_voltage')})", fontsize=9)
    ax2.grid(True, axis="x", alpha=0.5)

    fig.suptitle(f"{m['label']}   -   verdict {summary.get('verdict')} "
                 f"({len(weak)} below minimum)", fontsize=11, fontweight="bold")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def render_functional(sweep: dict, out_path: str):
    xs, ys = sweep["x_values"], sweep["y_values"]
    grid = np.full((len(ys), len(xs)), np.nan)
    bw = np.full_like(grid, np.nan)
    for p in sweep["points"]:
        i, j = ys.index(p["threads"]), xs.index(p["pattern"])
        grid[i, j] = p.get("errors_per_gb") if p.get("errors_per_gb") is not None else np.nan
        bw[i, j] = p.get("mbps") or np.nan
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(11, 4.6), gridspec_kw={"width_ratios": [1.3, 1]})
    # status colouring: PASS cells green, error cells red ramp (status, labelled in-cell)
    pass_mask = (grid == 0)
    disp = np.where(pass_mask, np.nan, grid)
    ax.pcolormesh(np.arange(len(xs) + 1), np.arange(len(ys) + 1), np.where(pass_mask, 1, np.nan),
                  cmap=ListedColormap(["#d1fae5"]), shading="flat")
    has_err = (not np.all(np.isnan(disp))) and np.nanmax(disp) > 0
    if has_err:
        pcm = ax.pcolormesh(np.arange(len(xs) + 1), np.arange(len(ys) + 1), disp, cmap="Reds",
                            norm=LogNorm(vmin=max(1e-3, np.nanmin(disp[disp > 0])),
                                         vmax=np.nanmax(disp)), shading="flat")
        fig.colorbar(pcm, ax=ax, label="bit errors per GB moved (log)")
    for i in range(len(ys)):
        for j in range(len(xs)):
            val = grid[i, j]
            if np.isnan(val):
                txt, col = "n/a", MUTED
            elif val == 0:
                txt, col = "PASS", GREEN
            else:
                txt, col = f"FAIL\n{val:.3g}/GB", "white" if val > 1 else RED
            ax.text(j + 0.5, i + 0.5, txt, ha="center", va="center", fontsize=8.5,
                    fontweight="bold", color=col)
    ax.set_xticks(np.arange(len(xs)) + 0.5)
    ax.set_xticklabels(xs)
    ax.set_yticks(np.arange(len(ys)) + 0.5)
    ax.set_yticklabels([str(y) for y in ys])
    ax.set_xlabel(sweep["axes"]["x"])
    ax.set_ylabel(sweep["axes"]["y"])
    tag = "SIMULATED" if sweep["mode"] == "simulated" else f"MEASURED live, {sweep['engine']}"
    ax.set_title(f"FUNCTIONAL stress margin ({tag})\n"
                 f"{sweep['seconds_per_point']}s x {sweep['mb_under_test']} MB per point - "
                 f"verdict {sweep['verdict']}", fontsize=9)
    # bandwidth panel: one line per pattern class, fixed hue order
    hues = [BLUE, BLUE_DARK, AMBER, RED, GREEN]
    for j, pat in enumerate(xs):
        ax2.plot(ys, bw[:, j], marker="o", ms=5, lw=2, color=hues[j % len(hues)], label=pat)
    ax2.set_xticks(ys)
    ax2.set_xlabel("threads")
    ax2.set_ylabel("verify bandwidth (MB/s)")
    ax2.set_title("throughput per pattern class", fontsize=9)
    ax2.grid(True, alpha=0.5)
    ax2.legend(fontsize=8, frameon=False)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def render_grid2d(g: dict, out_path: str):
    t, v = np.array(g["t_ps"]), np.array(g["v_mv"])
    grid = np.array(g["value"], dtype=float)
    fig, ax = plt.subplots(figsize=(8, 5.6))
    cmap = plt.get_cmap("Blues").copy()
    cmap.set_bad("#f3f4f6")
    pcm = ax.pcolormesh(t, v, grid, cmap=cmap, shading="auto")
    fig.colorbar(pcm, ax=ax, label="value (as provided)")
    ax.set_xlabel("timing offset (ps)")
    ax.set_ylabel("voltage offset (mV)")
    ax.set_title(g["label"], fontsize=9)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
