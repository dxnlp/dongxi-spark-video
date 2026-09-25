"""Start/stop a loopback portal through a project-specific SSH control socket."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shlex
import socket
import subprocess
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
URL = "http://127.0.0.1:8765"


def healthy():
    try:
        with urlopen(URL + "/api/state", timeout=2) as response:
            return json.load(response).get("app") == "spark-video"
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host", default="spark", help="Host alias from your SSH config"
    )
    parser.add_argument(
        "--remote-root",
        default="dongxi-spark-video",
        help="Checkout path on Spark; relative paths start in the SSH home directory",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop the remote portal/render and this checkout's tunnel",
    )
    args = parser.parse_args()
    if args.host.startswith("-") or any(c.isspace() for c in args.host):
        parser.error("Use an SSH host alias, not options or a shell command.")
    if args.remote_root.startswith("~"):
        parser.error(
            "Use an absolute remote path or a path relative to the SSH home directory, without ~."
        )
    state = ROOT / "status"
    state.mkdir(exist_ok=True)
    # Keep the UNIX socket path below the macOS length limit, even in deep repos.
    key = hashlib.sha256(f"{ROOT}:{args.host}:{args.remote_root}".encode()).hexdigest()[
        :16
    ]
    sock_dir = Path.home() / ".cache/dongxi-spark-video"
    sock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    control = str(sock_dir / f"{key}.sock")
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
    remote = PurePosixPath(args.remote_root)
    if args.stop:
        subprocess.run(
            [
                *ssh,
                args.host,
                shlex.join(["python3", str(remote / "scripts/manage.py"), "stop"]),
            ],
            check=True,
            timeout=60,
        )
        subprocess.run(
            [*ssh, "-S", control, "-O", "exit", args.host], check=False, timeout=10
        )
        print("Remote portal stopped; project tunnel closed.")
        return
    connection = subprocess.run(
        [*ssh, "-S", control, "-O", "check", args.host], capture_output=True, timeout=10
    )
    if connection.returncode == 0 and healthy():
        print(URL)
        return
    if connection.returncode != 0:
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", 8765))
            except OSError:
                raise RuntimeError(
                    "Local port 8765 is in use. Nothing was stopped or replaced."
                )
    subprocess.run(
        [
            *ssh,
            args.host,
            shlex.join(["python3", str(remote / "portal/start_remote.py")]),
        ],
        check=True,
        timeout=20,
    )
    if connection.returncode != 0:
        subprocess.run(
            [
                *ssh,
                "-M",
                "-S",
                control,
                "-fNT",
                "-o",
                "ExitOnForwardFailure=yes",
                "-o",
                "ServerAliveInterval=15",
                "-o",
                "ServerAliveCountMax=3",
                "-L",
                "127.0.0.1:8765:127.0.0.1:8765",
                args.host,
            ],
            check=True,
            timeout=15,
        )
    for _ in range(30):
        if healthy():
            print(URL)
            return
        time.sleep(0.2)
    raise RuntimeError(
        "Portal is not reachable; inspect the Spark portal log. No GPU job was started."
    )


if __name__ == "__main__":
    main()
