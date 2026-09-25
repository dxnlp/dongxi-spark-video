"""Shared portal state; no third-party packages and no model imports."""

import json
import os
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from resource_profiles import FRAME_PROFILES, profile

ROOT = Path(__file__).resolve().parent.parent
JOBS = ROOT / "portal/jobs"
ACTIVE = {"starting", "running", "validating"}
ID_RE = re.compile(r"portal-\d{8}-\d{6}-[0-9a-f]{8}")
GIB = 1024**3


def valid_id(value):
    return isinstance(value, str) and bool(ID_RE.fullmatch(value))


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def job_path(job_id):
    if not valid_id(job_id):
        raise ValueError("Invalid render ID")
    return JOBS / f"{job_id}.json"


def validate_request(data):
    if not isinstance(data, dict):
        raise ValueError("Expected a prompt")
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Enter a prompt first.")
    prompt = prompt.strip()
    if len(prompt) > 1200 or "\x00" in prompt:
        raise ValueError("Keep the prompt within 1,200 characters.")
    seed = data.get("seed")
    if seed is not None and (type(seed) is not int or not 0 <= seed <= 2147483647):
        raise ValueError("Seed must be a whole number from 0 to 2147483647.")
    return prompt, seed


def duration_presets():
    base = {
        "frames": 124,
        "seconds": 124 / 24,
        "render_seconds": 139,
        "label": "5.2 seconds",
        "memory_profile": "standard",
    }
    records = read_json(ROOT / "portal/duration-presets.json", [])
    result = [base]
    if not isinstance(records, list):
        return result
    for preset in records:
        if not isinstance(preset, dict):
            continue
        frames = preset.get("frames")
        seconds = preset.get("seconds")
        if (
            type(frames) is int
            and 124 < frames <= 362
            and type(seconds) in (int, float)
            and 5 < seconds < 16
        ):
            expected = FRAME_PROFILES.get(frames)
            if expected is None or preset.get("memory_profile", expected) != expected:
                continue
            if not any(p["frames"] == frames for p in result):
                result.append({**preset, "memory_profile": expected})
    return sorted(result, key=lambda p: p["frames"])


def request_frames(data):
    return request_preset(data)["frames"]


def request_preset(data):
    frames = data.get("frames", 124)
    preset = next((p for p in duration_presets() if p["frames"] == frames), None)
    if type(frames) is not int or preset is None:
        raise ValueError("Choose a tested video length from the portal.")
    return preset


def memory():
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key = line.split(":", 1)[0]
        if key in ("MemTotal", "MemAvailable", "MemFree", "SwapTotal", "SwapFree"):
            values[key] = int(line.split()[1]) * 1024
    return {
        "total": values["MemTotal"],
        "available": values["MemAvailable"],
        "used": values["MemTotal"] - values["MemAvailable"],
        "free": values["MemFree"],
        "swap_used": values["SwapTotal"] - values["SwapFree"],
        "reserve": 32 * GIB,
    }


def events_for(job_id):
    rows = []
    for path in (ROOT / "outputs" / job_id).glob("observer-*.jsonl"):
        # Writes may be in progress; ignore only incomplete JSON lines.
        for line in path.read_text()[-2_000_000:].splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return sorted(rows, key=lambda e: e.get("wall_time", 0))


