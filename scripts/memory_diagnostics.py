"""Read shared host and CUDA allocator counters without retaining tensors."""

from pathlib import Path


def memory_snapshot(torch):
    stats = torch.cuda.memory_stats()
    host = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key = line.split(":", 1)[0]
        if key in ("MemAvailable", "MemFree", "Cached", "SwapFree"):
            host[key] = int(line.split()[1]) * 1024
    return {
        "allocated_bytes": torch.cuda.memory_allocated(),
        "reserved_bytes": torch.cuda.memory_reserved(),
        "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
        "peak_reserved_bytes": torch.cuda.max_memory_reserved(),
        "inactive_split_bytes": stats.get("inactive_split_bytes.all.current"),
        "host_memory": host,
    }
