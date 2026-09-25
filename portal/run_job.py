"""Detached, single-render worker. Existing host guard owns GPU termination."""

from datetime import datetime
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback

from common import ROOT, JOBS, ID_RE, read_json, write_json, job_path, events_for

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "guard"))
from launch_gpu import launch
from runtime_checks import validate_output, verify_dependencies, inspect
from resource_guard import stop_owned
from capacity_monitor import sample as health_sample


def start_health_monitor(job_id):
    health = health_sample(time.time())
    if health["kernel_faults"] or health["temperature_c"] >= 70:
        raise RuntimeError(
            "Spark needs to cool down or clear the reported GPU fault before rendering."
        )
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts/capacity_monitor.py"), job_id],
        stdin=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    write_json(JOBS / f"{job_id}.monitor.json", {"pid": process.pid})
    return process


def cancel_launch(item):
    """Withdraw this exact guard, then stop only its labelled container."""
    pid = item["guard_pid"]
    try:
        command = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        if (
            str(ROOT / "guard/resource_guard.py").encode() in command
            and item["container_id"].encode() in command
        ):
            os.kill(pid, signal.SIGTERM)
    except (FileNotFoundError, ProcessLookupError):
        pass
    stop_owned(item["container_id"])


def poster(job_id):
    output = ROOT / "outputs" / job_id
    media = next(output.glob("*.mp4"))
    image = read_json(ROOT / "evidence/base-image.json")["prepared_image"]
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--label",
            "local.project=dongxi-spark-video",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--network",
            "none",
            "--cpus",
            "1",
            "--memory",
            "1g",
            "--memory-swap",
            "1g",
            "--cap-drop",
            "ALL",
            "-e",
            "NVIDIA_VISIBLE_DEVICES=void",
            "--mount",
            f"type=bind,src={output},dst=/output",
            "--entrypoint",
            "ffmpeg",
            image,
            "-v",
            "error",
            "-i",
            "/output/" + media.name,
            "-frames:v",
            "1",
            "-vf",
            "scale=832:480",
            "-y",
            "/output/poster.jpg",
        ],
        check=True,
        timeout=25,
    )


def run(job_id):
    assert ID_RE.fullmatch(job_id)
    path = job_path(job_id)
    job = read_json(path)
    assert job and job["state"] == "starting"
    item = None
    monitor = None
    cancel = JOBS / f"{job_id}.cancel"

    def update(state, **extra):
        job.update(state=state, updated_at=time.time(), **extra)
        write_json(path, job)

    def interrupted(signum, frame):
        cancel.touch()

    signal.signal(signal.SIGTERM, interrupted)
    lock = (ROOT / "status/coordinator.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if cancel.exists():
            update("cancelled", ended_at=time.time())
            return
        verify_dependencies()
        update("running", started_at=time.time())
        frames = job.get("frames", 124)
        if job.get("memory_profile", "standard") != "standard":
            monitor = start_health_monitor(job_id)
        if cancel.exists():
            update("cancelled", ended_at=time.time())
            return
        item = launch(
            job_id,
            prompt=job["prompt"],
            seed=job["seed"],
            num_frames=frames,
            memory_profile=job.get("memory_profile", "standard"),
        )
        update("running", container_id=item["container_id"])
        deadline = time.monotonic() + item["deadline_seconds"] + 20
        guard = {}
        state = None
        while time.monotonic() < deadline:
            guard = read_json(ROOT / "status" / f"{job_id}-guard.json", {})
            if cancel.exists():
                cancel_launch(item)
                state = inspect(item["container_id"])["State"]
                # Guard must also be gone before releasing the render slot.
                try:
                    status = Path(f"/proc/{item['guard_pid']}/status").read_text()
                    guard_alive = not any(
                        line.startswith("State:") and "Z" in line
                        for line in status.splitlines()
                    )
                except FileNotFoundError:
                    guard_alive = False
                if not state["Running"] and not guard_alive:
                    break
            if guard.get("state") in (
                "aborted_container_stopped",
                "abort_stop_unconfirmed",
                "container_exited",
            ):
                state = inspect(item["container_id"])["State"]
                if not state["Running"]:
                    break
            if (
                monitor is not None
                and monitor.poll() is not None
                and not cancel.exists()
            ):
                raise RuntimeError(
                    "The temperature/kernel monitor stopped. Stopping this render safely."
                )
            time.sleep(0.5)
        if state is None or state["Running"]:
            raise RuntimeError("Render deadline reached; stopping this render.")
        logs = subprocess.check_output(
            ["docker", "logs", item["container_id"]],
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
        )
        (ROOT / "logs" / f"{job_id}.log").write_text(logs)
        write_json(ROOT / "evidence" / f"{job_id}-state.json", state)
        if cancel.exists():
            fault = read_json(ROOT / "evidence" / f"{job_id}-health-stop.json", {})
            update(
                "failed" if fault else "cancelled",
                ended_at=time.time(),
                error=fault.get("cancel_reason"),
            )
            return
        if state["ExitCode"] != 0 or guard.get("state") != "container_exited":
            if "OutOfMemoryError" in logs and "GiB allowed" in logs:
                cap = item["resource_limits"]["cuda_gib"]
                raise RuntimeError(
                    f"This render reached the {cap} GiB GPU allocation cap. It was stopped; try the shorter length. No automatic retry was made."
                )
            raise RuntimeError(
                guard.get("reason")
                or "Renderer exited with an error. See the saved render log."
            )
        update("validating")
        media = validate_output(job_id, state, expected_frames=frames)
        video = next(s for s in media["streams"] if s["codec_type"] == "video")
        if cancel.exists():
            update("cancelled", ended_at=time.time())
            return
        try:
            poster(job_id)
        except Exception as exc:
            print("Poster optional:", str(exc), flush=True)
        events = events_for(job_id)
        completion = next(
            e for e in events if e["event"] == "generation_request_complete"
        )
        start = datetime.fromisoformat(
            state["StartedAt"].replace("Z", "+00:00")
        ).timestamp()
        update(
            "complete",
            ended_at=time.time(),
            render_seconds=completion["wall_time"] - start,
            generation_seconds=completion["seconds"],
            duration=float(video["duration"]),
            width=832,
            height=480,
            requested_frames=frames,
            frames=int(video["nb_read_frames"]),
            full_decode="passed",
        )
    except BaseException as exc:
        traceback.print_exc()
        safe = True
        if item:
            try:
                cancel_launch(item)
            except Exception:
                safe = False
                traceback.print_exc()
        update(
            "cancelled" if cancel.exists() and safe else "failed",
            ended_at=time.time(),
            error=str(exc) or type(exc).__name__,
            stop_confirmed=safe,
        )
    finally:
        try:
            if monitor is not None:
                try:
                    monitor.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    monitor.terminate()
                    monitor.wait(timeout=3)
        finally:
            lock.close()


if __name__ == "__main__":
    run(sys.argv[1])
