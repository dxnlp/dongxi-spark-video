"""Bounded host telemetry for one capacity trial; never starts GPU work.

Memory protection remains in the independent 250 ms resource guard. A blocked
GPU query cannot prevent that guard from running. This monitor can cancel only
the exact portal job it was given.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "portal"))
from common import ID_RE, read_json, write_json, job_path, JOBS

FAULTS = r"Out of memory|oom-kill|Killed process|NVRM:.*Xid|NV_ERR|GPU has fallen|soft lockup|hard LOCKUP|blocked for more than"


def sample(start):
    gpu = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=temperature.gpu,utilization.gpu,power.draw",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        timeout=2,
        check=True,
    ).stdout.strip()
    kernel = subprocess.run(
        [
            "journalctl",
            "-k",
            "--since",
            f"@{start:.0f}",
            "--no-pager",
            "-n",
            "40",
            "--grep",
            FAULTS,
        ],
        capture_output=True,
        text=True,
        timeout=2,
    )
    if kernel.returncode not in (0, 1) or "not seeing messages" in kernel.stderr:
        raise RuntimeError("Kernel log monitor unavailable")
    faults = kernel.stdout.strip()
    if faults == "-- No entries --":
        faults = ""
    temperature = float(gpu.split(",")[0])
    return dict(
        time=time.time(), gpu=gpu, temperature_c=temperature, kernel_faults=faults
    )


def main(job_id):
    assert ID_RE.fullmatch(job_id)
    start = time.time()
    path = ROOT / "evidence" / f"{job_id}-health.jsonl"
    with path.open("x") as log:
        while time.time() - start < 360:
            job = read_json(job_path(job_id), {})
            try:
                data = sample(start)
                reason = (
                    "New kernel GPU/memory fault"
                    if data["kernel_faults"]
                    else "GPU temperature reached 85 C"
                    if data["temperature_c"] >= 85
                    else None
                )
            except Exception as error:
                reason = "Health telemetry failed: " + str(error)
                data = dict(time=time.time(), error=reason)
            if reason:
                data["cancel_reason"] = reason
                (JOBS / f"{job_id}.cancel").touch()
                write_json(ROOT / "evidence" / f"{job_id}-health-stop.json", data)
            log.write(json.dumps(data) + "\n")
            log.flush()
            if reason or job.get("state") in ("complete", "failed", "cancelled"):
                return
            time.sleep(2)
        (JOBS / f"{job_id}.cancel").touch()


if __name__ == "__main__":
    main(sys.argv[1])
