"""DUT identity: board/BIOS (sysfs DMI ids), DIMM population (SMBIOS type 17 parsed
from /sys/firmware/dmi/entries - no dmidecode needed), raw SPD (ee1004/at24/spd5118
EEPROM via sysfs, with a minimal DDR4 decoder; decode-dimms used when present).

Everything degrades to a per-source status; nothing here needs an NDA tool.
"""
import glob
import os
import re

from .runctx import read_text, run_cmd, which

MEM_TYPE = {0x18: "DDR3", 0x1A: "DDR4", 0x1E: "LPDDR4", 0x22: "DDR5", 0x23: "LPDDR5",
            0x02: "Unknown", 0x01: "Other"}
FORM_FACTOR = {0x09: "DIMM", 0x0D: "SODIMM", 0x0C: "RIMM", 0x0F: "FB-DIMM",
               0x10: "Die"}
# (bank, code) -> vendor; bank = (byte320 & 0x7F) + 1. Only well-known IDs; others print hex.
JEP106 = {(1, 0x2C): "Micron", (1, 0xAD): "SK hynix", (1, 0xCE): "Samsung",
          (2, 0x98): "Kingston", (3, 0x9E): "Corsair", (5, 0xCD): "G.Skill",
          (5, 0xCB): "A-DATA", (6, 0x9B): "Crucial"}


def board_identity() -> dict:
    """World-readable DMI ids - answers 'what board/BIOS is this' without msinfo32."""
    base = "/sys/class/dmi/id"
    keys = ("sys_vendor", "product_name", "product_version", "board_vendor",
            "board_name", "board_version", "bios_vendor", "bios_version", "bios_date",
            "chassis_type")
    out = {k: read_text(os.path.join(base, k)) for k in keys}
    out["status"] = "ok" if any(v for v in out.values()) else "unavailable"
    cpu = None
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    out["cpu_model"] = cpu
    out["nproc"] = os.cpu_count()
    try:
        u = os.uname()
        out["kernel"] = f"{u.sysname} {u.release}"
    except OSError:
        pass
    return out


def _parse_type17(raw: bytes) -> dict:
    length = raw[1]
    fmt = raw[:length]
    strings = [s.decode("latin-1", "replace") for s in raw[length:].split(b"\x00")]

    def s(idx):
        return strings[idx - 1].strip() if 1 <= idx <= len(strings) else ""

    def u8(o):
        return fmt[o] if o < length else 0

    def u16(o):
        return int.from_bytes(fmt[o:o + 2], "little") if o + 2 <= length else 0

    def u32(o):
        return int.from_bytes(fmt[o:o + 4], "little") if o + 4 <= length else 0

    size = u16(0x0C)
    if size == 0x7FFF:
        size_mb = u32(0x1C) & 0x7FFFFFFF
    elif size & 0x8000:
        size_mb = (size & 0x7FFF) / 1024.0
    else:
        size_mb = size
    d = {"handle": int.from_bytes(raw[2:4], "little"),
         "size_mb": size_mb,
         "form_factor": FORM_FACTOR.get(u8(0x0E), f"0x{u8(0x0E):02x}"),
         "locator": s(u8(0x10)), "bank_locator": s(u8(0x11)),
         "type": MEM_TYPE.get(u8(0x12), f"0x{u8(0x12):02x}"),
         "type_detail": u16(0x13), "speed_mts": u16(0x15),
         "manufacturer": s(u8(0x17)), "serial": s(u8(0x18)), "part_number": s(u8(0x1A)),
         "ranks": (u8(0x1B) & 0x0F) if length > 0x1B else None,
         "configured_speed_mts": u16(0x20) if length > 0x21 else None,
         "min_voltage_mv": u16(0x22) if length > 0x23 else None,
         "max_voltage_mv": u16(0x24) if length > 0x25 else None,
         "configured_voltage_mv": u16(0x26) if length > 0x27 else None}
    return d


