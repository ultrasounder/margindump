"""Functional memory margining - the stress proxy you run when the PHY eye is not
reachable from the OS (no RMT in BIOS, AMD without MemEye, etc).

What this IS:  a sweep of data-pattern class (SI aggressiveness) x concurrency
               (bus utilisation / SSN / IR-drop) with bit-exact read-back verify.
What this is NOT: a voltage/timing margin. Nothing in-OS moves Vref or DQS on a
               stock UEFI system. The plot is labelled 'functional stress margin'.

Engine: stressapptest (Google, Apache-2.0) when installed - it is the reference
        tool. Otherwise a built-in numpy engine (copy / invert / verify in threads;
        numpy releases the GIL on large array ops so threads really run in parallel).
        Both report errors per GB moved, bandwidth, and the exact command/parameters.
"""
import os
import re
import threading
import time

import numpy as np

from .runctx import run_cmd, which

# Ordered from SI-benign to SI-aggressive. The 'class' is what the eye axis uses.
PATTERNS = [
    ("solid",     "all-zero / all-one blocks (minimal switching)"),
    ("checker",   "0x55/0xAA alternating (max adjacent-bit toggling)"),
    ("walking1",  "walking-1 across the 64-bit word (single-bit aggressors)"),
    ("prbs",      "pseudo-random 64-bit words (broadband switching noise)"),
    ("rowhammer", "0xFF victim rows between 0x00 aggressor rows (SSN + crosstalk)"),
]


