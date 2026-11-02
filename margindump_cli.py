#!/usr/bin/env python3
"""margindump - memory/PCIe margin probe + eye dump for a Linux PC.

  sudo python3 margindump.py all                 # probe + functional sweep + eyes + report
  sudo python3 margindump.py all --quick --seconds 2 --mb 256
  python3 margindump.py probe                    # capability probe only (root = more detail)
  python3 margindump.py margin --seconds 10      # functional stress margin sweep only
  python3 margindump.py eye --import-1d m.csv    # plot measured 1-D margins (RMT / LMR export)
  python3 margindump.py eye --import-2d g.csv    # plot a 2-D margin grid (t_ps,v_mv,value)
  sudo python3 margindump.py lmr --bdf 0000:01:00.0   # run pcilmr on a Gen4+ link
  python3 margindump.py all --simulated          # demo of what measured output looks like

Outputs land in margindump_runs/<timestamp>/ : report.md, *.png, *.json, ocptv.jsonl
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from margindump import __version__  # noqa: E402
from margindump.runctx import RunContext, is_root  # noqa: E402
from margindump.smbios import board_identity, dimm_population, spd_dump  # noqa: E402
from margindump.pcie import probe_pcie, run_pcilmr  # noqa: E402
from margindump.bdat import probe_bdat  # noqa: E402
from margindump.memstress import run_sweep  # noqa: E402
from margindump import eye as eyelib  # noqa: E402
from margindump import plot  # noqa: E402
from margindump.ocptv import OcptvWriter  # noqa: E402
from margindump.report import write_report  # noqa: E402


def cmd_probe(ctx, args):
    ctx.log(f"margindump {__version__} probe (root={is_root()})")
    board = board_identity()
    dimms = dimm_population()
    spd = spd_dump(ctx)
    pcie = probe_pcie()
    bdat = probe_bdat(ctx)
    probe = {"board": board, "dimms": dimms, "spd": spd, "pcie": pcie, "bdat": bdat}
    ctx.save_json("probe.json", probe)
    ctx.save_json("pcie_probe.json", pcie)
    ctx.log(f"  board : {board.get('sys_vendor')} {board.get('product_name')} / "
            f"{board.get('board_vendor')} {board.get('board_name')} BIOS {board.get('bios_version')}")
    ctx.log(f"  cpu   : {board.get('cpu_model')} ({board.get('nproc')} threads)")
    ctx.log(f"  dimms : {dimms.get('status')} - {dimms.get('populated', 0)} populated "
            f"({dimms.get('source', dimms.get('detail', ''))})")
    for d in dimms.get("dimms", []):
        ctx.log(f"          {d.get('locator')}: {d.get('size_mb')}MB {d.get('type')} "
                f"{d.get('speed_mts')}MT/s {d.get('manufacturer')} {d.get('part_number')}")
    ctx.log(f"  spd   : {spd.get('status')} - {len(spd.get('modules', []))} module(s)")
    ctx.log(f"  pcie  : {pcie['function_count']} functions, verdicts {pcie['verdict_counts']}")
    for fn in pcie["functions"]:
        if "link" in fn:
            lk = fn["link"]
            ctx.log(f"          {fn['bdf']} {lk['cur_gen']} x{lk['cur_width']:<2} "
                    f"{fn['lmr_verdict']:<18} {fn['name'][:60]}")
    ctx.log(f"  bdat  : {bdat.get('status')} - {bdat.get('detail', '')}")
    return probe


def cmd_margin(ctx, args):
    sweep = run_sweep(ctx, seconds=args.seconds, mb=args.mb, quick=args.quick,
                      simulated=args.simulated, engine=args.engine)
    ctx.save_json("margin_sweep.json", sweep)
    ctx.log(f"functional sweep verdict: {sweep['verdict']} ({sweep['total_bit_errors']} bit errors)")
    return sweep


def _data_rate(probe) -> float:
    for m in (probe or {}).get("spd", {}).get("modules", []):
        if m.get("max_data_rate_mts"):
            return float(m["max_data_rate_mts"])
    for d in (probe or {}).get("dimms", {}).get("dimms", []):
        r = d.get("configured_speed_mts") or d.get("speed_mts")
        if r:
            return float(r)
    return 2666.0


def cmd_eye(ctx, args, probe=None, sweep=None, lmr_results=None):
    eyes, figures = {}, {}
    rate = _data_rate(probe)
    model = eyelib.build_model_eye(data_rate_mts=rate)
    eyes["model"] = model
    figures["model_eye"] = ctx.path("eye_model_ddr4.png")
    plot.render_model_eye(model, figures["model_eye"])
    ctx.log(f"model eye: DDR4-{int(rate)} UI={model['ui_ps']:.0f} ps -> "
            f"{model['eye_width_ps']:.0f} ps x {model['eye_height_mv']:.0f} mV (MODEL)")

    sets = []
    if args.import_1d:
        sets.append(eyelib.margin1d_from_csv(args.import_1d))
    for r in (lmr_results or []):
        if r.get("lanes"):
            sets.append(eyelib.margin1d_from_pcilmr(r["bdf"], r["lanes"], r.get("gen", 4)))
    if args.simulated:
        sets.append(eyelib.margin1d_simulated("pcie_gen4_x16"))
        sets.append(eyelib.margin1d_simulated("rmt_ddr4"))
    for i, m in enumerate(sets):
        m["summary"] = eyelib.summarize_margin1d(m)
        key = f"margin1d_{i}"
        eyes[key] = m
        figures[key] = ctx.path(f"eye_{key}_{m['source'].replace('/', '_')[:24]}.png")
        plot.render_margin1d(m, m["summary"], figures[key])
        ctx.log(f"{m['label']}: {m['summary']['verdict']} - weakest timing "
                f"{m['summary']['weakest_timing']} ({m['summary']['width_ps_min']:.1f} ps), "
                f"weakest voltage {m['summary']['weakest_voltage']} "
                f"({m['summary']['height_mv_min']:.1f} mV)")
    if args.import_2d:
        g = eyelib.grid2d_from_csv(args.import_2d)
        eyes["grid2d"] = g
        figures["grid2d"] = ctx.path("eye_grid2d_imported.png")
        plot.render_grid2d(g, figures["grid2d"])
    if sweep:
        figures["functional"] = ctx.path("eye_functional_stress.png")
        plot.render_functional(sweep, figures["functional"])
    ctx.save_json("eyes.json", {k: {kk: vv for kk, vv in v.items()
                                    if kk not in ("log10_ber", "t_ps", "v_mv", "value")}
                                for k, v in eyes.items()})
    for name, p in figures.items():
        ctx.log(f"  wrote {os.path.basename(p)}")
    return eyes, figures


def cmd_lmr(ctx, args):
    pcie = probe_pcie()
    fn = next((f for f in pcie["functions"] if f["bdf"] == args.bdf), None)
    if fn is None:
        ctx.log(f"{args.bdf}: not found")
        return []
    if fn["lmr_verdict"] != "SUPPORTED":
        ctx.log(f"{args.bdf}: {fn['lmr_verdict']} - {fn['lmr_reason']}")
        return []
    ctx.log(f"{args.bdf}: running pcilmr (this takes minutes per link) ...")
    r = run_pcilmr(args.bdf)
    gen = {8.0: 3, 16.0: 4, 32.0: 5, 64.0: 6}.get(fn["link"]["cur_speed_gts"], 4)
    r.update(bdf=args.bdf, gen=gen)
    ctx.save_json(f"pcilmr_{args.bdf}.json", r)
    with open(ctx.path(f"pcilmr_{args.bdf}.txt"), "w") as f:
        f.write(r.get("stdout", "") + "\n" + r.get("stderr", ""))
    ctx.log(f"  pcilmr status {r['status']}, {len(r.get('lanes', []))} lanes parsed")
    return [r]


def write_ocptv(ctx, probe, sweep, eyes, cmdline):
    dut = dict(probe["board"])
    dut["dimms"] = probe["dimms"].get("dimms", [])
    dut["pcie_functions"] = [f for f in probe["pcie"]["functions"] if "link" in f]
    w = OcptvWriter(ctx.path("ocptv.jsonl"), "margindump", __version__, cmdline, dut)
    overall_fail = False

    sid = w.step_start("pcie_lane_margining_capability")
    for fn in dut["pcie_functions"]:
        hid = f"pci_{fn['bdf']}"
        w.measurement(sid, "link_speed", fn["link"]["cur_speed_gts"], "GT/s", hid)
        w.measurement(sid, "link_width", fn["link"]["cur_width"], "lanes", hid)
        w.measurement(sid, "lmr_supported", fn["lmr_verdict"] == "SUPPORTED", None, hid)
        for k, v in fn.get("aer", {}).get("aer_dev_correctable", {}).items():
            w.measurement(sid, f"aer_correctable_{k}", v, "count", hid)
        w.log(sid, f"{fn['bdf']}: {fn['lmr_verdict']} - {fn['lmr_reason']}")
    marginable = probe["pcie"].get("marginable_links", [])
    w.step_end(sid, "COMPLETE" if marginable else "SKIP")

    sid = w.step_start("intel_rmt_bdat_discovery")
    w.measurement(sid, "bdat_table_present", bool(probe["bdat"].get("bdat_table_present")))
    w.log(sid, f"{probe['bdat'].get('status')}: {probe['bdat'].get('detail', '')}")
    w.step_end(sid, "COMPLETE" if probe["bdat"].get("status") == "TABLE_PARSED" else "SKIP")

    if sweep:
        sid = w.step_start("functional_memory_stress_margin")
        for p in sweep["points"]:
            w.measurement(sid, f"bit_errors[{p['pattern']},t{p['threads']}]", p.get("bit_errors"),
                          "count", validators=[{"name": "zero_errors", "type": "EQUAL", "value": 0}])
            w.measurement(sid, f"bandwidth[{p['pattern']},t{p['threads']}]", p.get("mbps"), "MB/s")
        verdict = sweep["verdict"]
        overall_fail |= verdict == "FAIL"
        w.diagnosis(sid, f"functional-margin-{verdict.lower()}",
                    "PASS" if verdict == "PASS" else ("FAIL" if verdict == "FAIL" else "UNKNOWN"),
                    msg=sweep["note"])
        w.step_end(sid, "COMPLETE" if verdict != "ERROR" else "ERROR")

    for key, e in eyes.items():
        if e.get("kind") != "margin1d":
            continue
        sid = w.step_start(f"margin1d:{e['label']}")
        for x in e["entries"]:
            for fld, unit in (("left_ps", "ps"), ("right_ps", "ps"), ("up_mv", "mV"), ("down_mv", "mV")):
                w.measurement(sid, f"{x['id']}.{fld}", x[fld], unit)
        s = e["summary"]
        overall_fail |= (s["verdict"] == "FAIL" and e["source"] != "simulated")
        w.diagnosis(sid, f"margin-{s['verdict'].lower()}", s["verdict"],
                    msg=f"below minimum: {s['below_minimum']}")
        w.step_end(sid, "COMPLETE")
    w.run_end("COMPLETE", "FAIL" if overall_fail else "PASS")


def main():
    ap = argparse.ArgumentParser(prog="margindump", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="margindump_runs", help="output root directory")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_margin_args(p):
        p.add_argument("--seconds", type=float, default=3.0, help="seconds per sweep point")
        p.add_argument("--mb", type=int, default=512, help="MB under test per point")
        p.add_argument("--quick", action="store_true", help="4 patterns x 3 thread counts")
        p.add_argument("--engine", default="auto", choices=["auto", "stressapptest", "builtin-numpy"])
        p.add_argument("--simulated", action="store_true", help="demo data instead of a live run")

    def add_eye_args(p):
        p.add_argument("--import-1d", metavar="CSV",
                       help="id,left_ps,right_ps,up_mv,down_mv[,min_t_ps,min_v_mv]")
        p.add_argument("--import-2d", metavar="CSV", help="t_ps,v_mv,value")

    sub.add_parser("probe")
    add_margin_args(sub.add_parser("margin"))
    p = sub.add_parser("eye")
    add_eye_args(p)
    p.add_argument("--simulated", action="store_true")
    p = sub.add_parser("lmr")
    p.add_argument("--bdf", required=True)
    p = sub.add_parser("all")
    add_margin_args(p)
    add_eye_args(p)
    p.add_argument("--no-margin", action="store_true", help="skip the stress sweep")
    args = ap.parse_args()

    ctx = RunContext(args.out)
    ctx.log(f"run dir: {ctx.run_dir}")
    if args.cmd == "probe":
        cmd_probe(ctx, args)
    elif args.cmd == "margin":
        cmd_margin(ctx, args)
    elif args.cmd == "eye":
        probe = None
        cands = sorted(p for p in os.listdir(args.out) if os.path.isfile(
            os.path.join(args.out, p, "probe.json"))) if os.path.isdir(args.out) else []
        if cands:
            with open(os.path.join(args.out, cands[-1], "probe.json")) as f:
                probe = json.load(f)
        cmd_eye(ctx, args, probe=probe)
    elif args.cmd == "lmr":
        cmd_lmr(ctx, args)
    elif args.cmd == "all":
        probe = cmd_probe(ctx, args)
        sweep = None if args.no_margin else cmd_margin(ctx, args)
        eyes, figures = cmd_eye(ctx, args, probe=probe, sweep=sweep)
        write_ocptv(ctx, probe, sweep, eyes, " ".join(sys.argv))
        rp = write_report(ctx, probe, sweep, eyes, figures)
        ctx.log(f"report: {rp}")
        ctx.log(f"ocptv : {ctx.path('ocptv.jsonl')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
