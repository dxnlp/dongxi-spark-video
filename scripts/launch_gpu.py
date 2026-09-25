"""Admit and launch one exact bounded job using an independent host guard."""

import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time
from resource_profiles import FRAME_PROFILES, profile

ROOT = Path(__file__).resolve().parent.parent
GIB = 1024**3
PROJECT = "dongxi-spark-video"


def run(args, timeout=15):
    return subprocess.check_output(args, text=True, timeout=timeout).strip()


DEFAULT_PROMPT = "A wide cinematic shot of an alpine meadow at sunrise, pale pink mountain peaks above a blue valley filled with thin morning mist."


def validate_frames(num_frames):
    # Bounded duration experiments; the public portal still controls its own
    # independently verified choices. Named capacity profiles are separate experiments.
    if type(num_frames) is not int or not 124 <= num_frames <= 362:
        raise ValueError("Frame count must be an integer from 124 to 362")
    return num_frames


def aligned_frames(num_frames):
    # Mirrors pinned upstream minimax_h3/packing.py: causal chunks are 17*n+5.
    validate_frames(num_frames)
    return num_frames + (5 - num_frames) % 17


def launch(
    attempt,
    *,
    prompt=DEFAULT_PROMPT,
    seed=2026,
    num_frames=124,
    memory_profile="standard",
):
    limits = profile(memory_profile)
    validate_frames(num_frames)
    if os.environ.get("SPARK_VIDEO_DISABLE_GENERATION") == "1":
        raise RuntimeError("Generation disabled for this process")
    portal = bool(re.fullmatch(r"portal-\d{8}-\d{6}-[0-9a-f]{8}", attempt))
    assert portal, "Use a unique portal render ID"
    assert (
        num_frames in (124, 148, 362) and FRAME_PROFILES[num_frames] == memory_profile
    ), "Unsupported duration/profile pair"
    assert (
        isinstance(prompt, str)
        and 0 < len(prompt.strip()) <= 1200
        and "\x00" not in prompt
    )
    assert type(seed) is int and 0 <= seed <= 2147483647
    assert (
        json.loads((ROOT / "status/install.json").read_text())["state"] == "installed"
    )
    assert (
        json.loads((ROOT / "status/download.json").read_text())["state"] == "complete"
    )
    assert not run(["docker", "ps", "-q"]), "Other container work is still active"
    assert not run(
        ["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader"]
    ), "GPU already in use"
    mem = {
        s.split(":")[0]: int(s.split()[1]) * 1024
        for s in Path("/proc/meminfo").read_text().splitlines()
        if ":" in s
    }
    admission = max(112, limits["host_growth_gib"] + limits["reserve_gib"])
    assert mem["MemAvailable"] >= admission * GIB and mem["MemFree"] >= 80 * GIB, (
        "Host admission memory insufficient"
    )
    for line in Path("/proc/pressure/memory").read_text().splitlines():
        row = dict(x.split("=") for x in line.split()[1:])
        assert float(row["avg10"]) < 1.0, "Memory pressure at admission"
    output = ROOT / "outputs" / attempt
    assert not output.exists(), "Attempt already exists; never duplicate or overwrite"
    output.mkdir()
    status = ROOT / "status" / f"{attempt}-guard.json"
    assert not status.exists()
    image = json.loads((ROOT / "evidence/base-image.json").read_text())[
        "prepared_image"
    ]
    name = f"dongxi-spark-video-{attempt}"
    seconds = 300
    environment = {
        "HOME": "/work/cache/home",
        "NVIDIA_VISIBLE_DEVICES": "0",
        "NVIDIA_DRIVER_CAPABILITIES": "compute,utility",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
        "HF_HOME": "/work/cache/inference-hf",
        "TORCH_HOME": "/work/cache/torch",
        "TRITON_CACHE_DIR": "/work/cache/triton",
        "TORCHINDUCTOR_CACHE_DIR": "/work/cache/torchinductor",
        "XDG_CACHE_HOME": "/work/cache/xdg",
        "CUDA_CACHE_PATH": "/work/cache/cuda",
        "FASTVIDEO_ATTENTION_BACKEND": "VIDEO_SPARSE_ATTN_H3",
        "FASTVIDEO_VSA_SM100A": "0",
        "FASTVIDEO_FA4": "0",
        "FASTVIDEO_STAGE_LOGGING": "1",
        "FASTVIDEO_VSA_CUTEDSL": "0",
        "FASTVIDEO_MINIMAX_H3_FUSIONS": "all",
        "FASTVIDEO_INFERENCE_TORCH_COMPILE": "1",
        "FASTVIDEO_VAE_PARALLEL_DECODE": "1",
        "REPRO_OUTPUT_DIR": f"/work/outputs/{attempt}",
        "REPRO_MEMORY_PROFILE": memory_profile,
        "OMP_NUM_THREADS": "4",
        "OPENBLAS_NUM_THREADS": "4",
        "MKL_NUM_THREADS": "4",
        "TORCHINDUCTOR_COMPILE_THREADS": "2",
        "MAX_JOBS": "2",
        "PYTHONUNBUFFERED": "1",
    }
    args = [
        "docker",
        "create",
        "--name",
        name,
        "--label",
        f"local.project={PROJECT}",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--label",
        "local.resource-guard=required",
        "--label",
        f"local.memory-profile={memory_profile}",
        "--restart",
        "no",
        "--network",
        "none",
        "--gpus",
        "device=0",
        "--cpus",
        "8",
        "--memory",
        f"{limits['container_gib']}g",
        "--memory-swap",
        f"{limits['container_gib']}g",
        "--pids-limit",
        "512",
        "--shm-size",
        "2g",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,size=1g",
        "--tmpfs",
        "/root:rw,size=32m",
        "--security-opt",
        "no-new-privileges",
        "--cap-drop",
        "ALL",
        "--mount",
        f"type=bind,src={ROOT},dst=/work,readonly",
        "--mount",
        f"type=bind,src={ROOT}/cache,dst=/work/cache",
        "--mount",
        f"type=bind,src={ROOT}/outputs,dst=/work/outputs",
        "--mount",
        f"type=bind,src={ROOT}/evidence,dst=/work/evidence",
        "--mount",
        f"type=bind,src={ROOT}/guard,dst=/guard-code,readonly",
        "--mount",
        f"type=bind,src={ROOT}/status,dst=/guard-status,readonly",
        "--workdir",
        "/work/source",
        "--entrypoint",
        "python3",
    ]
    for key, value in environment.items():
        args += ["-e", f"{key}={value}"]
    child = ["nice", "-n", "19", "/work/runtime/venv/bin/python"]
    child += [
        "/work/scripts/observe_and_run.py",
        "--model-path",
        "/work/models/fasth3-v1",
        "--prompt",
        prompt,
        "--num-gpus",
        "1",
        "--execution-backend",
        "mp",
        "--height",
        "480",
        "--width",
        "832",
        "--num-frames",
        str(num_frames),
        "--steps",
        "5",
        "--seed",
        str(seed),
        "--vsa-kernel",
        "triton",
        "--no-fa4",
        "--vsa-sparsity",
        "0.9",
        "--vsa-tile-size",
        "64",
        "--lazy-module-load",
        "--video-decode-backend",
        "taeh3",
        "--taeh3-checkpoint",
        "/work/models/taeh3.safetensors",
        "--no-warmup",
        "--repeats",
        "1",
        "--output",
        f"/work/outputs/{attempt}",
    ]
    args += [
        image,
        "/guard-code/resource_entrypoint.py",
        "--status",
        f"/guard-status/{status.name}",
        "--",
        *child,
    ]
    identity = run(args)
    guard_command = [
        sys.executable,
        str(ROOT / "guard/resource_guard.py"),
        "--container-id",
        identity,
        "--execute",
        "--status",
        str(status),
        "--max-seconds",
        str(seconds),
        "--peak-budget-gib",
        str(limits["host_growth_gib"]),
        "--profile",
        memory_profile,
    ]
    with (ROOT / "logs" / f"{attempt}-guard.log").open("w") as log:
        guard = subprocess.Popen(
            guard_command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    record = {
        "attempt": attempt,
        "container_id": identity,
        "name": name,
        "created_time": time.time(),
        "num_frames": num_frames,
        "memory_profile": memory_profile,
        "resource_limits": limits,
        "guard_pid": guard.pid,
        "deadline_seconds": seconds,
        "container_command": args,
        "child_command": child,
        "guard_command": guard_command,
        "state": "guard_launch_requested",
    }
    (ROOT / "evidence" / f"{attempt}-launch.json").write_text(
        json.dumps(record, indent=2) + "\n"
    )
    print(json.dumps(record), flush=True)
    return record
