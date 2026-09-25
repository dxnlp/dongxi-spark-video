"""Loopback-only Spark Video portal, using Python's standard library."""

import argparse
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

from common import (
    ROOT,
    JOBS,
    ACTIVE,
    ID_RE,
    read_json,
    write_json,
    job_path,
    validate_request,
    memory,
    job_view,
    media_path,
    byte_range,
)
from common import duration_presets, request_preset, profile

HERE = Path(__file__).resolve().parent


class BusyError(Exception):
    pass


class Store:
    def __init__(self):
        self.lock = threading.Lock()
        JOBS.mkdir(parents=True, exist_ok=True)

    def jobs(self):
        records = [read_json(p) for p in JOBS.glob("*.json") if ID_RE.fullmatch(p.stem)]
        return sorted(
            (x for x in records if x), key=lambda x: x["created_at"], reverse=True
        )

    def active(self):
        return next((j for j in self.jobs() if j["state"] in ACTIVE), None)

    def submit(self, data):
        prompt, seed = validate_request(data)
        preset = request_preset(data)
        frames = preset["frames"]
        limits = profile(preset["memory_profile"])
        admission = max(112, limits["host_growth_gib"] + limits["reserve_gib"])
        with self.lock:
            if self.active():
                raise BusyError(
                    "A render is already running. Stop it or wait until it finishes."
                )
            mem = memory()
            if mem["available"] < admission * 1024**3 or mem["free"] < 80 * 1024**3:
                raise BusyError(
                    f"This length needs {admission} GiB available on Spark before rendering. No job was started."
                )
            # Protect against any active job outside this portal before accepting.
            for command in (
                ["docker", "ps", "-q"],
                ["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader"],
            ):
                if subprocess.check_output(command, text=True, timeout=8).strip():
                    raise BusyError(
                        "Spark is running another workload. No job was started."
                    )
            job_id = (
                time.strftime("portal-%Y%m%d-%H%M%S", time.gmtime())
                + "-"
                + secrets.token_hex(4)
            )
            job = {
                "id": job_id,
                "state": "starting",
                "created_at": time.time(),
                "prompt": prompt,
                "seed": secrets.randbelow(2147483648) if seed is None else seed,
                "frames": frames,
                "duration": preset["seconds"],
                "memory_profile": preset["memory_profile"],
            }
            write_json(job_path(job_id), job)
            try:
                with (ROOT / "logs" / f"{job_id}-portal.log").open("a") as log:
                    process = subprocess.Popen(
                        [sys.executable, str(HERE / "run_job.py"), job_id],
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                write_json(JOBS / f"{job_id}.runner.json", {"pid": process.pid})
                # Reap completed children without tying them to the web request.
                threading.Thread(target=process.wait, daemon=True).start()
            except Exception:
                job.update(
                    state="failed",
                    ended_at=time.time(),
                    error="Could not start the renderer",
                )
                write_json(job_path(job_id), job)
                raise
            return job_view(job)

    def cancel(self, job_id):
        with self.lock:
            job = read_json(job_path(job_id))
            if not job:
                raise FileNotFoundError("Render not found")
            if job["state"] in ACTIVE:
                (JOBS / f"{job_id}.cancel").touch()
            return job_view(job)

    def recover(self):
        # A server restart must neither duplicate nor terminate an existing job.
        for job in self.jobs():
            if job["state"] not in ACTIVE:
                continue
            runner = read_json(JOBS / f"{job['id']}.runner.json", {})
            try:
                command = (
                    Path(f"/proc/{runner['pid']}/cmdline").read_bytes().split(b"\0")
                )
                if (
                    str(HERE / "run_job.py").encode() in command
                    and job["id"].encode() in command
                ):
                    continue
            except (OSError, KeyError):
                pass
            job.update(
                state="failed",
                ended_at=time.time(),
                error="The render worker stopped. Its independent memory guard remains responsible for GPU cleanup.",
            )
            write_json(job_path(job["id"]), job)

    def state(self):
        jobs = self.jobs()[:20]
        return {
            "app": "spark-video",
            "memory": memory(),
            "jobs": [job_view(j) for j in jobs],
            "active_id": next((j["id"] for j in jobs if j["state"] in ACTIVE), None),
            "server_time": time.time(),
            "settings": {
                "duration": 124 / 24,
                "width": 832,
                "height": 480,
                "steps": 4,
                "expected_seconds": 139,
                "deadline_seconds": 300,
                "max_prompt": 1200,
                "duration_presets": duration_presets(),
            },
        }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SparkVideo"

    def log_message(self, format, *args):
        if "/api/state" not in str(args):
            super().log_message(format, *args)

    def send_headers(self, code, kind, length):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        if code >= 400:
            self.close_connection = True
            self.send_header("Connection", "close")
        self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'self'",
        )

    def json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_headers(code, "application/json; charset=utf-8", len(body))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def host_ok(self):
        return self.headers_in_host() in {
            f"127.0.0.1:{self.server.server_port}",
            f"localhost:{self.server.server_port}",
        }

    def headers_in_host(self):
        return self.headers.get("Host", "")

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            if not self.host_ok():
                self.json(403, {"error": "Loopback access only"})
                return
            path = urlsplit(self.path).path
            if path == "/api/state":
                self.json(200, self.server.store.state())
                return
            if path.startswith("/media/"):
                parts = path.split("/")
                if len(parts) != 4:
                    raise FileNotFoundError()
                media = media_path(parts[2], parts[3])
                size = media.stat().st_size
                try:
                    start, end, partial = byte_range(self.headers.get("Range"), size)
                except ValueError:
                    self.send_headers(416, "text/plain", 0)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                self.send_headers(
                    206 if partial else 200,
                    mimetypes.guess_type(media.name)[0] or "application/octet-stream",
                    end - start + 1,
                )
                self.send_header("Accept-Ranges", "bytes")
                if partial:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                if urlsplit(self.path).query == "download=1":
                    self.send_header(
                        "Content-Disposition",
                        f'attachment; filename="spark-video-{parts[2]}.mp4"',
                    )
                self.end_headers()
                if self.command != "HEAD":
                    with media.open("rb") as stream:
                        stream.seek(start)
                        remaining = end - start + 1
                        while remaining:
                            block = stream.read(min(256 * 1024, remaining))
                            if not block:
                                break
                            self.wfile.write(block)
                            remaining -= len(block)
                return
            static = {
                "/": "index.html",
                "/app.js": "app.js",
                "/style.css": "style.css",
                "/favicon.ico": None,
            }
            if path not in static or static[path] is None:
                raise FileNotFoundError()
            asset = HERE / "static" / static[path]
            body = asset.read_bytes()
            self.send_headers(
                200,
                (mimetypes.guess_type(asset.name)[0] or "text/plain")
                + "; charset=utf-8",
                len(body),
            )
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (FileNotFoundError, ValueError):
            self.json(404, {"error": "Not found"})
        except Exception as exc:
            print("GET error:", repr(exc), flush=True)
            self.json(500, {"error": "Could not read Spark status. Please retry."})

    def do_POST(self):
        try:
            host = self.headers_in_host()
            if not self.host_ok() or self.headers.get("Origin") != f"http://{host}":
                self.json(403, {"error": "Open the local portal to control renders."})
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self.json(415, {"error": "JSON required"})
                return
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 8192:
                self.json(413, {"error": "Request is too large"})
                return
            data = json.loads(self.rfile.read(size))
            path = urlsplit(self.path).path
            if path == "/api/jobs":
                self.json(202, self.server.store.submit(data))
                return
            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                parts = path.split("/")
                if len(parts) == 5:
                    self.json(200, self.server.store.cancel(parts[3]))
                    return
            self.json(404, {"error": "Not found"})
        except BusyError as exc:
            self.json(409, {"error": str(exc)})
        except (ValueError, TypeError) as exc:
            self.json(400, {"error": str(exc)})
        except FileNotFoundError:
            self.json(404, {"error": "Render not found"})
        except Exception as exc:
            print("POST error:", repr(exc), flush=True)
            self.json(
                500,
                {"error": "Could not start the render. No automatic retry was made."},
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    for directory in ("logs", "status", "evidence", "outputs", "cache", "portal/jobs"):
        (ROOT / directory).mkdir(parents=True, exist_ok=True)
    lock = (ROOT / "status/portal-server.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    store = Store()
    store.recover()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    server.store = store
    write_json(
        ROOT / "status/portal-server.json",
        {"pid": os.getpid(), "port": args.port, "started_at": time.time()},
    )
    print(f"Spark Video listening on 127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