def progress(events, state):
    percent = 1
    stage = "Starting renderer"
    step = 0
    block = 0
    shards = {"text_encoder": set(), "transformer": set()}
    for event in events:
        kind = event.get("event")
        component = event.get("component")
        if kind == "generation_request_start":
            percent = max(percent, 5)
            stage = "Preparing prompt"
        if kind == "component_load_start" and component == "text_encoder":
            percent = max(percent, 6)
            stage = "Loading prompt encoder"
        if kind == "checkpoint_cache_released":
            family = event.get("file", "").split("/")[0]
            if family in shards:
                shards[family].add(event["file"])
                start, end = (6, 30) if family == "text_encoder" else (32, 57)
                percent = max(
                    percent, start + (end - start) * min(14, len(shards[family])) / 14
                )
                stage = (
                    "Loading prompt encoder"
                    if family == "text_encoder"
                    else "Loading video model"
                )
        if kind == "component_load_complete" and component == "text_encoder":
            percent = max(percent, 31)
            stage = "Encoding prompt"
        if kind == "component_load_start" and component == "transformer":
            percent = max(percent, 32)
            stage = "Loading video model"
        if kind == "component_load_complete" and component == "transformer":
            percent = max(percent, 57)
            stage = "Starting diffusion"
        if kind in ("forward_start", "block_complete", "forward_complete"):
            step = event.get("number", 1)
            block = event.get("block", 50 if kind == "forward_complete" else 0)
            percent = max(percent, 57 + 36 * ((step - 1) + block / 50) / 4)
            stage = f"Generating motion · step {step} of 4"
        if kind == "forward_complete" and step == 4:
            stage = "Decoding video"
            percent = max(percent, 94)
        if kind == "component_load_start" and component == "audio_vae":
            stage = "Decoding audio"
            percent = max(percent, 96)
        if kind == "generation_request_complete":
            stage = "Checking video"
            percent = max(percent, 98)
    if state == "validating":
        stage = "Checking video"
        percent = max(percent, 99)
    if state == "complete":
        return {"percent": 100, "stage": "Ready to play", "step": 4, "block": 50}
    if state == "cancelled":
        stage = "Render stopped"
    if state == "failed":
        stage = "Render stopped with an error"
    return {
        "percent": round(min(99, percent), 1),
        "stage": stage,
        "step": step,
        "block": block,
    }


def job_view(job):
    events = events_for(job["id"])
    latest = next((e for e in reversed(events) if "allocated_bytes" in e), {})
    guard = read_json(ROOT / "status" / f"{job['id']}-guard.json", {})
    start = job.get("started_at", job["created_at"])
    end = job.get("ended_at", time.time())
    cancel = (JOBS / f"{job['id']}.cancel").exists() and job["state"] in ACTIVE
    gpu_live = job["state"] in ACTIVE and guard.get("state") not in (
        "container_exited",
        "aborted_container_stopped",
        "abort_stop_unconfirmed",
    )
    view = {
        **job,
        **progress(events, job["state"]),
        "elapsed": max(0, end - start),
        "host_reserve": guard.get(
            "absolute_available_reserve_bytes",
            profile(job.get("memory_profile", "standard"))["reserve_gib"] * GIB,
        ),
        "cancel_requested": cancel,
        "cuda_allocated": latest.get("allocated_bytes") if gpu_live else 0,
        "cuda_reserved": latest.get("reserved_bytes") if gpu_live else 0,
        "cuda_sample_time": latest.get("wall_time"),
        "peak_host_used": (
            guard.get("initial_memory", {}).get("MemTotal", 0)
            - guard.get("minimum_available_bytes", 0)
        )
        if guard
        else None,
    }
    if cancel:
        view["stage"] = "Stopping safely…"
    if job["state"] == "complete":
        view["video_url"] = f"/media/{job['id']}/video"
        if (ROOT / "outputs" / job["id"] / "poster.jpg").exists():
            view["poster_url"] = f"/media/{job['id']}/poster"
    return view


def media_path(job_id, kind):
    job = read_json(job_path(job_id))
    if not job or job.get("state") != "complete":
        raise FileNotFoundError("Video is not ready")
    output = ROOT / "outputs" / job_id
    if kind == "video":
        files = list(output.glob("*.mp4"))
        if len(files) != 1:
            raise FileNotFoundError("Video not found")
        path = files[0]
    elif kind == "poster":
        path = output / "poster.jpg"
    else:
        raise FileNotFoundError("Media not found")
    if not path.resolve().is_relative_to(output.resolve()) or not path.is_file():
        raise FileNotFoundError("Media not found")
    return path


def byte_range(header, size):
    if header is None:
        return 0, size - 1, False
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header)
    if not match or not any(match.groups()):
        raise ValueError("Invalid byte range")
    left, right = match.groups()
    if left:
        start = int(left)
        end = min(size - 1, int(right)) if right else size - 1
    else:
        if int(right) <= 0:
            raise ValueError("Invalid byte range")
        start = max(0, size - int(right))
        end = size - 1
    if start >= size or end < start:
        raise ValueError("Unsatisfiable byte range")
    return start, end, True
