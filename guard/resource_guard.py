"""Host-local guard for one NEW, explicitly labelled container.

Default operation is read-only. --execute starts a stopped container and watches
host memory. This is a secondary safeguard, not a substitute for a peak-memory
budget. Only the launcher may admit a new job after its readiness checks.
"""

import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import time
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from resource_profiles import profile

GIB = 1024**3
PROJECT = "dongxi-spark-video"
START_RESERVE = 32 * GIB
ABORT_RESERVE = 32 * GIB


def read_memory(path=Path("/proc/meminfo")):
    values = {}
    for line in path.read_text().splitlines():
        key, value = line.split(":", 1)
        if key in (
            "MemTotal",
            "MemAvailable",
            "MemFree",
            "Cached",
            "Buffers",
            "SReclaimable",
            "SwapTotal",
            "SwapFree",
        ):
            fields = value.split()
            if len(fields) != 2 or fields[1] != "kB":
                raise ValueError("Unexpected /proc/meminfo units")
            values[key] = int(fields[0]) * 1024
    if not 0 < values["MemAvailable"] <= values["MemTotal"]:
        raise ValueError("Invalid or absent host available-memory reading")
    return values


def read_pressure(path=Path("/proc/pressure/memory")):
    values = {}
    for line in path.read_text().splitlines():
        fields = line.split()
        row = dict(item.split("=", 1) for item in fields[1:])
        values[fields[0]] = float(row["avg10"])
    if set(values) != {"some", "full"} or any(
        not math.isfinite(v) or not 0 <= v <= 100 for v in values.values()
    ):
        raise ValueError("Invalid host memory pressure reading")
    return values


def abort_reason(memory, pressure, reserve=ABORT_RESERVE):
    if memory.get("MemFree", 8 * GIB) < 8 * GIB:
        return "Host immediately free memory below additional 8 GiB reserve"
    if memory["MemAvailable"] < reserve:
        return f"Host available memory below {reserve / GIB:g} GiB reserve"
    if pressure["full"] >= 1.0 or pressure["some"] >= 5.0:
        return "Host memory pressure exceeds conservative limit"
    return None


def validate_target(info, requested_id, memory_profile="standard"):
    limits = profile(memory_profile)
    if not re.fullmatch(r"[0-9a-f]{64}", requested_id):
        raise ValueError("Full immutable container ID is required")
    if info.get("Id") != requested_id:
        raise ValueError("Container identity mismatch")
    labels = info.get("Config", {}).get("Labels") or {}
    if labels.get("local.project") != PROJECT:
        raise ValueError("Container is not owned by this project")
    if labels.get("local.resource-guard") != "required":
        raise ValueError("Container was not prepared for guarded execution")
    if labels.get("local.memory-profile", "standard") != memory_profile:
        raise ValueError("Container and guard resource profiles differ")
    if info["State"].get("Running") or info["State"].get("Restarting"):
        raise ValueError("Refusing to attach after a workload has already started")
    policy = info.get("HostConfig", {}).get("RestartPolicy") or {}
    if policy.get("Name") not in ("", "no"):
        raise ValueError("Automatic container restart must be disabled")
    host = info.get("HostConfig", {})
    limit = host.get("Memory", 0)
    if (
        not 0 < limit <= limits["container_gib"] * GIB
        or host.get("MemorySwap") != limit
    ):
        raise ValueError(
            "Require the selected profile's memory cap and no extra container swap"
        )
    config = info["Config"]
    command = config.get("Cmd") or []
    if (
        config.get("Hostname") != requested_id[:12]
        or config.get("Entrypoint") != ["python3"]
        or command[:2] != ["/guard-code/resource_entrypoint.py", "--status"]
        or len(command) < 5
        or command[3] != "--"
    ):
        raise ValueError("Container must use the checked guard entrypoint")


def docker(*args, timeout=5):
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout, check=True
    ).stdout


