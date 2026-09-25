# Limits, resource protection and troubleshooting

## Supported scope

One NVIDIA DGX Spark GB10, 128 GB marketed unified memory (121.69 GiB reported by Linux on the measured host). CPU and GPU share that DRAM. `MemTotal - MemAvailable` estimates whole-host use; CUDA allocated/reserved counters describe overlapping GPU allocations. **Do not add them together.**

This portal exposes text-to-audio-video at 832×480, 24 fps, four FastH3 transformer forwards. The CLI argument `--steps 5` denotes five sigma points, producing four forwards. It uses the tiny TAEH3 video decoder rather than the full video VAE. The audio VAE remains part of the model pipeline.

The supported requested-frame buckets are 124, 148 and 362. The pinned model uses a causal grid of `17*n + 5`; 148 requests align to 158 frames. Upstream muxing can drop one terminal frame to match audio, so validation accepts only the aligned count or one fewer, with A/V timing within one frame. The measured 6.5-second and 15-second files contain 157 and 361 frames.

Fifteen seconds is the largest **tested and exposed preset**, not a proven physical hardware limit. Higher resolution, full video VAE, image/reference conditioning, concurrent models and longer clips are outside the measured envelope. The preview checkpoint's official model card says FL2VA and Ref2VA were not distilled. This portal therefore cannot reproduce reference choreography or guarantee a person's likeness.

## Current safeguards

| Guard | 5.2 / 6.5 seconds | 15 seconds |
|---|---:|---:|
| Available memory required at admission | 112 GiB | 116 GiB |
| Immediately free memory required | 80 GiB | 80 GiB |
| PyTorch CUDA allocation ceiling | 72 GiB | 84 GiB |
| Host-memory growth budget | 80 GiB | 92 GiB |
| Container memory / memory-plus-swap | 80 / 80 GiB | 92 / 92 GiB |
| Absolute available-memory reserve | 32 GiB | 24 GiB |
| Immediately free page reserve | 8 GiB | 8 GiB |
| Maximum GPU job runtime | 300 seconds | 300 seconds |

The server determines the profile from the selected duration. A client cannot choose an arbitrary cap. Intermediate capacity-test profiles remain in the guard's tested definitions but are not portal options.

- All jobs refuse admission while another Docker container or GPU compute process is active; an external process can still start later, so the host guard monitors continuously.
- The host watchdog samples memory/pressure every 250 ms. A scheduling gap over two seconds, unreadable telemetry, budget breach, reserve breach, PSI `full avg10 >= 1%` or `some >= 5%`, or 256 MiB new swap consumption aborts the owned job.
- The in-container supervisor requires a fresh matching guard lease. It terminates its child if the host guard stops updating.
- The long profile also requires GPU temperature below 70°C at admission. An independent monitor requests cancellation at 85°C, matching new kernel/GPU faults, or failed telemetry. The worker treats unexpected monitor exit as a failure.
- Inference has no network, no host home/credential mount, no extra container swap, dropped capabilities and a read-only project base mount; cache, outputs and evidence have specific writable mounts.
- One job at a time; no queue, automatic retry, automatic cap increases or automatic host reboot.

Docker limits and the PyTorch allocator cap do not account for every possible driver allocation on a unified-memory machine. These layers reduce risk; they cannot guarantee responsiveness during every driver, thermal or hardware failure. Do not bypass guards to obtain a longer render.

## OOM and host recovery

The original 72 GiB allocator refusal exited normally and left SSH responsive. Later tests succeeded up to the 15-second profile with 30.78 GiB available at the measured peak. Neither result deliberately exhausted physical memory or tested recovery from every driver failure.

An ordinary allocator exception does not itself require a hard reset. A system/driver hang is a different outcome. Check [NVIDIA release notes](https://docs.nvidia.com/dgx/dgx-spark/release-notes.html) and [known issues](https://docs.nvidia.com/dgx/dgx-spark/known-issues.html) for the installed OS/driver; do not assume protections from a different version apply. This repository never updates drivers, changes swap/networking or reboots the host.

If SSH disappears during a run, avoid retries. Preserve the bounded logs and inspect host memory, kernel OOM/Xid/NV_ERR events and the named container after access returns. Diagnose before any new GPU attempt.

## Common failures

| Symptom | What to check |
|---|---|
| “Needs 112/116 GiB available” | Finish other work and inspect `/proc/meminfo`. The portal will not kill another project. Do not flush global caches automatically. |
| “Another workload” | `docker ps` and `nvidia-smi`. Even CPU containers block this conservative admission policy. |
| Long profile refuses telemetry | Your account needs readable kernel journal entries and `nvidia-smi` temperature support. No permission change is made automatically. |
| CUDA allocation cap reached | Read `logs/<job-id>.log`; try a shorter preset after confirming normal exit and recovered memory. |
| Five-minute deadline | Cold compiler caches or load can push a render over the deadline. It stops without retry; 264 seconds is a measurement, not a promise. |
| Port 8765 busy | Check the existing listener/tunnel. The launcher refuses to replace an unrelated listener. |
| SSH fails | Restore your existing SSH alias/keys. The portal does not manage credentials. |
| Setup dependency check reports cuSPARSELt platform mismatch | The measured ARM64 wheel had a nonstandard platform tag. The validator accepts only that single known report and verifies the actual ELF machine is AArch64. Other dependency failures stop setup. |
| Blank/noisy or malformed output | Full decoding only proves media validity. Visual quality must be reviewed by the user; successful validation is not proof of motion/identity quality. |

Keep raw diagnostics local: they may contain prompts and machine paths. The Git ignore rules exclude them.
