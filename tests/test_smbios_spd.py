"""Synthetic SMBIOS type 17 + DDR4 SPD -> decoder checks."""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from margindump import smbios  # noqa: E402


def _type17():
    fmt = bytearray(0x28)
    fmt[0], fmt[1] = 17, 0x28
    struct.pack_into("<H", fmt, 2, 0x0040)       # handle
    struct.pack_into("<H", fmt, 0x0C, 8192)      # size MB
    fmt[0x0E] = 0x09                             # DIMM
    fmt[0x10], fmt[0x11] = 1, 2                  # locator, bank strings
    fmt[0x12] = 0x1A                             # DDR4
    struct.pack_into("<H", fmt, 0x13, 0x0080)    # synchronous
    struct.pack_into("<H", fmt, 0x15, 2666)      # speed
    fmt[0x17], fmt[0x18], fmt[0x19], fmt[0x1A] = 3, 4, 5, 6
    fmt[0x1B] = 0x01                             # single rank
    struct.pack_into("<H", fmt, 0x20, 2666)      # configured speed
    struct.pack_into("<HHH", fmt, 0x22, 1200, 1200, 1200)
    strings = b"DIMM_A1\x00BANK 0\x00Kingston\x00ABC123\x00none\x00KHX2666C16/8G\x00\x00"
    return bytes(fmt) + strings


def _spd_ddr4():
    spd = bytearray(512)
    spd[2] = 0x0C                                # DDR4
    spd[3] = 0x02                                # UDIMM
    spd[4] = 0x05                                # 8 Gb dies
    spd[11] = 0x01                               # 1.2 V
    spd[12] = (0 << 3) | 0x01                    # 1 rank, x8
    spd[13] = 0x03                               # 64-bit bus, no ECC
    spd[18] = 6                                  # tCKmin = 6 * 125 ps = 750 ps -> 2666 MT/s
    spd[24] = 96                                 # tAAmin = 12.0 ns -> CL16 at 750 ps
    spd[25] = 96
    spd[26] = 96
    spd[27], spd[28] = 0x01, 0x00                # tRAS = 256 * 125 ps = 32 ns
    spd[320], spd[321] = 0x01, 0x98              # Kingston (bank 2, 0x98)
    spd[329:349] = b"KHX2666C16/8G       "
    return bytes(spd)


def run():
    d = smbios._parse_type17(_type17())
    assert d["size_mb"] == 8192 and d["type"] == "DDR4" and d["speed_mts"] == 2666
    assert d["locator"] == "DIMM_A1" and d["manufacturer"] == "Kingston"
    assert d["part_number"] == "KHX2666C16/8G" and d["ranks"] == 1
    assert d["configured_voltage_mv"] == 1200
    s = smbios._decode_ddr4_spd(_spd_ddr4())
    assert s["max_data_rate_mts"] == 2667 or s["max_data_rate_mts"] == 2666, s
    assert s["cl_at_tck_min"] == 16 and s["manufacturer"] == "Kingston"
    assert s["module_type"] == "UDIMM" and s["ranks"] == 1 and s["device_width"] == 8
    assert s["tras_ps"] == 32000
    print("SMBIOS/SPD tests: OK", d["part_number"], s["max_data_rate_mts"], "MT/s CL", s["cl_at_tck_min"])


def test_smbios_spd():
    run()


if __name__ == "__main__":
    run()
