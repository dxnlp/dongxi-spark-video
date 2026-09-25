"""Use upstream model computation with bounded allocation and checkpoint cache advice."""

import functools
import importlib.util
import json
import os
from pathlib import Path
import time

PROCESS_STARTED = time.monotonic()
OUTPUT = Path(os.environ["REPRO_OUTPUT_DIR"])
OUTPUT.mkdir(parents=True, exist_ok=True)


def event(kind, **data):
    row = {
        "event": kind,
        "pid": os.getpid(),
        "wall_time": time.time(),
        "process_elapsed_seconds": time.monotonic() - PROCESS_STARTED,
        **data,
    }
    with (OUTPUT / f"observer-{os.getpid()}.jsonl").open("a") as f:
        f.write(json.dumps(row) + "\n")
    print("REPRO_OBSERVER " + json.dumps(row), flush=True)


event("python_started")


def install_observer():
    import torch
    from fastvideo import VideoGenerator
    from fastvideo.worker.gpu_worker import Worker
    from fastvideo.models.dits.minimax_h3 import MiniMaxH3Transformer3DModel
    from fastvideo.models.loader.component_loader import PipelineComponentLoader
    from fastvideo.pipelines.lazy_module import LazyModule
    from checkpoint_cache import install_checkpoint_cache_policy
    from memory_diagnostics import memory_snapshot

    install_checkpoint_cache_policy(event)
    original_release = LazyModule.release

    @functools.wraps(original_release)
    def observed_release(self):
        result = original_release(self)
        event("component_released", **memory_snapshot(torch))
        return result

    LazyModule.release = observed_release
    original_init = Worker.init_device

    @functools.wraps(original_init)
    def guarded_init(self):
        assert self.fastvideo_args.num_gpus == 1 and self.local_rank == 0
        assert self.fastvideo_args.lazy_module_load is True
        assert self.fastvideo_args.video_decode_backend == "taeh3"
        assert "VIDEO_SPARSE_ATTN_H3" in str(self.fastvideo_args.attention_backend)
        total = torch.cuda.get_device_properties(0).total_memory
        from resource_profiles import profile

        cap = profile(os.environ.get("REPRO_MEMORY_PROFILE", "standard"))["cuda_gib"]
        fraction = cap * 1024**3 / total
        assert 0 < fraction < 1
        torch.cuda.set_per_process_memory_fraction(fraction, device=0)
        event("allocator_cap", bytes=cap * 1024**3, total_bytes=total)
        result = original_init(self)
        assert abs(torch.cuda.get_per_process_memory_fraction(0) - fraction) < 1e-8
        event("worker_initialized")
        return result

    Worker.init_device = guarded_init
    original_load = PipelineComponentLoader.load_module

    @functools.wraps(original_load)
    def observed_load(module_name, *args, **kwargs):
        if module_name == "vae":
            raise RuntimeError(
                "Full H3 video VAE is outside the TAEH3 benchmark recipe"
            )
        event("component_load_start", component=module_name)
        started = time.monotonic()
        result = original_load(module_name, *args, **kwargs)
        event(
            "component_load_complete",
            component=module_name,
            seconds=time.monotonic() - started,
            **(memory_snapshot(torch) if torch.cuda.is_initialized() else {}),
        )
        if module_name == "transformer":
            event("transformer_loaded_memory", **memory_snapshot(torch))
        return result

    PipelineComponentLoader.load_module = staticmethod(observed_load)
    original_generate = VideoGenerator.generate

    @functools.wraps(original_generate)
    def observed_generate(self, *args, **kwargs):
        event("generation_request_start")
        started = time.monotonic()
        result = original_generate(self, *args, **kwargs)
        event(
            "generation_request_complete",
            seconds=time.monotonic() - started,
            video_path=str(getattr(result, "video_path", None)),
        )
        return result

    VideoGenerator.generate = observed_generate
    original_forward = MiniMaxH3Transformer3DModel.forward
    counters = {"calls": 0}

    @functools.wraps(original_forward)
    def observed_forward(self, *args, **kwargs):
        counters["calls"] += 1
        count = counters["calls"]
        if count > 4:
            raise RuntimeError("Refusing more than the four trained FastH3 forwards")
        if count == 1:
            backends = [
                type(block.attn.distributed_attention).__name__
                for block in self.transformer_blocks
            ]
            assert backends and set(backends) == {"DistributedAttention_VSA"}, backends
            event(
                "video_attention_verified",
                backend="DistributedAttention_VSA",
                blocks=len(backends),
            )
            # Lightweight stage telemetry for the local portal. No extra CUDA
            # synchronization or model/activation copies are introduced.
            for index, block in enumerate(self.transformer_blocks):

                def block_done(module, args, result, block_number=index + 1):
                    event(
                        "block_complete",
                        number=counters["calls"],
                        block=block_number,
                        blocks=len(backends),
                        **memory_snapshot(torch),
                    )

                block.register_forward_hook(block_done)
            # Observe only the first block and initial projections. No tensor
            # copies, synchronization, math changes, or retained activations.
            for name, module in [
                ("proj_in", self.proj_in),
                ("token_refiner", self.token_refiner),
                ("block0", self.transformer_blocks[0]),
                ("block0.attn", self.transformer_blocks[0].attn),
                ("block0.ff", self.transformer_blocks[0].ff),
            ]:

                def before_hook(module, args, label=name):
                    if counters["calls"] == 1:
                        event(
                            "allocation_boundary",
                            module=label,
                            phase="before",
                            **memory_snapshot(torch),
                        )

                def after_hook(module, args, result, label=name):
                    if counters["calls"] == 1:
                        event(
                            "allocation_boundary",
                            module=label,
                            phase="after",
                            **memory_snapshot(torch),
                        )

                module.register_forward_pre_hook(before_hook)
                module.register_forward_hook(after_hook)
        event("forward_start", number=count, **memory_snapshot(torch))
        started = time.monotonic()
        result = original_forward(self, *args, **kwargs)
        torch.cuda.synchronize()
        event(
            "forward_complete",
            number=count,
            seconds=time.monotonic() - started,
            peak_allocated_bytes=torch.cuda.max_memory_allocated(),
            peak_reserved_bytes=torch.cuda.max_memory_reserved(),
        )
        return result

    MiniMaxH3Transformer3DModel.forward = observed_forward


install_observer()

if __name__ == "__main__":
    event("upstream_example_start")
    # Keep this wrapper as multiprocessing's main module. runpy.run_path(...,
    # run_name='__main__') would make spawned workers replay the upstream
    # example instead, losing the allocator guard and forward observations.
    example = "/work/source/examples/inference/basic/basic_fasth3.py"
    try:
        spec = importlib.util.spec_from_file_location("repro_upstream_example", example)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.main()
    except BaseException as exc:
        event("failed", error_type=type(exc).__name__)
        raise
    else:
        event("upstream_example_complete")
