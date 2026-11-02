"""OCP Test & Validation (OCPTV / ocp-diag) style JSONL writer.

One artifact per line, in the shape used by ocp-diag-core and ocp-diag-pci_lmt:
testRunStart (with dutInfo) -> testStepStart / measurement / log / diagnosis /
testStepEnd ... -> testRunEnd. A capability that is absent ends its step with
status SKIP and a measurement 'supported=false' - never a FAIL diagnosis.

This is 'OCPTV-shaped' (schema 2.0 field names); run it through ocp-diag's
validator before claiming conformance.
"""
import json

from .runctx import iso_now


class OcptvWriter:
    def __init__(self, path: str, name: str, version: str, cmdline: str, dut: dict):
        self.path = path
        self.seq = 0
        self.step_id = 0
        self.f = open(path, "w")
        self.hw_ids = {}
        hw = []
        for i, d in enumerate(dut.get("dimms", [])):
            hid = f"dimm{i}"
            self.hw_ids[hid] = hid
            hw.append({"hardwareInfoId": hid, "name": d.get("locator", hid),
                       "manufacturer": d.get("manufacturer"), "partNumber": d.get("part_number"),
                       "serialNumber": d.get("serial"), "computerSystem": dut.get("product_name"),
                       "version": f"{d.get('type')}-{d.get('configured_speed_mts') or d.get('speed_mts')}"})
        for fn in dut.get("pcie_functions", []):
            hid = f"pci_{fn['bdf']}"
            self.hw_ids[hid] = hid
            hw.append({"hardwareInfoId": hid, "name": fn.get("name"),
                       "location": fn["bdf"], "version": fn.get("link", {}).get("cur_gen")})
        self._emit("testRunArtifact", {"testRunStart": {
            "name": name, "version": version, "commandLine": cmdline, "parameters": {},
            "dutInfo": {"dutInfoId": "dut0", "name": dut.get("product_name") or "linux-pc",
                        "platformInfos": [{"info": f"{k}={v}"} for k, v in dut.items()
                                          if isinstance(v, str) and v],
                        "hardwareInfos": hw,
                        "softwareInfos": [{"softwareInfoId": "os", "name": "kernel",
                                           "version": dut.get("kernel"), "softwareType": "SYSTEM"}]}}})

    def _emit(self, kind: str, body: dict):
        self.seq += 1
        rec = {"sequenceNumber": self.seq, "timestamp": iso_now(), kind: body}
        self.f.write(json.dumps(rec, default=str) + "\n")
        self.f.flush()

    def step_start(self, name: str) -> str:
        self.step_id += 1
        sid = str(self.step_id)
        self._emit("testStepArtifact", {"testStepId": sid, "testStepStart": {"name": name}})
        return sid

    def measurement(self, sid: str, name: str, value, unit: str = None,
                    hw_id: str = None, validators: list = None):
        body = {"name": name, "value": value}
        if unit:
            body["unit"] = unit
        if hw_id:
            body["hardwareInfoId"] = hw_id
        if validators:
            body["validators"] = validators
        self._emit("testStepArtifact", {"testStepId": sid, "measurement": body})

    def log(self, sid: str, msg: str, severity: str = "INFO"):
        self._emit("testStepArtifact", {"testStepId": sid, "log": {"severity": severity,
                                                                     "message": msg}})

    def diagnosis(self, sid: str, verdict: str, dtype: str, hw_id: str = None, msg: str = None):
        body = {"verdict": verdict, "type": dtype}
        if hw_id:
            body["hardwareInfoId"] = hw_id
        if msg:
            body["message"] = msg
        self._emit("testStepArtifact", {"testStepId": sid, "diagnosis": body})

    def step_end(self, sid: str, status: str = "COMPLETE"):
        self._emit("testStepArtifact", {"testStepId": sid, "testStepEnd": {"status": status}})

    def run_end(self, status: str, result: str):
        self._emit("testRunArtifact", {"testRunEnd": {"status": status, "result": result}})
        self.f.close()
