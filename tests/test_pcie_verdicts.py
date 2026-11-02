"""Synthetic PCI config spaces -> verdicts. Run: python3 -m pytest -q tests/ (or python3 tests/test_pcie_verdicts.py)"""
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from margindump import pcie  # noqa: E402


def _cfg(max_speed, max_width, cur_speed, cur_width, lmr=False, pcie_cap=True,
         supported_speeds=None, margining_ready=True):
    cfg = bytearray(4096)
    struct.pack_into("<HH", cfg, 0, 0x8086, 0x1234)       # vendor/device
    if not pcie_cap:
        return bytes(cfg[:256])
    struct.pack_into("<H", cfg, 0x06, 0x0010)             # status: capability list
    cfg[0x34] = 0x40                                      # cap ptr
    off = 0x40
    cfg[off], cfg[off + 1] = 0x10, 0x00                   # PCIe cap, last
    struct.pack_into("<H", cfg, off + 2, 0x0002 | (4 << 4))  # ver 2, root port
    struct.pack_into("<I", cfg, off + 0x0C, max_speed | (max_width << 4))
    struct.pack_into("<H", cfg, off + 0x12, cur_speed | (cur_width << 4))
    vec = 0
    for s in (supported_speeds or range(1, max_speed + 1)):
        vec |= 1 << (s - 1)
    struct.pack_into("<I", cfg, off + 0x2C, vec << 1)
    # extended caps: AER at 0x100 -> (LMR at 0x200)
    nxt = 0x200 if lmr else 0
    struct.pack_into("<I", cfg, 0x100, 0x0001 | (1 << 16) | (nxt << 20))
    if lmr:
        struct.pack_into("<I", cfg, 0x200, 0x0027 | (1 << 16) | (0 << 20))
        struct.pack_into("<H", cfg, 0x204, 0x0000)        # port caps: no driver sw
        struct.pack_into("<H", cfg, 0x206, 0x0001 if margining_ready else 0)
    return bytes(cfg)


def _fake_sysfs(devs):
    root = tempfile.mkdtemp(prefix="fakepci_")
    for bdf, cfg in devs.items():
        d = os.path.join(root, bdf)
        os.makedirs(d)
        with open(os.path.join(d, "config"), "wb") as f:
            f.write(cfg)
        for name, val in (("vendor", "0x8086"), ("device", "0x1234"), ("class", "0x060400")):
            with open(os.path.join(d, name), "w") as f:
                f.write(val + "\n")
    with open(os.path.join(root, "0000:00:01.0", "aer_dev_correctable"), "w") as f:
        f.write("RxErr 3\nBadTLP 0\nBadDLLP 0\nRollover 0\nTimeout 1\nNonFatalErr 0\n"
                "CorrIntErr 0\nHeaderOF 0\nTOTAL_ERR_COR 4\n")
    return root


def run():
    devs = {
        "0000:00:01.0": _cfg(3, 16, 3, 16),                 # CPU PEG root port, Gen3 x16 (RTX 2060S)
        "0000:00:1d.0": _cfg(3, 4, 3, 4),                   # PCH root port, Gen3 x4 (NVMe)
        "0000:00:1c.0": _cfg(3, 1, 0, 0),                   # unpopulated PCH port
        "0000:02:00.0": _cfg(4, 4, 4, 4, lmr=True),         # Gen4 x4 NVMe with LMR
        "0000:03:00.0": _cfg(4, 16, 3, 16, lmr=True),       # Gen4-capable GPU trained at Gen3
        "0000:04:00.0": _cfg(4, 8, 4, 8, lmr=False),        # Gen4 link, LMR missing (anomaly)
        "0000:00:1f.0": _cfg(0, 0, 0, 0, pcie_cap=False),   # legacy PCI (ISA bridge)
    }
    pcie.PCI_SYSFS = _fake_sysfs(devs)
    pcie.which = lambda *_: None                          # no lspci in test
    res = pcie.probe_pcie()
    got = {f["bdf"]: f["lmr_verdict"] for f in res["functions"]}
    want = {"0000:00:01.0": "NOT_SUPPORTED", "0000:00:1d.0": "NOT_SUPPORTED",
            "0000:00:1c.0": "NO_LINK", "0000:02:00.0": "SUPPORTED",
            "0000:03:00.0": "SUPPORTED_LINK_TOO_SLOW", "0000:04:00.0": "ABSENT_UNEXPECTED",
            "0000:00:1f.0": "LEGACY_PCI"}
    assert got == want, (got, want)
    f01 = next(f for f in res["functions"] if f["bdf"] == "0000:00:01.0")
    assert f01["link"]["cur_gen"] == "Gen3" and f01["link"]["cur_width"] == 16
    assert f01["aer"]["aer_dev_correctable"]["RxErr"] == 3
    f02 = next(f for f in res["functions"] if f["bdf"] == "0000:02:00.0")
    assert f02["lmr"]["margining_ready"] is True and f02["lmr"]["min_timing_ps"] == 18.75
    assert res["marginable_links"] == ["0000:02:00.0"]
    print("PCIe verdict tests: OK", got)


def test_verdicts():
    run()


if __name__ == "__main__":
    run()
