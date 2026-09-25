"""Inspect or stop only this checkout's portal and guarded render containers."""

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "portal"), str(ROOT / "guard")]
from common import ACTIVE, read_json, JOBS, ID_RE
from resource_guard import PROJECT, stop_owned


def process_matches(pid, script):
    try:
        args = Path(f"/proc/{int(pid)}/cmdline").read_bytes().split(b"\0")
        return str(script).encode() in args
    except (OSError, TypeError, ValueError):
        return False


def owned_containers():
    ids = subprocess.check_output(
        [
            "docker",
            "ps",
            "-q",
            "--no-trunc",
            "--filter",
            f"label=local.project={PROJECT}",
        ],
        text=True,
        timeout=10,
    ).split()
    result = []
    for cid in ids:
        info = json.loads(
            subprocess.check_output(["docker", "inspect", cid], text=True, timeout=5)
        )[0]
        if any(
            m.get("Destination") == "/work" and m.get("Source") == str(ROOT)
            for m in info.get("Mounts", [])
        ):
            result.append(info)
    return result


def stop():
    record = read_json(ROOT / "status/portal-server.json", {})
    pid = record.get("pid")
    if process_matches(pid, ROOT / "portal/server.py"):
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while (
            process_matches(pid, ROOT / "portal/server.py")
            and time.monotonic() < deadline
        ):
            time.sleep(0.1)
        if process_matches(pid, ROOT / "portal/server.py"):
            raise RuntimeError(
                "Portal did not exit. Refusing to report resources released."
            )
    # With the listener stopped there can be no new accepted requests.
    jobs = [read_json(p, {}) for p in JOBS.glob("*.json") if ID_RE.fullmatch(p.stem)]
    active = [job for job in jobs if job.get("state") in ACTIVE]
    for job in active:
        (JOBS / f"{job['id']}.cancel").touch()
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if all(
            read_json(JOBS / f"{job['id']}.json", {}).get("state") not in ACTIVE
            for job in active
        ):
            break
        time.sleep(0.5)
    # Covers an orphan whose worker exited. Guard validates immutable ID/labels.
    for info in owned_containers():
        if info["Config"]["Labels"].get("local.resource-guard") != "required":
            raise RuntimeError(
                "A project setup/validation container is still running; let it finish before stopping."
            )
        stop_owned(info["Id"])
    if owned_containers():
        raise RuntimeError("Project containers still running.")
    workers = []
    for record_path in JOBS.glob("*.runner.json"):
        pid = read_json(record_path, {}).get("pid")
        if process_matches(pid, ROOT / "portal/run_job.py"):
            workers.append(pid)
    if workers:
        raise RuntimeError(
            f"Workers are still finishing cleanup: {workers}. Check status again shortly."
        )
    print(
        "Portal stopped; no render containers or workers remain for this checkout. Disk artifacts retained."
    )


def status():
    record = read_json(ROOT / "status/portal-server.json", {})
    print(
        json.dumps(
            {
                "root": str(ROOT),
                "portal_running": process_matches(
                    record.get("pid"), ROOT / "portal/server.py"
                ),
                "running_project_containers": [
                    item["Name"] for item in owned_containers()
                ],
                "install": read_json(ROOT / "status/install.json", {}).get(
                    "state", "not installed"
                ),
                "download": read_json(ROOT / "status/download.json", {}).get(
                    "state", "not downloaded"
                ),
            },
            indent=2,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "stop"))
    args = parser.parse_args()
    (stop if args.action == "stop" else status)()


if __name__ == "__main__":
    main()
