# Architecture

```text
Browser → 127.0.0.1:8765 → optional SSH tunnel → Spark loopback HTTP server
                                                    ↓ validated request
                                               detached worker
                                                    ↓ admission + lock
                                       isolated Docker model process
                                      ↗                              ↖
                           host memory watchdog             container lease supervisor
                                                    ↓ clean exit
                                       CPU full-decode / metadata validation
                                                    ↓
                                         local MP4 and portal history
```

The server uses only Python's standard library. It serves a static UI and accepts same-origin JSON requests. Host/Origin checks, CSP and strict media-path checks protect the local interface, but it has no authentication or multi-user authorization. Keep it on loopback; do not reverse-proxy it to a public address without designing those controls.

`portal/server.py` admits one request, maps its duration to a fixed memory profile and starts `portal/run_job.py`. A checkout-local filesystem lock prevents overlapping worker/setup activity. `scripts/launch_gpu.py` repeats the host admission checks and creates a stopped, labelled container. The independent host guard validates its immutable ID, ownership, limits and lease mounts before starting it.

`guard/resource_guard.py` samples host memory, swap, pressure and deadline. `guard/resource_entrypoint.py` refuses to run without a fresh guard record and kills its own child on lease loss. The worker handles cancellation and output validation. Neither a browser disconnection nor HTTP-server restart automatically retries a render.

`scripts/observe_and_run.py` wraps the pinned upstream example to enforce the CUDA allocator ceiling and four-forward recipe, emit telemetry and restrict decoding to TAEH3. Its wrapper remains the multiprocessing main module so spawned workers reinstall the controls. `checkpoint_cache.py` advises the OS to discard only completed project checkpoint-file cache, never live GPU tensors or global host caches.

Progress is an estimate weighted by actual events: encoder/shard loading, transformer loading, four diffusion steps, decoding, export and validation. Time alone never advances the percentage. Only a fully decoded/validated result reaches 100%. Tensor samples are event-driven; host memory is polled about once per second by the portal UI.

## Storage and isolation

Host-side paths are derived from the checkout directory. The container's `/work` layout is stable, including the upstream checkout and virtual environment. Inference sees no network and no host home directory. Keep secrets outside the repository, including ignored files: a Git ignore rule is not a mount boundary.

Setup/download containers have network access and bounded CPU/memory resources, and do not receive `--gpus`. Inference is a separate, short-lived container. FFmpeg metadata/full-decode checks run afterward in small CPU containers. No daemon installs a model in memory while idle.

The original working environment is deliberately separate from the packaged repository. This extraction does not move model files, change the old installation, or start its services. For a future migration, deploy this checkout and either perform setup or explicitly map/reuse the existing runtime after checking source/model revisions; avoid copying credentials or raw private logs into Git.
