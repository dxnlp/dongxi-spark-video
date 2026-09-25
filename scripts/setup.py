"""Prepare isolated CPU dependencies and pinned weights. Never starts inference."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parent.parent
IMAGE = "dongxi-spark-video:cu130"


def prepare():
    if platform.system() != "Linux" or platform.machine() != "aarch64":
        raise RuntimeError("Run setup on the ARM64 DGX Spark, not the client laptop.")
    for directory in (
        "logs",
        "status",
        "evidence",
        "cache",
        "runtime",
        "models",
        "outputs",
        "portal/jobs",
    ):
        (ROOT / directory).mkdir(parents=True, exist_ok=True)
    for command in (
        ["docker", "ps", "-q"],
        ["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader"],
    ):
        if subprocess.check_output(command, text=True, timeout=10).strip():
            raise RuntimeError("Other work is running. Setup will not interrupt it.")


def cpu_stage(stage, seconds):
    name = f"dongxi-spark-video-setup-{stage}-{time.time_ns()}"
    # CPU-only, same UID, separate cache/venv; host credentials never mounted.
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--label",
        "local.project=dongxi-spark-video",
        "--restart",
        "no",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--cpus",
        "2",
        "--memory",
        "12g",
        "--memory-swap",
        "12g",
        "--pids-limit",
        "512",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "-e",
        "NVIDIA_VISIBLE_DEVICES=void",
        "-e",
        "HF_HUB_DISABLE_IMPLICIT_TOKEN=1",
        "-e",
        "HOME=/work/cache/setup-home",
        "--mount",
        f"type=bind,src={ROOT},dst=/work",
        "--entrypoint",
        "timeout",
        IMAGE,
        str(seconds),
        "nice",
        "-n",
        "19",
        "bash",
        f"/work/scripts/{stage}.sh",
    ]
    try:
        subprocess.run(command, check=True, timeout=seconds + 30)
    except BaseException:
        # This unique name was just created by this invocation; no broad cleanup.
        subprocess.run(["docker", "stop", "--time", "2", name], timeout=10, check=False)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("image", "install", "download", "all"))
    args = parser.parse_args()
    prepare()
    lock = (ROOT / "status/coordinator.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    stages = ("image", "install", "download") if args.stage == "all" else (args.stage,)
    for stage in stages:
        if stage == "image":
            subprocess.run(
                [
                    "docker",
                    "build",
                    "--tag",
                    IMAGE,
                    "--file",
                    str(ROOT / "docker/Dockerfile"),
                    str(ROOT),
                ],
                check=True,
                timeout=3600,
            )
            image = subprocess.check_output(
                ["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"], text=True
            ).strip()
            (ROOT / "evidence/base-image.json").write_text(
                json.dumps({"prepared_image": image, "time": time.time()}, indent=2)
                + "\n"
            )
        else:
            if (
                shutil.disk_usage(ROOT).free
                < (220 if stage == "download" else 40) * 1024**3
            ):
                raise RuntimeError(
                    "Insufficient setup disk headroom (220 GiB for download; 40 GiB for install). Existing files preserved."
                )
            cpu_stage(stage, 21600 if stage == "download" else 10800)
    print("Requested setup stages completed. No portal or GPU job was started.")


if __name__ == "__main__":
    main()
