# margindump

Memory / PCIe margin probe + eye dump for a Linux PC. Built for an iBUYPOWER
CO900iV2 (i7-9700F, DDR4-2666, RTX 2060 SUPER, one NVMe, PCIe 3.0) on Ubuntu,
but it probes rather than assumes, so it runs on anything.

The tool answers three questions **per link, in this order, and never conflates them**:

1. **Capability** - can a *physical* margin be measured from the OS on this box?
   - PCIe Lane Margining at Receiver (LMR): walks every function's config space
     (pure Python over sysfs, no lspci needed) and reports `SUPPORTED` /
     `NOT_SUPPORTED` / `NO_LINK` / `ABSENT_UNEXPECTED` / `LEGACY_PCI`.
   - Intel Rank Margin Tool (RMT): looks for the ACPI `BDAT` table, follows its
     pointer into reserved memory, enumerates the schema list.
2. **Result** - where capability exists, run or import the measurement
   (`pcilmr` on Gen4+ links; RMT/LMR exports via CSV). Where it doesn't, run the
   **functional stress margin** (stressapptest, or the built-in numpy engine).
3. **Eye** - plot it. Every figure is titled `MEASURED`, `MODEL`, `SIMULATED` or
   `FUNCTIONAL`. A screenshot can never be mistaken for something it is not.

Outputs: `report.md` (verdict table first), `ocptv.jsonl` (OCP-Diag / OCPTV-style
JSONL), `probe.json`, `pcie_probe.json`, `margin_sweep.json`, `eyes.json`, PNG eyes.

## What it will say on the CO900iV2 (i7-9700F / Z390-class / PCIe 3.0)

| Measurement | Expected verdict | Why |
|---|---|---|
| PCIe LMR on the GPU x16 and NVMe x4 links | `NOT_SUPPORTED` | links are Gen3 (8 GT/s); LMR is only defined at 16 GT/s+. Not a failure. |
| AER correctable counters per link | reported | the only OS-visible PCIe link-health signal on Gen3 |
| Intel RMT via BDAT | most likely `NO_TABLE` | consumer BIOSes rarely build with `RMT`/`BdatTestType` UPDs enabled; if the table *is* there, the schema GUIDs and raw blob are dumped |
| DIMM identity | SMBIOS type 17 (size, speed, vendor, part #, ranks) + raw SPD if `ee1004` binds | identity is what makes a margin number worth keeping |
| Functional stress margin | `PASS` with errors/GB + bandwidth per pattern class x thread count | the honest in-OS proxy |
| DDR4 eye | `MODEL` for DDR4-2666 (UI = 375 ps, **not** the 750 ps clock period) | reference in the right units for when a measured eye arrives |

The tests in `tests/test_pcie_verdicts.py` feed synthetic config spaces for exactly
this topology (Gen3 x16 root port, Gen3 x4 NVMe, an empty port, a Gen4 NVMe with LMR,
a Gen4-capable GPU trained at Gen3, a Gen4 link with LMR missing) and assert the verdicts.

## Install (Ubuntu)

```bash
sudo apt install python3-numpy python3-matplotlib python3-scipy \
                 pciutils dmidecode i2c-tools stressapptest acpica-tools   # all optional but recommended
# pciutils >= 3.11 ships pcilmr (Ubuntu 24.04+); older releases: build from https://mj.ucw.cz/sw/pciutils/
sudo modprobe i2c-i801 ee1004     # exposes DDR4 SPD EEPROMs as /sys/bus/i2c/drivers/ee1004/*/eeprom
```

Only numpy/matplotlib/scipy are hard requirements. Everything else is detected and
reported as present/absent.

## Run

```bash
sudo python3 margindump.py all                      # probe + sweep + eyes + report (~2 min)
sudo python3 margindump.py all --quick --seconds 2  # ~30 s smoke run
sudo python3 margindump.py probe                    # capability probe only
sudo python3 margindump.py margin --seconds 10 --mb 2048
sudo python3 margindump.py lmr --bdf 0000:01:00.0   # Gen4+ links only; runs pcilmr
python3 margindump.py eye --import-1d samples/rmt_per_rank_byte_example.csv
python3 margindump.py eye --import-2d samples/margin2d_example.csv
python3 margindump.py all --simulated               # demo of measured-style output
python3 tests/test_pcie_verdicts.py && python3 tests/test_smbios_spd.py
```

Run as root for: 4 KiB config space (extended caps, i.e. LMR detection), SMBIOS
raw entries, SPD EEPROMs, ACPI tables, /dev/mem for BDAT. Non-root still works and
says what it couldn't see.

## Import contracts

`--import-1d` (per lane / per rank 1-D margins - what LMR and RMT actually produce):

```
id,left_ps,right_ps,up_mv,down_mv[,min_t_ps,min_v_mv]
r0b3,112.5,118.0,97.2,101.4,75,60
```

`--import-2d` (a shmoo / Margin2D / scope export):

```
t_ps,v_mv,value      # value = 1 pass / 0 fail, a BER, or an error count
```

## Layout

```
margindump.py           CLI (probe | margin | eye | lmr | all)
margindump/pcie.py      config-space walker, LMR cap decode, AER counters, pcilmr runner
margindump/bdat.py      ACPI BDAT -> /dev/mem -> schema list (RMT discovery)
margindump/smbios.py    DMI ids, SMBIOS type 17 parser, DDR4 SPD decoder
margindump/memstress.py functional sweep: stressapptest adapter + built-in numpy engine
margindump/eye.py       model eye (right units), margin1d sets, 2-D grid import
margindump/plot.py      renderers (labelled MODEL/MEASURED/SIMULATED/FUNCTIONAL)
margindump/ocptv.py     OCPTV-style JSONL writer (SKIP for absent capability, never FAIL)
margindump/report.py    report.md
tests/                  synthetic config-space + SMBIOS/SPD tests
samples/run_live_sandbox/     real run (2-vCPU VM: no PCIe caps, no DMI - shows graceful degradation)
samples/run_simulated_demo/   --simulated run: Gen4 x16 LMR eye with a weak lane, RMT per-rank eye
```

## Honest limits

- **Gen3 cannot be margined from software.** No tool - open or NDA - changes that.
  The i7-9700F box will produce an LMR verdict table, AER counters, DIMM identity, a
  functional stress margin and a model eye. It will not produce a measured PCIe eye.
- **The DDR4 PHY eye is inside the memory controller's training engine.** Intel
  publishes it only when the BIOS runs RMT and emits BDAT. The BDAT schema GUID ->
  name table (`bdat.py: KNOWN_SCHEMAS`) is intentionally empty until filled from the
  UEFI BDAT Interface Spec v4.0.x; the tool dumps GUIDs + the raw blob so you can.
- **The model eye is a scaled reference, not a measurement.** Parameters are fractions
  of UI / VDDQ so it follows the SPD data rate. It exists to keep units honest.
- **`pcilmr` output parsing is best-effort** (format varies by pciutils version); the raw
  text is always saved next to the JSON.
- The OCPTV output uses the 2.0 artifact names; validate with ocp-diag's tooling before
  claiming conformance.

## Roadmap (what turns this into the collector)

1. `pcie_lmt` / `ocp-diag-pci_lmt` JSONL import (Gen4/5 lanes -> margin1d, no CSV step).
2. BDAT RMT schema decode -> margin1d per rank/byte, automatically, when the table exists.
3. Thermal pre-soak + per-point `sensors` capture so the functional sweep carries temperature.
4. `--identity` block (DIMM serial + NVMe serial + board) as the primary key for every run.

License: MIT.
