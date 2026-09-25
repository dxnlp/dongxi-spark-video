#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends python3 python3-venv python3-dev git curl ca-certificates build-essential pkg-config ffmpeg libgl1 libglib2.0-0
python3 -m venv /opt/repro-tools
/opt/repro-tools/bin/pip install --no-cache-dir uv==0.10.12
/opt/repro-tools/bin/uv --version
ffmpeg -version | head -n 1
printf 'BOOTSTRAP_COMPLETE\n'
