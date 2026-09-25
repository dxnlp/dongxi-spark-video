# Measured results

Recorded on 2026-09-25 on one DGX Spark. These figures are historical measurements from the working installation before this repository was extracted. Generated media was deleted at the user's request; private raw run logs and machine paths are not distributed here.

## Configuration

- FastVideo commit `8760eb7a0677cc2582ce4c990e2e1c3a121eb149`.
- FastH3 Preview v1 VSA DataFree checkpoint revision `5ea076f35b84da4c3c82217112fa733d8eea2ae1`.
- One GB10; 832×480 at 24 fps; four transformer forwards; VSA sparsity 0.9; Triton kernel; no FA4.
- Lazy module loading, GPU-direct transformer loading, TAEH3 video decoder, generated audio.
- No explicit warm-up render. Compiler/download cache state can affect first-run timing; “cold” here does not mean a brand-new machine with empty compiler caches.
- Project checkpoint page-cache advice only after each loaded shard has been consumed; no global cache flush. Model tensor computation stays upstream.

## Timings and memory

| Requested frames | Actual frames | Video duration | Render to MP4 | CUDA peak allocated / reserved | Peak whole-host use | Minimum available | Peak GPU temp |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 124 | 124 | 5.1667 s | 139.25 s | 70.11 / 70.87 GiB | 81.31 GiB | 40.38 GiB | Not included |
| 148 | 157 | 6.5417 s | 156.71 s | 71.18 / 71.86 GiB | ≈82.24 GiB | 39.45 GiB | Not included |
| 175 | 175 | 7.2917 s | 163.36 s | 71.72 / 72.82 GiB | 83.24 GiB | 38.45 GiB | 75°C |
| 243 | 243 | 10.125 s | 191.30 s | 74.12 / 75.76 GiB | 86.21 GiB | 35.48 GiB | 78°C |
| 362 | 361 | 15.0417 s | 263.88 s | 78.44 / 80.34 GiB | 90.91 GiB | 30.78 GiB | 83°C |

Host use is total reported memory minus minimum available; the 6.5-second figure uses the rounded 121.69 GiB total.

The three capacity runs used 76, 80 and 84 GiB CUDA caps respectively. The portal exposes only the established 5.2-, 6.5- and 15-second options. Increasing the earlier 72 GiB operational cap enabled longer clips; it did not discover the machine's physical exhaustion threshold.

The 5.2-second benchmark took 127.95 seconds on the narrower generation-request clock, versus 139.25 seconds from process start through exported MP4. A separate browser-submitted 5.2-second scene took 132.58 seconds through export and 137.37 seconds including validation/poster creation. Avoid comparing unlike clocks.

For the 7.29-, 10.125- and 15.04-second capacity tests, worker-through-validation times were 169.36, 198.04 and 268.61 seconds. The largest trial's host growth was 87.27 GiB. CUDA counters overlap with host use and must not be added to it.

All three capacity files passed full decoding and frame/A/V checks. Sampled frames showed coherent landscape motion; the 15-second file played to completion. Recorded PSI maxima were zero, no new swap or matching OOM/Xid/NV_ERR events were found, and containers exited cleanly. Host available memory recovered to about 118.1 GiB afterward.

## Interpretation

The upstream [FastH3 announcement](https://haoailab.com/blogs/fasth3-preview/) and its single-Spark performance figures motivated this work. The measured local recipe uses TAEH3. It does not reproduce the announcement's full-VAE setting exactly and should not be presented as an identical benchmark.

These are a few observed runs, not a statistical performance study or assurance of quality across prompts. There is no proven safe maximum at higher resolutions, with a full video VAE, or with concurrent workloads. The cleaned setup and shutdown orchestration has CPU regression coverage; it was packaged while generation was stopped and has not received another end-to-end GPU run.
