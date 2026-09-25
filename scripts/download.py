"""Pinned FastH3 download with verified-file progress, not allocated-byte progress."""

import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import urllib.request

from huggingface_hub import HfApi, hf_hub_download

ROOT = Path("/work")
REPO = "FastVideo/FastVideo-FastH3-4-step-Preview-v1-VSA-DataFree"
REV = "5ea076f35b84da4c3c82217112fa733d8eea2ae1"
model = ROOT / "models/fasth3-v1"
model.mkdir(parents=True, exist_ok=True)
info = HfApi(token=False).model_info(REPO, revision=REV, files_metadata=True)
files = [
    {"path": f.rfilename, "size": f.size, "sha256": f.lfs.sha256 if f.lfs else None}
    for f in info.siblings
]
(ROOT / "evidence/model-manifest.json").write_text(
    json.dumps({"repo": REPO, "revision": REV, "files": files}, indent=2) + "\n"
)
total = sum(f["size"] for f in files)
completed = []
lock = threading.Lock()
started = time.time()


def record(state, **extra):
    data = {
        "state": state,
        "repo": REPO,
        "revision": REV,
        "expected_bytes": total,
        "verified_bytes": sum(f["size"] for f in completed),
        "verified_files": len(completed),
        "total_files": len(files),
        "elapsed_seconds": time.time() - started,
        "time": time.time(),
        **extra,
    }
    p = ROOT / "status/download.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(p)
    print(json.dumps(data), flush=True)


def fetch(entry):
    path = Path(
        hf_hub_download(REPO, entry["path"], revision=REV, local_dir=model, token=False)
    )
    assert path.stat().st_size == entry["size"], entry["path"]
    if entry["sha256"]:
        digest = hashlib.sha256()
        with path.open("rb") as f:
            while block := f.read(16 * 1024**2):
                digest.update(block)
            if hasattr(os, "posix_fadvise"):
                os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        assert digest.hexdigest() == entry["sha256"], entry["path"]
    with lock:
        completed.append(entry)
        record("downloading", last_verified_file=entry["path"])


record("downloading")
try:
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for _ in pool.map(fetch, files):
            pass
    url = "https://raw.githubusercontent.com/madebyollin/taehv/62f7591f59dfbb4c3c02b7a621d180a9eeaba26c/safetensors/taeh3.safetensors"
    target = ROOT / "models/taeh3.safetensors"
    with urllib.request.urlopen(url, timeout=60) as response:
        blob = response.read()
    assert (
        hashlib.sha256(blob).hexdigest()
        == "4fd022bfcab08772fe0536b17ea1a3bbb5625be11e397868d1c5d891863d4c13"
    )
    target.write_bytes(blob)
    record("complete", decoder_sha256_verified=True)
except Exception as exc:
    record("failed", error_type=type(exc).__name__)
    # Exception URLs can carry signed credentials, so do not print them.
    raise SystemExit(1)
