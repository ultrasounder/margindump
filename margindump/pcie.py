"""PCIe capability probe - pure Python over sysfs config space (no lspci needed).

For every PCI function we read /sys/bus/pci/devices/<bdf>/config and walk:
  * the legacy capability list  -> PCI Express capability (ID 0x10): link speed/width
  * the extended capability list -> Lane Margining at Receiver (ext ID 0x0027),
    Secondary PCIe (0x0019), 16/32/64 GT/s physical-layer caps, AER (0x0001)

Verdict semantics per link (this is the point of the tool):
  SUPPORTED          LMR extended capability present - pcilmr / pcie_lmt can margin it
  NOT_SUPPORTED      link tops out at <= 8 GT/s (Gen3 or lower). LMR is only defined
                     for 16 GT/s and above, so there is nothing to measure. This is a
                     capability fact, NOT a test failure.
  ABSENT_UNEXPECTED  link supports >= 16 GT/s but no LMR capability is advertised
                     (spec requires it) - worth a look.
  NO_LINK            function has a PCIe cap but no active link (unpopulated port)
  LEGACY_PCI         no PCIe capability at all (virtio, host bridge, legacy PCI)

Reads are read-only; we never write Lane Control registers here. Root is needed
for the full 4 KiB config space (non-root sees the first 64 bytes only).
"""
import glob
import os
import re

from .runctx import read_text, which, run_cmd

PCI_SYSFS = "/sys/bus/pci/devices"   # overridable for tests / offline config dumps


def _dev(bdf: str, name: str) -> str:
    return os.path.join(PCI_SYSFS, bdf, name)

SPEED_GTS = {1: 2.5, 2: 5.0, 3: 8.0, 4: 16.0, 5: 32.0, 6: 64.0}
GEN_NAME = {1: "Gen1", 2: "Gen2", 3: "Gen3", 4: "Gen4", 5: "Gen5", 6: "Gen6"}
PORT_TYPE = {0: "endpoint", 1: "legacy_endpoint", 4: "root_port", 5: "upstream_port",
             6: "downstream_port", 7: "pcie_to_pci_bridge", 8: "pci_to_pcie_bridge",
             9: "rc_integrated_endpoint", 10: "rc_event_collector"}
EXT_CAP_NAMES = {0x0001: "AER", 0x0019: "SecondaryPCIe", 0x0026: "PhysLayer16GT",
                 0x0027: "LaneMarginingAtReceiver", 0x002A: "PhysLayer32GT",
                 0x0030: "PhysLayer64GT", 0x0010: "SR-IOV", 0x001E: "L1PMSubstates",
                 0x0003: "DeviceSerialNumber", 0x000E: "ARI", 0x0018: "LTR"}
CAP_PCIE = 0x10
EXT_LMR = 0x0027

# Minimum margins pcilmr / pcie_lmt report against (PCIe Base Spec receiver
# margining minimums: 0.3 UI timing, 15 mV voltage when voltage margining is supported)
LMR_MIN_TIMING_PS = {4: 18.75, 5: 9.375, 6: 4.6875}
LMR_MIN_VOLTAGE_MV = 15.0


def _u16(b, o):
    return int.from_bytes(b[o:o + 2], "little") if o + 2 <= len(b) else 0


def _u32(b, o):
    return int.from_bytes(b[o:o + 4], "little") if o + 4 <= len(b) else 0


def _read_config(bdf: str) -> bytes:
    try:
        with open(_dev(bdf, "config"), "rb") as f:
            return f.read()
    except OSError:
        return b""


def _walk_caps(cfg: bytes):
    """Yield (cap_id, offset) for the legacy capability list."""
    status = _u16(cfg, 0x06)
    if not (status & 0x10) or len(cfg) < 0x40:
        return
    ptr = cfg[0x34] & 0xFC
    seen = set()
    while ptr and ptr not in seen and ptr + 2 <= len(cfg):
        seen.add(ptr)
        cap_id, nxt = cfg[ptr], cfg[ptr + 1] & 0xFC
        yield cap_id, ptr
        ptr = nxt


def _walk_ext_caps(cfg: bytes):
    """Yield (ext_id, version, offset) for the extended capability list."""
    if len(cfg) < 0x104:
        return
    ptr = 0x100
    seen = set()
    while ptr and ptr >= 0x100 and ptr not in seen and ptr + 4 <= len(cfg):
        seen.add(ptr)
        hdr = _u32(cfg, ptr)
        ext_id, ver, nxt = hdr & 0xFFFF, (hdr >> 16) & 0xF, (hdr >> 20) & 0xFFC
        if ext_id == 0 or ext_id == 0xFFFF:
            return
        yield ext_id, ver, ptr
        ptr = nxt