def inspect(container_id):
    return json.loads(docker("inspect", container_id, timeout=1))[0]


def stop_owned(container_id, inspect_fn=inspect, run_fn=docker):
    """Recheck ownership and stop this exact ID; never match names or kill host PIDs."""
    info = inspect_fn(container_id)
    labels = info.get("Config", {}).get("Labels") or {}
    if (
        info.get("Id") != container_id
        or labels.get("local.project") != PROJECT
        or labels.get("local.resource-guard") != "required"
    ):
        raise RuntimeError("Refusing stop: target identity or ownership changed")
    if not info["State"].get("Running"):
        return
    try:
        run_fn("stop", "--time", "1", container_id, timeout=4)
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
        run_fn("kill", container_id, timeout=4)
    if inspect_fn(container_id)["State"].get("Running"):
        raise RuntimeError("Stop command returned but container is still running")


def supervise(
    container_id, status_path, max_seconds, peak_budget_gib, memory_profile="standard"
):
    limits = profile(memory_profile)
    reserve = limits["reserve_gib"] * GIB
    if not 1 <= peak_budget_gib <= limits["host_growth_gib"]:
        raise ValueError("Growth budget exceeds selected profile")
    if not 1 <= max_seconds <= 300:
        raise ValueError("All renders are limited to 300 seconds")
    info = inspect(container_id)
    validate_target(info, container_id, memory_profile)
    if info["Config"]["Cmd"][2] != f"/guard-status/{status_path.name}":
        raise ValueError("Container lease path differs from guard status path")
    mounts = {m["Destination"]: m for m in info.get("Mounts", [])}
    if (
        mounts.get("/guard-status", {}).get("Source")
        != str(status_path.parent.resolve())
        or mounts.get("/guard-status", {}).get("RW") is not False
        or mounts.get("/guard-code", {}).get("RW") is not False
    ):
        raise ValueError(
            "Require read-only guard code and matching status directory mounts"
        )
    memory, pressure = read_memory(), read_pressure()
    required = max(START_RESERVE, peak_budget_gib * GIB + reserve)
    if memory["MemAvailable"] < required or abort_reason(memory, pressure, reserve):
        raise RuntimeError(
            f"Admission refused: need {required / GIB:g} GiB available and low pressure"
        )
    status_path.parent.mkdir(parents=True, exist_ok=True)
    minimum_available = memory["MemAvailable"]
    minimum_free = memory.get("MemFree", 8 * GIB)
    initial_memory = dict(memory)
    initial_available = memory["MemAvailable"]
    initial_swap_used = memory["SwapTotal"] - memory["SwapFree"]
    maximum_swap_used = initial_swap_used
    maximum_pressure = dict(pressure)

    def record(state, **extra):
        data = dict(
            state=state,
            guard_pid=os.getpid(),
            container_id=container_id,
            time=time.time(),
            monotonic=time.monotonic(),
            peak_budget_gib=peak_budget_gib,
            absolute_available_reserve_bytes=reserve,
            memory_profile=memory_profile,
            minimum_available_bytes=minimum_available,
            minimum_free_bytes=minimum_free,
            initial_memory=initial_memory,
            **extra,
        )
        data.setdefault("memory", memory)
        data.setdefault("pressure", pressure)
        data["maximum_swap_used_bytes"] = maximum_swap_used
        data["maximum_pressure_avg10"] = dict(maximum_pressure)
        temporary = status_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2) + "\n")
        temporary.replace(status_path)
        if memory_profile != "standard":
            with status_path.with_suffix(".samples.jsonl").open("a") as samples:
                samples.write(json.dumps(data, separators=(",", ":")) + "\n")

    start_command = None
    armed = False
    try:
        record("armed", memory=memory, pressure=pressure)
        armed = True
        # Start asynchronously so monitoring continues during model loading.
        start_command = subprocess.Popen(
            ["docker", "start", container_id],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        started = time.monotonic()
        last_check = started
        next_inspect = started
        while True:
            now = time.monotonic()
            if now - last_check > 2:
                raise RuntimeError("Guard scheduling gap exceeded two seconds")
            last_check = now
            memory, pressure = read_memory(), read_pressure()
            minimum_available = min(minimum_available, memory["MemAvailable"])
            minimum_free = min(minimum_free, memory.get("MemFree", 8 * GIB))
            maximum_swap_used = max(
                maximum_swap_used, memory["SwapTotal"] - memory["SwapFree"]
            )
            for key in maximum_pressure:
                maximum_pressure[key] = max(maximum_pressure[key], pressure[key])
            reason = abort_reason(memory, pressure, reserve)
            if reason:
                raise RuntimeError(reason)
            if initial_available - memory["MemAvailable"] > peak_budget_gib * GIB:
                raise RuntimeError(
                    "Host available-memory reduction exceeded the stage budget"
                )
            if (
                memory["SwapTotal"] - memory["SwapFree"] - initial_swap_used
                >= 256 * 1024**2
            ):
                raise RuntimeError("New swap consumption reached 256 MiB")
            if now - started >= max_seconds:
                raise RuntimeError("Maximum guarded runtime exceeded")
            if start_command.poll() not in (None, 0):
                raise RuntimeError("Docker start failed")
            if start_command.poll() is None and now - started > 10:
                raise RuntimeError("Docker start exceeded ten seconds")
            if start_command.poll() == 0 and now >= next_inspect:
                state = inspect(container_id)["State"]
                if not state.get("Running"):
                    record(
                        "container_exited",
                        exit_code=state.get("ExitCode"),
                        memory=memory,
                        pressure=pressure,
                    )
                    return
                next_inspect = time.monotonic() + 2
            record("watching", memory=memory, pressure=pressure)
            time.sleep(0.25)
    except BaseException as error:
        if armed:
            try:
                record("aborting", reason=str(error))
            except Exception:
                # The entrypoint's stale-heartbeat timeout remains armed.
                pass
            try:
                stop_owned(container_id)
                record("aborted_container_stopped", reason=str(error))
            except Exception as stop_error:
                record(
                    "abort_stop_unconfirmed",
                    reason=str(error),
                    stop_error=str(stop_error),
                )
        raise
    finally:
        if start_command is not None and start_command.poll() is None:
            # Only stop the local docker CLI child, not an arbitrary host process.
            start_command.terminate()
            try:
                start_command.wait(timeout=2)
            except subprocess.TimeoutExpired:
                start_command.kill()
                start_command.wait(timeout=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container-id", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--status", type=Path)
    parser.add_argument("--max-seconds", type=int, default=300)
    parser.add_argument("--peak-budget-gib", type=int, default=80)
    parser.add_argument("--profile", default="standard")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{64}", args.container_id):
        parser.error("Provide the full immutable container ID")
    if not 1 <= args.max_seconds <= 300:
        parser.error("Runtime must be between one and 300 seconds")
    limits = profile(args.profile)
    if not 1 <= args.peak_budget_gib <= limits["host_growth_gib"]:
        parser.error("Peak budget exceeds selected resource profile")
    if args.execute:
        if args.status is None:
            parser.error("Execution requires an explicit status file")
        args.status.parent.mkdir(parents=True, exist_ok=True)
        # One guard per project status directory; a lost SSH session does not
        # release this host-side process's lock.
        lock = (args.status.parent / "resource-guard.lock").open("a")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

        def interrupted(signum, frame):
            raise RuntimeError(f"Guard received signal {signum}")

        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        supervise(
            args.container_id,
            args.status,
            args.max_seconds,
            args.peak_budget_gib,
            args.profile,
        )
    else:
        validate_target(inspect(args.container_id), args.container_id, args.profile)
        print(
            json.dumps(
                dict(mode="read_only", memory=read_memory(), pressure=read_pressure()),
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