def _pattern_words(name: str, n_words: int, seed: int = 1) -> np.ndarray:
    if name == "solid":
        w = np.zeros(n_words, dtype=np.uint64)
        w[n_words // 2:] = np.uint64(0xFFFFFFFFFFFFFFFF)
        return w
    if name == "checker":
        w = np.empty(n_words, dtype=np.uint64)
        w[0::2] = np.uint64(0x5555555555555555)
        w[1::2] = np.uint64(0xAAAAAAAAAAAAAAAA)
        return w
    if name == "walking1":
        idx = np.arange(n_words, dtype=np.uint64) % np.uint64(64)
        return np.left_shift(np.uint64(1), idx)
    if name == "prbs":
        rng = np.random.default_rng(seed)
        return rng.integers(0, 2 ** 63 - 1, size=n_words, dtype=np.int64).view(np.uint64) \
            ^ np.uint64(0x9E3779B97F4A7C15)
    if name == "rowhammer":
        # 8 KiB "rows": aggressor rows of 0x00, victim rows of 0xFF
        row = 1024  # words of 8 bytes = 8 KiB
        w = np.zeros(n_words, dtype=np.uint64)
        for r in range(1, n_words // row, 2):
            w[r * row:(r + 1) * row] = np.uint64(0xFFFFFFFFFFFFFFFF)
        return w
    raise ValueError(name)


def _worker(name, n_words, seconds, seed, result):
    src = _pattern_words(name, n_words, seed)
    dst = np.empty_like(src)
    inv = np.empty_like(src)
    t_end = time.time() + seconds
    bytes_moved, word_errs, bit_errs, iters = 0, 0, 0, 0
    while time.time() < t_end:
        np.copyto(dst, src)                       # write
        diff = np.bitwise_xor(dst, src)           # read-back verify
        bad = np.count_nonzero(diff)
        if bad:
            word_errs += int(bad)
            bit_errs += int(np.bitwise_count(diff[diff != 0]).sum())
        np.bitwise_not(dst, out=inv)              # invert in place (data-bus toggling)
        diff = np.bitwise_xor(inv, np.bitwise_not(src))
        bad = np.count_nonzero(diff)
        if bad:
            word_errs += int(bad)
            bit_errs += int(np.bitwise_count(diff[diff != 0]).sum())
        bytes_moved += src.nbytes * 4             # 2 writes + 2 reads
        iters += 1
    result.append((bytes_moved, word_errs, bit_errs, iters))


def _builtin_point(pattern: str, threads: int, seconds: float, mb: int) -> dict:
    per_thread_words = max(1024, (mb * 1024 * 1024) // threads // 8)
    results, ths = [], []
    t0 = time.time()
    for i in range(threads):
        th = threading.Thread(target=_worker,
                              args=(pattern, per_thread_words, seconds, 17 + i, results))
        th.start()
        ths.append(th)
    for th in ths:
        th.join()
    dur = time.time() - t0
    moved = sum(r[0] for r in results)
    werr = sum(r[1] for r in results)
    berr = sum(r[2] for r in results)
    gb = moved / 1e9
    return {"engine": "builtin-numpy", "pattern": pattern, "threads": threads,
            "mb_under_test": per_thread_words * 8 * threads // (1024 * 1024),
            "seconds": seconds, "duration_s": round(dur, 2),
            "gb_moved": round(gb, 3), "mbps": round(moved / 1e6 / dur, 1) if dur else 0,
            "word_errors": werr, "bit_errors": berr,
            "errors_per_gb": round(berr / gb, 4) if gb else None,
            "status": "PASS" if berr == 0 else "FAIL",
            "cmd": f"builtin pattern={pattern} threads={threads} s={seconds} mb={mb}"}


def _sat_point(pattern: str, threads: int, seconds: float, mb: int) -> dict:
    """stressapptest adapter. Pattern class maps onto stressapptest's own
    pattern/inversion machinery; concurrency onto copy threads (-m)."""
    invert = {"solid": 0, "checker": 1, "walking1": 1, "prbs": 2, "rowhammer": 2}[pattern]
    argv = ["stressapptest", "-s", str(int(seconds)), "-M", str(mb), "-m", str(threads),
            "-i", str(invert), "-C", str(max(1, threads // 2))]
    t0 = time.time()
    rc, out, err = run_cmd(argv, timeout=int(seconds) + 120)
    dur = time.time() - t0
    text = out + "\n" + err
    status = "PASS" if re.search(r"Status:\s*PASS", text) else "FAIL"
    m_mb = re.search(r"Completed:\s*([\d.]+)M", text)
    m_bw = re.search(r"([\d.]+)MB/s", text)
    m_hw = re.search(r"(\d+)\s+hardware incidents", text)
    m_err = re.search(r"(\d+)\s+errors", text)
    gb = float(m_mb.group(1)) / 1024.0 if m_mb else 0.0
    errs = int(m_err.group(1)) if m_err else (0 if status == "PASS" else -1)
    return {"engine": "stressapptest", "pattern": pattern, "threads": threads,
            "mb_under_test": mb, "seconds": seconds, "duration_s": round(dur, 2),
            "gb_moved": round(gb, 3), "mbps": float(m_bw.group(1)) if m_bw else None,
            "hardware_incidents": int(m_hw.group(1)) if m_hw else None,
            "bit_errors": errs, "errors_per_gb": round(errs / gb, 4) if gb and errs >= 0 else None,
            "status": status if rc in (0, 1) else "ERROR", "rc": rc, "cmd": " ".join(argv),
            "tail": text.strip().splitlines()[-3:]}


def run_sweep(ctx, seconds: float = 3.0, mb: int = 512, quick: bool = True,
              simulated: bool = False, engine: str = "auto") -> dict:
    nproc = os.cpu_count() or 2
    threads_axis = sorted({1, max(2, nproc // 2), nproc}) if quick else \
        sorted({1, 2, max(2, nproc // 2), nproc, nproc * 2})
    patterns = [p for p, _ in PATTERNS] if not quick else ["solid", "checker", "prbs", "rowhammer"]
    if engine == "auto":
        engine = "stressapptest" if which("stressapptest") else "builtin-numpy"
    if simulated:
        engine = "simulated"
    ctx.log(f"functional margin sweep: engine={engine} patterns={patterns} "
            f"threads={threads_axis} {seconds}s x {mb}MB per point")
    points = []
    for pat in patterns:
        for th in threads_axis:
            if simulated:
                pt = _simulated_point(pat, th, seconds, mb)
            elif engine == "stressapptest":
                pt = _sat_point(pat, th, seconds, mb)
            else:
                pt = _builtin_point(pat, th, seconds, mb)
            ctx.log(f"  {pat:>10s} x{th:<2d} threads: {pt['status']:5s} "
                    f"{pt.get('mbps') or 0:8.1f} MB/s  bit_errors={pt.get('bit_errors')}")
            points.append(pt)
    total_err = sum(max(0, p.get("bit_errors") or 0) for p in points)
    return {"engine": engine, "mode": "simulated" if simulated else "live",
            "axes": {"x": "pattern class (SI aggressiveness, low -> high)",
                     "y": "concurrent threads (bus utilisation / SSN / IR-drop proxy)"},
            "x_values": patterns, "y_values": threads_axis,
            "seconds_per_point": seconds, "mb_under_test": mb,
            "points": points, "total_bit_errors": total_err,
            "verdict": "PASS" if total_err == 0 and all(p["status"] != "ERROR" for p in points)
            else ("FAIL" if total_err > 0 else "ERROR"),
            "note": ("functional stress proxy; not a voltage/timing margin. Zero errors "
                     "means 'no observable failure under this stress', not 'wide eye'.")}


def _simulated_point(pattern, threads, seconds, mb):
    """Demo data: errors appear only in the most aggressive corner (prbs/rowhammer at
    max threads) so the plot shows what a marginal DIMM would look like."""
    agg = {"solid": 0, "checker": 1, "walking1": 2, "prbs": 3, "rowhammer": 4}[pattern]
    rng = np.random.default_rng(agg * 31 + threads)
    gb = round(seconds * 2.5 * threads, 2)
    stress = agg / 4.0 + threads / 16.0
    errs = int(rng.poisson(gb * max(0.0, (stress - 0.95) * 3.0)))   # rate grows with stress
    return {"engine": "simulated", "pattern": pattern, "threads": threads,
            "mb_under_test": mb, "seconds": seconds, "duration_s": seconds,
            "gb_moved": gb, "mbps": round(2500 * threads * (1 - 0.05 * agg), 1),
            "word_errors": errs, "bit_errors": errs,
            "errors_per_gb": round(errs / gb, 4), "status": "PASS" if errs == 0 else "FAIL",
            "cmd": "simulated"}
