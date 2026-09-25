"""Validate installed artifacts without importing modules that require a GPU."""

import importlib.metadata
import json
from pathlib import Path
import struct
import sys
import time
import torch
import transformers

root = Path("/work")
assert torch.version.cuda == "13.0"
assert torch.__version__.startswith("2.12.0")
assert not torch.cuda.is_initialized()
versions = {
    name: importlib.metadata.version(name)
    for name in (
        "fastvideo",
        "fastvideo-kernel",
        "torch",
        "triton",
        "transformers",
        "safetensors",
    )
}
assert versions["fastvideo-kernel"] == "0.3.5"
assert versions["triton"] == "3.7.0"
report = (root / "evidence/dependency-check.txt").read_text()
if "Found 1 incompatibility" in report:
    assert "nvidia-cusparselt-cu13" in report and "different platform" in report
    library = (
        root
        / "runtime/venv/lib/python3.12/site-packages/nvidia/cusparselt/lib/libcusparseLt.so.0"
    )
    with library.open("rb") as f:
        header = f.read(20)
    assert header[:4] == b"\x7fELF" and struct.unpack("<H", header[18:20])[0] == 183
else:
    assert "All installed packages are compatible" in report
state = {
    "state": "installed",
    "time": time.time(),
    "python": sys.version,
    "torch": torch.__version__,
    "cuda": torch.version.cuda,
    "transformers": transformers.__version__,
    "versions": versions,
    "gpu_validation": "pending",
    "validation": "CPU metadata, CUDA build and dependency/ARM64 checks passed; real FastVideo import and sparse kernel are tested in guarded GPU probe.",
}
(root / "status/install.json").write_text(json.dumps(state, indent=2) + "\n")
print(json.dumps(state), flush=True)