def dimm_population() -> dict:
    """SMBIOS type 17 from sysfs (root), else dmidecode -t 17 (root), else unavailable."""
    entries = sorted(glob.glob("/sys/firmware/dmi/entries/17-*/raw"))
    dimms = []
    if entries:
        for p in entries:
            try:
                with open(p, "rb") as f:
                    raw = f.read()
            except OSError:
                continue
            if len(raw) < 0x15:
                continue
            d = _parse_type17(raw)
            if d["size_mb"]:
                dimms.append(d)
        return {"status": "ok", "source": "/sys/firmware/dmi/entries/17-*/raw",
                "populated": len(dimms), "slots": len(entries), "dimms": dimms}
    if which("dmidecode"):
        rc, out, err = run_cmd(["dmidecode", "-t", "17"], timeout=20)
        if rc == 0:
            cur, slots = None, 0
            for line in out.splitlines():
                if line.startswith("Memory Device"):
                    slots += 1
                    if cur and cur.get("size_mb"):
                        dimms.append(cur)
                    cur = {}
                elif cur is not None and ":" in line:
                    k, v = [x.strip() for x in line.split(":", 1)]
                    if k == "Size":
                        m = re.match(r"(\d+)\s*(MB|GB)", v)
                        cur["size_mb"] = int(m.group(1)) * (1024 if m.group(2) == "GB" else 1) \
                            if m else 0
                    elif k == "Locator":
                        cur["locator"] = v
                    elif k == "Type":
                        cur["type"] = v
                    elif k == "Speed":
                        cur["speed_mts"] = int(re.sub(r"\D", "", v) or 0)
                    elif k == "Configured Memory Speed":
                        cur["configured_speed_mts"] = int(re.sub(r"\D", "", v) or 0)
                    elif k in ("Manufacturer", "Part Number", "Serial Number", "Form Factor"):
                        cur[k.lower().replace(" ", "_")] = v
                    elif k == "Rank":
                        cur["ranks"] = int(v) if v.isdigit() else None
            if cur and cur.get("size_mb"):
                dimms.append(cur)
            return {"status": "ok", "source": "dmidecode -t 17", "populated": len(dimms),
                    "slots": slots, "dimms": dimms}
        return {"status": "unavailable", "detail": err.strip()[:160], "dimms": []}
    return {"status": "unavailable",
            "detail": ("no SMBIOS type-17 entries in sysfs and dmidecode not installed" if os.geteuid() == 0 else "need root for /sys/firmware/dmi/entries or dmidecode"), "dimms": []}