def _aer_counters(bdf: str) -> dict:
    """Kernel AER per-device counters (sysfs). On a Gen3 link these are the only
    OS-visible link-health signal - the poor man's margin indicator."""
    out = {}
    for name in ("aer_dev_correctable", "aer_dev_nonfatal", "aer_dev_fatal"):
        txt = read_text(_dev(bdf, name))
        if txt is None:
            continue
        counters = {}
        for line in txt.splitlines():
            m = re.match(r"^(\S+)\s+(\d+)$", line.strip())
            if m:
                counters[m.group(1)] = int(m.group(2))
        out[name] = counters
    return out


def _sysfs_name(bdf: str) -> str:
    """Best-effort human name: lspci if present, else class/vendor/device."""
    if which("lspci"):
        rc, o, _ = run_cmd(["lspci", "-s", bdf], timeout=10)
        if rc == 0 and o.strip():
            return o.strip().split(" ", 1)[-1][:90]
    v = read_text(_dev(bdf, "vendor"), "?")
    d = read_text(_dev(bdf, "device"), "?")
    c = read_text(_dev(bdf, "class"), "?")
    return f"vendor {v} device {d} class {c}"


def _nvme_map() -> dict:
    """bdf -> {model, serial, fw} for NVMe controllers (sysfs, no nvme-cli needed)."""
    m = {}
    for ctrl in glob.glob("/sys/class/nvme/nvme*"):
        try:
            dev = os.path.realpath(os.path.join(ctrl, "device"))
        except OSError:
            continue
        bdf = os.path.basename(dev)
        m[bdf] = {"model": read_text(os.path.join(ctrl, "model"), ""),
                  "serial": read_text(os.path.join(ctrl, "serial"), ""),
                  "firmware": read_text(os.path.join(ctrl, "firmware_rev"), ""),
                  "nvme": os.path.basename(ctrl)}
    return m


