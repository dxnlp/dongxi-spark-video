#!/bin/bash
set -euo pipefail
export UV_CACHE_DIR=/work/cache/download-uv
export HF_HOME=/work/cache/download-hf
export HF_HUB_DISABLE_IMPLICIT_TOKEN=1
export HF_HUB_DISABLE_PROGRESS_BARS=1
export HF_XET_HIGH_PERFORMANCE=1
export HF_XET_CHUNK_CACHE_SIZE_BYTES=0
export HF_XET_NUM_CONCURRENT_RANGE_GETS=8
export PYTHONUNBUFFERED=1
UV=/opt/repro-tools/bin/uv
"$UV" venv /work/runtime/download --python /usr/bin/python3
"$UV" pip install --python /work/runtime/download/bin/python huggingface_hub==1.32.0 hf-xet==1.6.1a0
exec /work/runtime/download/bin/python /work/scripts/download.py
