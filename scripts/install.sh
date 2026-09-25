#!/bin/bash
# Runs only inside the project CPU setup container, never on the host.
set -euo pipefail
test -f /.dockerenv
export HOME=/work/cache/setup-home
export UV_CACHE_DIR=/work/cache/uv
export UV_PYTHON_INSTALL_DIR=/work/runtime/python
export UV_TORCH_BACKEND=cu130
export TORCH_CUDA_ARCH_LIST=12.1
export MAX_JOBS=2 CMAKE_BUILD_PARALLEL_LEVEL=2 MAKEFLAGS=-j2
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2
export UV_CONCURRENT_BUILDS=1 UV_CONCURRENT_DOWNLOADS=4 UV_CONCURRENT_INSTALLS=2
export HF_HOME=/work/cache/huggingface HF_HUB_DISABLE_IMPLICIT_TOKEN=1
export PYTHONUNBUFFERED=1
mkdir -p "$HOME" /work/source /work/runtime /work/evidence /work/status
UV=/opt/repro-tools/bin/uv
REV=8760eb7a0677cc2582ce4c990e2e1c3a121eb149
if [ ! -d /work/source/.git ]; then
  git init -q /work/source
  git -C /work/source fetch --depth=1 https://github.com/hao-ai-lab/FastVideo.git "$REV"
  git -C /work/source checkout --detach FETCH_HEAD
fi
test "$(git -C /work/source rev-parse HEAD)" = "$REV"
test -z "$(git -C /work/source status --porcelain --untracked-files=no)"
cd /work/source
python3 - <<'PY'
from pathlib import Path
import subprocess
for name, url, revision in [
    ('cutlass', 'https://github.com/NVIDIA/cutlass.git', 'e67e63c331d6e4b729047c95cf6b92c8454cba89'),
    ('tk', 'https://github.com/HazyResearch/ThunderKittens.git', '6c27e28c8115d1839d9eeeb530913c184a75fc87'),
]:
    path = Path('fastvideo-kernel/include') / name
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git', 'init', '-q', str(path)], check=True)
    subprocess.run(['git', '-C', str(path), 'fetch', '--depth=1', url, revision], check=True, timeout=600)
    subprocess.run(['git', '-C', str(path), 'checkout', '--detach', 'FETCH_HEAD'], check=True)
    assert subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip() == revision
PY
if [ ! -x /work/runtime/venv/bin/python ]; then
  "$UV" venv /work/runtime/venv --python 3.12 --python-preference only-managed --seed
fi
"$UV" pip install --python /work/runtime/venv/bin/python --prerelease allow \
  --constraint /work/requirements/spark-cu130.txt -e .
"$UV" pip freeze --python /work/runtime/venv/bin/python > /work/evidence/requirements-installed.txt
"$UV" pip check --python /work/runtime/venv/bin/python > /work/evidence/dependency-check.txt 2>&1 || true
/work/runtime/venv/bin/python /work/scripts/verify_cpu_install.py
