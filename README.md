# Dongxi Spark Video

A local prompt-to-video portal for **one NVIDIA DGX Spark (GB10, 128 GB unified memory)**. Generate a short video with audio, follow stage-based progress, watch live memory use, and play or download the result in your browser.

Inference uses [FastVideo](https://github.com/hao-ai-lab/FastVideo) with the four-step [FastH3 Preview v1 VSA checkpoint](https://huggingface.co/FastVideo/FastVideo-FastH3-4-step-Preview-v1-VSA-DataFree). Everything runs on your Spark. No paid API or external inference service is required.

## What works

- Text prompt → 832 × 480 video at 24 fps with generated audio.
- **5.2, 6.5 and 15 seconds**, using measured resource profiles.
- Event-based progress, elapsed time, host-memory chart and CUDA tensor counters.
- One active render, cancellation, persistent local history, playback and download.
- Docker isolation, host-memory watchdog, container lease and a five-minute render deadline.
- Loopback HTTP server accessed locally or through your SSH connection.

This is a preview model. It can produce imperfect motion, anatomy, fine detail or audio. The portal does **not** support image/reference-video conditioning, motion transfer, exact identity control, custom soundtracks, arbitrary resolutions or arbitrary durations. See [limits and safety](docs/LIMITS.md).

## Measured performance

Single Spark, FastH3 four transformer forwards, 90% sparse Triton VSA, GPU-direct loading, lazy modules, **TAEH3 video decoder**, no warm-up request. These are observations from the original installation on 2026-09-25, not speed guarantees for every prompt or a fresh build.

| Portal length | Actual video | Render to MP4 | Profile | Peak host use | Minimum available |
|---|---:|---:|---|---:|---:|
| 5.2 s | 5.167 s | 139 s | standard | 81.31 GiB | 40.38 GiB |
| 6.5 s | 6.542 s | 157 s | standard | ≈82.24 GiB | 39.45 GiB |
| 15 s | 15.042 s | 264 s | capacity-84 | 90.91 GiB | 30.78 GiB |

Render time includes model startup/loading through MP4 export. The portal also performs CPU decoding/metadata validation and creates a poster before reporting 100%. Downloading dependencies/models and first-time compiler work are separate. The published upstream full-VAE benchmark is a different decoder recipe. [Benchmark details](docs/BENCHMARKS.md) explain the clocks, rounding and measurement scope.

## Requirements

**On Spark:** Linux ARM64, a working NVIDIA driver and NVIDIA Container Toolkit, Docker usable by your account, Python 3.10+, Git, and sufficient free SSD space. The measured stack used DGX OS OTA 7.5.0, driver 580.173.02, kernel 6.17.0-1031-nvidia, container CUDA 13.0, PyTorch 2.12.0+cu130 and Python 3.12.13. Other combinations have not been validated here.

Allow roughly **350 GiB of free disk for a fresh setup** (planning allowance, not an exact footprint). The pinned model files alone total about 138 GiB; dependency environments, image layers, download/build caches and outputs add more. Setup refuses stages with insufficient headroom.

**On a client laptop:** Python 3.10+ and OpenSSH. No CUDA, Python ML packages or Node installation is needed to use the portal. `ssh spark` should already work, or pass another SSH alias with `--host`.

The 15-second profile needs **116 GiB available and 80 GiB immediately free** at admission. The short profiles require 112 GiB available. All profiles refuse admission while any Docker container or GPU compute job is active. They do not stop another workload to make room.

## Setup on Spark

Place a clone of this repository at `~/dongxi-spark-video` on Spark, then run:

```bash
cd ~/dongxi-spark-video
python3 scripts/setup.py all
python3 scripts/manage.py status
```

Setup builds a project Docker image, installs dependencies in an isolated `/work/runtime/venv`, and downloads revision-pinned public weights with file-size and available SHA-256 checks. It **does not start the portal or inference**. It does not install host Python packages, change drivers, flush global caches, or install a startup service.

The stages can also be run explicitly:

```bash
python3 scripts/setup.py image
python3 scripts/setup.py install
python3 scripts/setup.py download
```

Network access is needed during setup. The public downloader does not read or pass your Hugging Face token. Never put credential files in this checkout: the runtime mounts the project directory. Setup errors stop the current attempt and preserve downloaded files; there is no automatic retry.

**Validation boundary:** the original runtime recipe produced the measured videos. The cleaned repository passes CPU checks; its new fresh-install orchestration has not been re-run from scratch, and no GPU generation was restarted during repository preparation. Dependency versions are constrained to the measured snapshot, not a complete hash lock. Start with the shortest preset when validating a new installation.

## Open the portal

From a local clone on your Mac or Linux client:

```bash
python3 portal/launch.py --host spark --remote-root dongxi-spark-video
```

Open **<http://127.0.0.1:8765/>**. Enter a prompt, choose a duration, optionally choose a seed, then select **Generate video**. A relative `--remote-root` is resolved from your SSH home directory. Use an absolute path if your checkout lives elsewhere.

If browsing directly on Spark, start only the local server:

```bash
python3 portal/start_remote.py
```

The idle server has no model loaded. Closing a browser tab does not cancel a render. **Stop render** cancels the current job. The independent watchdog continues working if the web server, client or SSH connection goes away.

## Stop and release resources

From the client, using the same host/path as launch:

```bash
python3 portal/launch.py --host spark --remote-root dongxi-spark-video --stop
```

Or directly on Spark:

```bash
python3 scripts/manage.py stop
python3 scripts/manage.py status
```

Shutdown stops this checkout's portal and cancels its active render, with ownership checks before stopping an orphaned render container. The client command also closes its own SSH control tunnel. Models, environments and outputs stay on disk for reuse; retaining them does not keep GPU tensors allocated. No recurring task or restart policy is installed.

## Layout

```text
portal/           HTTP server, worker, browser UI, client SSH launcher
guard/            Independent host watchdog and in-container lease supervisor
scripts/          Setup, runtime launch/checks, telemetry, shutdown and test runner
tests/            CPU-only tests with Docker/GPU calls mocked
docker/           CUDA base-image recipe
requirements/     Measured ARM64/CUDA dependency constraints
docs/             Limits, benchmarks, architecture and publishing checklist
```

Runtime directories (`source/`, `models/`, `runtime/`, `cache/`, `outputs/`, `portal/jobs/`, `logs/`, `status/`, `evidence/`) are generated locally and ignored by Git. `portal/jobs/` and logs can contain private prompts. No model, reference image, soundtrack or generated video is included.

## Development and verification

The web server and CPU tests use Python's standard library. These commands do not load a model or call Docker:

```bash
python3 scripts/test.py
python3 -m compileall -q portal scripts guard tests
bash -n scripts/bootstrap.sh scripts/install.sh scripts/download.sh
node --check portal/static/app.js  # optional local JS syntax check; also runs in CI
```

Tests cover admission, early resource stops, guard ownership/leases, profile dispatch, progress, cancellation, concurrent submissions, HTTP origin/Host checks, media seeking/validation, shutdown ownership and checkout portability. GitHub Actions runs the CPU checks only.

See [architecture](docs/ARCHITECTURE.md), [troubleshooting and limits](docs/LIMITS.md), [validation status](docs/VALIDATION.md), and [preparing a GitHub upload](docs/PUBLISHING.md).

## License and upstream work

The portal and orchestration code in this repository use the [MIT License](LICENSE). This does not license the downloaded model weights or change their usage terms. FastH3 inherits the MiniMax H3 Community License; FastVideo and TAEH3 have their own licenses. Review the linked terms before use or redistribution. See [third-party notices](THIRD_PARTY_NOTICES.md) for pinned sources and attribution.
