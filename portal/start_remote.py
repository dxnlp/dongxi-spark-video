"""Start the loopback portal if it is not already healthy. No GPU work."""

import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
URL = "http://127.0.0.1:8765/api/state"


def healthy():
    try:
        with urlopen(URL, timeout=2) as response:
            return json.load(response).get("app") == "spark-video"
    except Exception:
        return False


for directory in ("logs", "status", "evidence", "outputs", "cache", "portal/jobs"):
    (ROOT / directory).mkdir(parents=True, exist_ok=True)
if healthy():
    print("Spark Video is already running.")
    raise SystemExit(0)
with (ROOT / "logs/portal-server.log").open("a") as log:
    process = subprocess.Popen(
        [sys.executable, str(HERE / "server.py"), "--port", "8765"],
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
for _ in range(30):
    if healthy():
        print("Spark Video started on 127.0.0.1:8765.")
        break
    if process.poll() is not None:
        raise RuntimeError(
            "Portal could not start; inspect logs/portal-server.log. No other process was stopped."
        )
    time.sleep(0.2)
else:
    raise RuntimeError("Portal did not become ready. Inspect its log.")
