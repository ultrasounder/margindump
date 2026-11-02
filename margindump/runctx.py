"""Run context: output directory, JSON persistence, subprocess wrapper, tool lookup."""
import datetime as _dt
import json
import os
import shutil
import subprocess
import time


def now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds")


class RunContext:
    def __init__(self, out_root: str = "margindump_runs", run_dir: str = None):
        if run_dir is None:
            run_dir = os.path.join(out_root, now_stamp())
        os.makedirs(run_dir, exist_ok=True)
        self.run_dir = run_dir
        self.t0 = time.time()
        self.log_path = os.path.join(run_dir, "margindump.log")

    def path(self, name: str) -> str:
        return os.path.join(self.run_dir, name)

    def save_json(self, name: str, obj) -> str:
        p = self.path(name)
        with open(p, "w") as f:
            json.dump(obj, f, indent=2, default=_json_default)
        return p

    def log(self, msg: str, quiet: bool = False):
        line = f"[{time.time() - self.t0:7.2f}s] {msg}"
        if not quiet:
            print(line, flush=True)
        with open(self.log_path, "a") as f:
            f.write(line + "\n")


def _json_default(o):
    try:
        import numpy as np
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
    except ImportError:
        pass
    if isinstance(o, bytes):
        return o.hex()
    return str(o)


def which(tool: str):
    return shutil.which(tool)


def run_cmd(cmd, timeout: int = 60):
    """Run a command (list or str). Returns (rc, stdout, stderr). rc=127 if missing."""
    if isinstance(cmd, str):
        argv = cmd.split()
    else:
        argv = list(cmd)
    if which(argv[0]) is None:
        return 127, "", f"{argv[0]}: not found"
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        return 124, (e.stdout or "") if isinstance(e.stdout, str) else "", "timeout"
    except OSError as e:
        return 126, "", str(e)


def is_root() -> bool:
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def read_text(path: str, default: str = None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default
