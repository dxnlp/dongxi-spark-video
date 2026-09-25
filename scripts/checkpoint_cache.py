"""Release only this project's completed checkpoint-file cache; preserve tensors."""

import functools
import os
from pathlib import Path
import sys
import time

MODEL_ROOT = Path("/work/models/fasth3-v1")


def discard_checkpoint_cache(filename, root=MODEL_ROOT):
    path = Path(filename).resolve(strict=True)
    if (
        not path.is_relative_to(root.resolve(strict=True))
        or path.suffix != ".safetensors"
    ):
        raise ValueError("Cache advice is restricted to project checkpoint files")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        after = os.fstat(fd)
        assert (before.st_ino, before.st_size, before.st_mtime_ns) == (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        return before.st_size
    finally:
        os.close(fd)


def install_checkpoint_cache_policy(event):
    import torch
    from fastvideo.models.loader import weight_utils

    original = weight_utils.safetensors_weights_iterator

    @functools.wraps(original)
    def iterator(hf_weights_files, to_cpu=False, broadcast=True, async_broadcast=False):
        # Each upstream subiterator closes its shard before we advise the kernel.
        # Order, tensors, dtype and weight assignment remain upstream behavior.
        for filename in hf_weights_files:
            yield from original(
                [filename],
                to_cpu=to_cpu,
                broadcast=broadcast,
                async_broadcast=async_broadcast,
            )
            if not to_cpu:
                started = time.monotonic()
                torch.cuda.synchronize()
                size = discard_checkpoint_cache(filename)
                event(
                    "checkpoint_cache_released",
                    file=str(Path(filename).relative_to(MODEL_ROOT)),
                    file_bytes=size,
                    seconds=time.monotonic() - started,
                    allocated_bytes=torch.cuda.memory_allocated(),
                    reserved_bytes=torch.cuda.memory_reserved(),
                )

    patched = []
    for name, module in tuple(sys.modules.items()):
        if (
            name.startswith("fastvideo.")
            and getattr(module, "safetensors_weights_iterator", None) is original
        ):
            module.safetensors_weights_iterator = iterator
            patched.append(name)
    assert "fastvideo.models.loader.weight_utils" in patched
    event(
        "checkpoint_cache_policy_installed",
        modules=patched,
        policy="POSIX_FADV_DONTNEED after completed GPU shard loading; project files only",
    )
