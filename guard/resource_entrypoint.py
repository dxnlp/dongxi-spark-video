"""Container-local supervisor. Starts work only with a fresh host guard lease.

This is an additional fail-closed check, not a guarantee against GPU/host failure.
The child command is isolated in its own process group. No host processes are
enumerated or signalled.
"""

import argparse
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import time


def lease_valid(path, hostname, now):
    try:
        data = json.loads(path.read_text())
        identity = data["container_id"]
        age = now - data["monotonic"]
        return (
            data["state"] == "watching"
            and re.fullmatch(r"[0-9a-f]{64}", identity) is not None
            and hostname == identity[:12]
            and 0 <= age < 3
        )
    except (OSError, ValueError, TypeError, KeyError):
        return False


def stop_child(child):
    if child.poll() is not None:
        return
    try:
        os.killpg(child.pid, signal.SIGTERM)
        child.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=1)
    except ProcessLookupError:
        pass


def supervise(path, command):
    hostname = socket.gethostname()
    if not re.fullmatch(r"[0-9a-f]{12}", hostname):
        raise RuntimeError("Use the default Docker container hostname")
    deadline = time.monotonic() + 20
    while not lease_valid(path, hostname, time.monotonic()):
        if time.monotonic() >= deadline:
            raise RuntimeError("No fresh host guard lease; refusing to start work")
        time.sleep(0.1)
    child = None
    try:
        child = subprocess.Popen(command, start_new_session=True)
        while child.poll() is None:
            if not lease_valid(path, hostname, time.monotonic()):
                raise RuntimeError("Host guard lease expired or withdrawn")
            time.sleep(0.1)
        return child.returncode
    finally:
        if child is not None:
            # A simultaneous host Docker stop must not interrupt our own child
            # cleanup after the lease has already been withdrawn.
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            stop_child(child)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("A child command is required")

    def interrupted(signum, frame):
        raise RuntimeError(f"Container supervisor received signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    raise SystemExit(supervise(args.status, command))


if __name__ == "__main__":
    main()