def probe_pcie() -> dict:
    """Walk every PCI function; return per-function capability facts + verdicts."""
    devs = sorted(os.path.basename(p) for p in glob.glob(os.path.join(PCI_SYSFS, "*")))
    nvme = _nvme_map()
    functions, links = [], []
    for bdf in devs:
        cfg = _read_config(bdf)
        entry = {"bdf": bdf, "name": _sysfs_name(bdf), "config_bytes": len(cfg),
                 "vendor": read_text(_dev(bdf, "vendor"), ""),
                 "device": read_text(_dev(bdf, "device"), ""),
                 "class": read_text(_dev(bdf, "class"), ""),
                 "driver": os.path.basename(os.path.realpath(_dev(bdf, "driver")))
                 if os.path.exists(_dev(bdf, "driver")) else None}
        if bdf in nvme:
            entry["nvme"] = nvme[bdf]
        pcie_off = None
        for cap_id, off in _walk_caps(cfg):
            if cap_id == CAP_PCIE:
                pcie_off = off
        ext = {}
        for ext_id, ver, off in _walk_ext_caps(cfg):
            ext[EXT_CAP_NAMES.get(ext_id, f"ext_0x{ext_id:04x}")] = {"id": ext_id,
                                                                      "version": ver,
                                                                      "offset": off}
        entry["ext_caps"] = ext
        if pcie_off is None:
            entry["lmr_verdict"] = "LEGACY_PCI"
            entry["lmr_reason"] = "no PCI Express capability (not a PCIe link)"
            functions.append(entry)
            continue

        pcie_caps = _u16(cfg, pcie_off + 2)
        cap_ver = pcie_caps & 0xF
        port_type = (pcie_caps >> 4) & 0xF
        link_cap = _u32(cfg, pcie_off + 0x0C)
        link_sta = _u16(cfg, pcie_off + 0x12)
        max_speed, max_width = link_cap & 0xF, (link_cap >> 4) & 0x3F
        cur_speed, cur_width = link_sta & 0xF, (link_sta >> 4) & 0x3F
        link_cap2 = _u32(cfg, pcie_off + 0x2C) if cap_ver >= 2 else 0
        speeds_vec = (link_cap2 >> 1) & 0x7F
        supported = [SPEED_GTS[i + 1] for i in range(7) if speeds_vec & (1 << i)
                     and (i + 1) in SPEED_GTS]
        link = {"port_type": PORT_TYPE.get(port_type, f"type_{port_type}"),
                "cap_version": cap_ver,
                "max_speed_gts": SPEED_GTS.get(max_speed), "max_width": max_width,
                "cur_speed_gts": SPEED_GTS.get(cur_speed), "cur_width": cur_width,
                "max_gen": GEN_NAME.get(max_speed), "cur_gen": GEN_NAME.get(cur_speed),
                "supported_speeds_gts": supported}
        entry["link"] = link
        entry["aer"] = _aer_counters(bdf)

        lmr = ext.get("LaneMarginingAtReceiver")
        if lmr:
            off = lmr["offset"]
            port_caps = _u16(cfg, off + 4)
            port_status = _u16(cfg, off + 6)
            entry["lmr"] = {"uses_driver_software": bool(port_caps & 1),
                            "margining_ready": bool(port_status & 1),
                            "margining_software_ready": bool(port_status & 2),
                            "min_timing_ps": LMR_MIN_TIMING_PS.get(cur_speed),
                            "min_voltage_mv": LMR_MIN_VOLTAGE_MV}
        if cur_width == 0 or cur_speed == 0:
            entry["lmr_verdict"] = "NO_LINK"
            entry["lmr_reason"] = "PCIe port with no active link"
        elif lmr:
            if cur_speed >= 4:
                entry["lmr_verdict"] = "SUPPORTED"
                entry["lmr_reason"] = (f"LMR capability at 0x{lmr['offset']:03x}; link "
                                       f"{link['cur_gen']} x{cur_width} - run pcilmr/pcie_lmt")
            else:
                entry["lmr_verdict"] = "SUPPORTED_LINK_TOO_SLOW"
                entry["lmr_reason"] = (f"LMR capability present but link negotiated "
                                       f"{link['cur_gen']}; margining requires >= 16 GT/s")
        elif max_speed >= 4:
            entry["lmr_verdict"] = "ABSENT_UNEXPECTED"
            entry["lmr_reason"] = (f"port advertises {link['max_gen']} but no LMR "
                                   f"extended capability (spec mandates it at 16 GT/s)")
        else:
            entry["lmr_verdict"] = "NOT_SUPPORTED"
            entry["lmr_reason"] = (f"link is {link['cur_gen']} ({link['cur_speed_gts']} GT/s) "
                                   f"x{cur_width}; Lane Margining at Receiver is only "
                                   f"defined for 16 GT/s and above")
        functions.append(entry)
        links.append(entry)

    verdicts = {}
    for e in functions:
        verdicts[e["lmr_verdict"]] = verdicts.get(e["lmr_verdict"], 0) + 1
    marginable = [e["bdf"] for e in links if e["lmr_verdict"] == "SUPPORTED"]
    return {"source": f"{PCI_SYSFS}/*/config (pure-Python capability walk)",
            "root": os.geteuid() == 0 if hasattr(os, "geteuid") else None,
            "function_count": len(functions), "verdict_counts": verdicts,
            "marginable_links": marginable,
            "tools": {"pcilmr": which("pcilmr"), "pcie_lmt": which("pcie_lmt"),
                      "pci_lmt": which("pci_lmt"), "lspci": which("lspci"),
                      "nvme": which("nvme")},
            "functions": functions}


def run_pcilmr(bdf: str, timeout: int = 900) -> dict:
    """Run pciutils' pcilmr on one link (Gen4+ only). Captures raw output for the
    eye adapter; parsing of the per-lane table is best-effort (format varies by
    pciutils version), so the raw text is always kept."""
    if not which("pcilmr"):
        return {"status": "TOOL_MISSING", "detail": "pcilmr (pciutils >= 3.11) not installed"}
    rc, out, err = run_cmd(["pcilmr", bdf], timeout=timeout)
    lanes = []
    # Typical line: "Lane 0: ... Left ... ps ... Right ... ps ... Up ... mV ... Down ... mV"
    for line in out.splitlines():
        m = re.search(r"Lane\s+(\d+)", line)
        nums = re.findall(r"(-?\d+(?:\.\d+)?)\s*(ps|mV)", line)
        if m and len(nums) >= 2:
            ps = [float(n) for n, u in nums if u == "ps"]
            mv = [float(n) for n, u in nums if u == "mV"]
            lanes.append({"lane": int(m.group(1)),
                          "left_ps": ps[0] if ps else None,
                          "right_ps": ps[1] if len(ps) > 1 else None,
                          "up_mv": mv[0] if mv else None,
                          "down_mv": mv[1] if len(mv) > 1 else None})
    return {"status": "OK" if rc == 0 else "ERROR", "rc": rc, "stdout": out,
            "stderr": err, "lanes": lanes}
