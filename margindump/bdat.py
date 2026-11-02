"""Intel BDAT (BIOS Data ACPI Table) discovery - the only public route to Intel
Rank Margin Tool (RMT) results on a consumer board.

Chain: /sys/firmware/acpi/tables/BDAT (ACPI header + Generic Address Structure)
       -> physical address of the BDAT structure in reserved memory
       -> /dev/mem read -> BDAT header ("BDATHEAD") -> schema list -> per-schema GUIDs

Whether a given OEM BIOS enables RMT at all (FSP UPDs RMT / RMTBIT / RmtPerTask /
BdatTestType) is unknown until you look; most consumer BIOSes don't. The honest
outcomes are therefore:
  NO_TABLE        no BDAT ACPI table -> RMT results are not published by this BIOS
  TABLE_NO_ACCESS table exists but /dev/mem is unreadable (STRICT_DEVMEM / lockdown)
  TABLE_PARSED    schema list enumerated; raw blob saved as bdat.bin for offline decode

Schema GUID -> name mapping is deliberately left for the user to fill from the
UEFI BDAT Interface Specification (v4.0.x) and the platform FSP headers; we do not
reproduce GUIDs from memory. The RMT schema is parsed only when its GUID is set.
"""
import os
import struct
import uuid

from .runctx import read_text

BDAT_TABLE = "/sys/firmware/acpi/tables/BDAT"

# Fill from UEFI BDAT Interface Spec v4.0.x / FSP BdatSchema headers, e.g.
#   KNOWN_SCHEMAS = {"<guid>": "RMT (schema 6)", "<guid>": "SPD (schema 1)"}
KNOWN_SCHEMAS = {}


def _read_physical(addr: int, size: int) -> bytes:
    with open("/dev/mem", "rb", buffering=0) as f:
        f.seek(addr)
        return f.read(size)


def probe_bdat(ctx=None) -> dict:
    tables = []
    try:
        tables = sorted(os.listdir("/sys/firmware/acpi/tables"))
    except OSError:
        pass
    out = {"acpi_tables_visible": len(tables), "bdat_table_present": "BDAT" in tables}
    if "BDAT" not in tables:
        out["status"] = "NO_TABLE"
        out["detail"] = ("no BDAT ACPI table: this BIOS does not publish Intel RMT "
                         "results (or ACPI tables are not readable - need root)")
        if not tables:
            out["detail"] = "ACPI tables not readable (run as root)"
            out["status"] = "UNKNOWN"
        return out
    try:
        with open(BDAT_TABLE, "rb") as f:
            tbl = f.read()
    except OSError as e:
        out.update(status="TABLE_NO_ACCESS", detail=f"cannot read BDAT table: {e}")
        return out
    if len(tbl) < 36 + 12:
        out.update(status="TABLE_MALFORMED", detail=f"BDAT table only {len(tbl)} bytes")
        return out
    sig, length, rev = tbl[0:4].decode("ascii", "replace"), struct.unpack_from("<I", tbl, 4)[0], tbl[8]
    oem_id = tbl[10:16].decode("ascii", "replace").strip()
    oem_tbl = tbl[16:24].decode("ascii", "replace").strip()
    space_id, bit_width, bit_off, access, addr = struct.unpack_from("<BBBBQ", tbl, 36)
    out.update(acpi={"signature": sig, "length": length, "revision": rev, "oem_id": oem_id,
                     "oem_table_id": oem_tbl},
               gas={"address_space_id": space_id, "bit_width": bit_width,
                    "address": f"0x{addr:x}"})
    if space_id != 0 or addr == 0:
        out.update(status="TABLE_PARSED_NO_POINTER",
                   detail="BDAT GAS does not point at system memory")
        return out
    try:
        head = _read_physical(addr, 32)
    except OSError as e:
        out.update(status="TABLE_NO_ACCESS",
                   detail=f"/dev/mem read at 0x{addr:x} failed ({e}); CONFIG_STRICT_DEVMEM "
                          f"or kernel lockdown - boot with iomem=relaxed to test")
        return out
    if head[:8] != b"BDATHEAD":
        out.update(status="TABLE_BAD_SIGNATURE",
                   detail=f"expected BDATHEAD at 0x{addr:x}, got {head[:8]!r}")
        return out
    struct_size, crc16, _res, pver, sver, oem_off = struct.unpack_from("<IHHHHI", head, 8)
    blob = _read_physical(addr, max(struct_size, 32))
    out["bdat_header"] = {"size": struct_size, "primary_version": pver,
                          "secondary_version": sver, "oem_offset": oem_off}
    # schema list follows the 32-byte header
    n, _r, year, month, day, hour, minute, second, _r2 = struct.unpack_from("<HHHBBBBBB", blob, 32)
    offsets = struct.unpack_from(f"<{n}I", blob, 44)
    schemas = []
    for off in offsets:
        if off + 22 > len(blob):
            schemas.append({"offset": off, "error": "beyond structure"})
            continue
        guid = uuid.UUID(bytes_le=blob[off:off + 16])
        data_size, crc = struct.unpack_from("<IH", blob, off + 16)
        schemas.append({"offset": off, "guid": str(guid), "data_size": data_size,
                        "crc16": crc, "name": KNOWN_SCHEMAS.get(str(guid), "unmapped")})
    out.update(status="TABLE_PARSED",
               bios_timestamp=f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}",
               schema_count=n, schemas=schemas)
    if ctx is not None:
        with open(ctx.path("bdat.bin"), "wb") as f:
            f.write(blob)
        out["raw_file"] = "bdat.bin"
    out["detail"] = (f"{n} schema(s) published; map GUIDs via KNOWN_SCHEMAS to decode RMT "
                     f"(schema 6) margins into the 1D eye")
    return out