def _decode_ddr4_spd(spd: bytes) -> dict:
    """Minimal JEDEC DDR4 SPD (Annex L) decode: enough for identity + timing model."""
    if len(spd) < 384:
        return {"status": "short", "bytes": len(spd)}
    mtb, ftb = 125.0, 1.0  # ps

    def sb(v):  # signed byte
        return v - 256 if v > 127 else v

    tck_ps = spd[18] * mtb + sb(spd[125]) * ftb
    taa_ps = spd[24] * mtb + sb(spd[123]) * ftb
    trcd_ps = spd[25] * mtb + sb(spd[122]) * ftb
    trp_ps = spd[26] * mtb + sb(spd[121]) * ftb
    tras_ps = (((spd[27] & 0x0F) << 8) | spd[28]) * mtb
    density_code = spd[4] & 0x0F
    density_mb = {0: 256, 1: 512, 2: 1024, 3: 2048, 4: 4096, 5: 8192, 6: 16384,
                  7: 32768}.get(density_code)
    ranks = ((spd[12] >> 3) & 0x7) + 1
    dev_width = {0: 4, 1: 8, 2: 16, 3: 32}.get(spd[12] & 0x7)
    bus_width = {0: 8, 1: 16, 2: 32, 3: 64}.get(spd[13] & 0x7)
    ecc = bool(spd[13] & 0x08)
    mfr_bank = (spd[320] & 0x7F) + 1
    mfr_code = spd[321]
    part = spd[329:349].decode("ascii", "replace").strip()
    serial = spd[325:329].hex()
    data_rate = round(2.0 * 1e6 / tck_ps) if tck_ps else None
    if data_rate:  # snap to the JEDEC bin name (2667 -> 2666 etc.)
        for jedec in (1600, 1866, 2133, 2400, 2666, 2933, 3200):
            if abs(data_rate - jedec) <= jedec * 0.01:
                data_rate = jedec
                break
    module_type = {1: "RDIMM", 2: "UDIMM", 3: "SO-DIMM", 4: "LRDIMM", 8: "72b-SO-RDIMM",
                   9: "72b-SO-UDIMM", 12: "16b-SO-DIMM", 13: "32b-SO-DIMM"}.get(spd[3] & 0xF)
    cl = round(taa_ps / tck_ps) if tck_ps else None
    return {"status": "ok", "dram_type": "DDR4", "module_type": module_type,
            "manufacturer": JEP106.get((mfr_bank, mfr_code),
                                       f"JEP106 bank {mfr_bank} code 0x{mfr_code:02x}"),
            "part_number": part, "serial": serial,
            "density_per_die_mb": density_mb, "ranks": ranks, "device_width": dev_width,
            "bus_width": bus_width, "ecc": ecc,
            "tck_min_ps": tck_ps, "max_data_rate_mts": data_rate,
            "taa_ps": taa_ps, "trcd_ps": trcd_ps, "trp_ps": trp_ps, "tras_ps": tras_ps,
            "cl_at_tck_min": cl, "vdd_1v2": bool(spd[11] & 0x01)}


def spd_dump(ctx=None) -> dict:
    """Read raw SPD EEPROMs via sysfs. Tries to load i2c-i801 + ee1004 (DDR4) /
    spd5118 (DDR5) first - best effort, root only."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        for mod in ("i2c_i801", "i2c_dev", "ee1004", "spd5118"):
            run_cmd(["modprobe", mod], timeout=10)
    paths = sorted(glob.glob("/sys/bus/i2c/drivers/ee1004/*/eeprom")
                   + glob.glob("/sys/bus/i2c/drivers/spd5118/*/eeprom")
                   + glob.glob("/sys/bus/i2c/drivers/at24/*/eeprom"))
    modules = []
    for p in paths:
        try:
            with open(p, "rb") as f:
                raw = f.read()
        except OSError:
            continue
        dev = p.split("/")[-2]
        entry = {"i2c_device": dev, "bytes": len(raw)}
        if len(raw) >= 3 and raw[2] == 0x0C:
            entry.update(_decode_ddr4_spd(raw))
        elif len(raw) >= 3 and raw[2] == 0x12:
            entry.update({"status": "raw_only", "dram_type": "DDR5",
                          "detail": "DDR5 SPD decode not implemented; raw saved"})
        else:
            entry.update({"status": "raw_only", "dram_type": f"byte2=0x{raw[2]:02x}"
                          if len(raw) > 2 else "?"})
        if ctx is not None:
            with open(ctx.path(f"spd_{dev}.bin"), "wb") as f:
                f.write(raw)
            entry["raw_file"] = f"spd_{dev}.bin"
        modules.append(entry)
    out = {"status": "ok" if modules else "unavailable", "modules": modules,
           "source": "sysfs i2c eeprom (ee1004/spd5118/at24)"}
    if not modules:
        out["detail"] = ("no SPD EEPROM visible: needs root, i2c-i801 + ee1004 "
                         "(DDR4) or spd5118 (DDR5); some boards hide SMBus SPD")
    if which("decode-dimms"):
        rc, o, _ = run_cmd(["decode-dimms"], timeout=30)
        if rc == 0 and "Decoding EEPROM" in o:
            out["decode_dimms_raw"] = o[:6000]
    return out
