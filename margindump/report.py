"""Markdown quick dump: verdict table first, then evidence."""
import os


def _row(cells):
    return "| " + " | ".join(str(c) for c in cells) + " |"


def write_report(ctx, probe: dict, sweep: dict, eyes: dict, figures: dict) -> str:
    bi = probe["board"]
    dimms = probe["dimms"]
    pcie = probe["pcie"]
    bdat = probe["bdat"]
    L = []
    L.append(f"# margindump quick dump - {bi.get('sys_vendor') or '?'} {bi.get('product_name') or '?'}")
    L.append("")
    L.append(f"Run: `{ctx.run_dir}`  |  kernel {bi.get('kernel')}  |  root: {pcie.get('root')}")
    L.append("")
    L.append("## Verdicts (capability first, then result)")
    L.append("")
    L.append(_row(["Measurement", "Capability on this box", "Result", "Evidence"]))
    L.append(_row(["---", "---", "---", "---"]))
    # PCIe LMR
    vc = pcie.get("verdict_counts", {})
    marg = pcie.get("marginable_links", [])
    if marg:
        cap = f"SUPPORTED on {len(marg)} link(s)"
        res = "run `margindump lmr --bdf <bdf>` (pcilmr)"
    elif vc.get("NOT_SUPPORTED") or vc.get("SUPPORTED_LINK_TOO_SLOW"):
        cap = "NOT_SUPPORTED - all active links are <= 8 GT/s (Gen3)"
        res = "n/a (not a failure)"
    elif vc.get("LEGACY_PCI") and not vc.get("NOT_SUPPORTED"):
        cap = "NO PCIe LINKS VISIBLE (VM / container)"
        res = "n/a"
    else:
        cap, res = "see pcie_probe.json", "n/a"
    L.append(_row(["PCIe Lane Margining (LMR)", cap, res,
                   f"{pcie.get('function_count')} functions walked; {vc}"]))
    # RMT / BDAT
    L.append(_row(["Intel RMT via ACPI BDAT", bdat.get("status"),
                   "parsed" if bdat.get("status") == "TABLE_PARSED" else "n/a",
                   bdat.get("detail", "")]))
    # functional
    if sweep:
        L.append(_row(["Functional stress margin", f"available ({sweep['engine']}, {sweep['mode']})",
                       sweep["verdict"],
                       f"{len(sweep['points'])} points, {sweep['total_bit_errors']} bit errors"]))
    # measured / simulated 1d
    for key, eye in eyes.items():
        if eye.get("kind") == "margin1d":
            s = eye.get("summary", {})
            L.append(_row([eye["label"], "data present", s.get("verdict"),
                           f"{s.get('count')} lanes/ranks; weakest timing {s.get('weakest_timing')}, "
                           f"weakest voltage {s.get('weakest_voltage')}; below min: {s.get('below_minimum')}"]))
    if "model" in eyes:
        e = eyes["model"]
        L.append(_row(["DDR4 reference eye", "MODEL ONLY (not measured)", "-",
                       f"{e['interface']}: UI {e['ui_ps']:.0f} ps, modelled eye "
                       f"{e['eye_width_ps']:.0f} ps x {e['eye_height_mv']:.0f} mV @1e-12"]))
    L.append("")
    L.append("## DUT identity")
    L.append("")
    for k in ("sys_vendor", "product_name", "board_vendor", "board_name", "bios_vendor",
              "bios_version", "bios_date", "cpu_model", "nproc"):
        L.append(f"- {k}: {bi.get(k)}")
    L.append(f"- DIMMs ({dimms.get('status')}, {dimms.get('source', '')}): "
             f"{dimms.get('populated', 0)} populated of {dimms.get('slots', 0)} slots")
    for d in dimms.get("dimms", []):
        L.append(f"  - {d.get('locator')}: {d.get('size_mb')} MB {d.get('type')} "
                 f"{d.get('speed_mts')} MT/s (configured {d.get('configured_speed_mts')}), "
                 f"{d.get('manufacturer')} {d.get('part_number')} ranks={d.get('ranks')}")
    spd = probe.get("spd", {})
    L.append(f"- SPD: {spd.get('status')} - {spd.get('detail', '')}")
    for m in spd.get("modules", []):
        if m.get("status") == "ok":
            L.append(f"  - {m['i2c_device']}: {m['dram_type']} {m['module_type']} "
                     f"{m['manufacturer']} {m['part_number']} tCKmin {m['tck_min_ps']:.0f} ps "
                     f"-> {m['max_data_rate_mts']} MT/s, CL{m['cl_at_tck_min']}, ranks {m['ranks']}")
    L.append("")
    L.append("## PCIe links")
    L.append("")
    L.append(_row(["BDF", "device", "link", "LMR verdict", "AER correctable"]))
    L.append(_row(["---", "---", "---", "---", "---"]))
    for fn in pcie.get("functions", []):
        if "link" not in fn:
            continue
        lk = fn["link"]
        aer = fn.get("aer", {}).get("aer_dev_correctable", {})
        aer_s = ", ".join(f"{k}={v}" for k, v in aer.items() if v) or ("0" if aer else "n/a")
        nv = fn.get("nvme")
        name = f"{fn['name']}" + (f" [{nv['nvme']} {nv['model']} fw {nv['firmware']}]" if nv else "")
        L.append(_row([fn["bdf"], name[:70], f"{lk.get('cur_gen')} x{lk.get('cur_width')} "
                       f"(max {lk.get('max_gen')} x{lk.get('max_width')})",
                       fn["lmr_verdict"], aer_s]))
    legacy = [fn["bdf"] for fn in pcie.get("functions", []) if "link" not in fn]
    if legacy:
        L.append("")
        L.append(f"Functions without a PCIe capability (legacy PCI / virtio / host bridge): "
                 f"{len(legacy)}")
    L.append("")
    if sweep:
        L.append("## Functional stress margin sweep")
        L.append("")
        L.append(sweep["note"])
        L.append("")
        L.append(_row(["pattern", "threads", "status", "MB/s", "GB moved", "bit errors", "err/GB"]))
        L.append(_row(["---"] * 7))
        for p in sweep["points"]:
            L.append(_row([p["pattern"], p["threads"], p["status"], p.get("mbps"),
                           p.get("gb_moved"), p.get("bit_errors"), p.get("errors_per_gb")]))
        L.append("")
    L.append("## Figures")
    L.append("")
    for name, path in figures.items():
        L.append(f"- {name}: `{os.path.basename(path)}`")
        L.append(f"  ![{name}]({os.path.basename(path)})")
    L.append("")
    L.append("## Files")
    L.append("")
    for fn in sorted(os.listdir(ctx.run_dir)):
        L.append(f"- `{fn}`")
    L.append("")
    L.append("## Honest limits")
    L.append("")
    L.append("- A PCIe Gen3 link cannot be margined from software: Lane Margining at Receiver "
             "exists only at 16 GT/s and above. On this class of PC the only OS-visible link "
             "health signal is the AER correctable-error counters above.")
    L.append("- The DDR4 PHY eye lives in the memory controller's training engine. Intel exposes "
             "it only if the BIOS runs RMT and publishes BDAT; AMD has no public equivalent. "
             "Without that, 'memory margining' from the OS is a functional stress proxy.")
    L.append("- The model eye is a scaled reference (correct UI for the SPD data rate), not a "
             "measurement. It exists so a measured eye can be judged against sane units.")
    path = ctx.path("report.md")
    with open(path, "w") as f:
        f.write("\n".join(L))
    return path
