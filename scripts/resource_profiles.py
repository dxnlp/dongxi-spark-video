"""Measured resource profiles; short renders retain the original policy."""

PROFILES = {
    "standard": dict(cuda_gib=72, host_growth_gib=80, container_gib=80, reserve_gib=32),
    "capacity-76": dict(
        cuda_gib=76, host_growth_gib=84, container_gib=84, reserve_gib=24
    ),
    "capacity-80": dict(
        cuda_gib=80, host_growth_gib=88, container_gib=88, reserve_gib=24
    ),
    "capacity-84": dict(
        cuda_gib=84, host_growth_gib=92, container_gib=92, reserve_gib=24
    ),
}

# Only measured duration/profile combinations can be offered by the portal.
FRAME_PROFILES = {
    124: "standard",
    148: "standard",
    175: "capacity-76",
    243: "capacity-80",
    362: "capacity-84",
}


def profile(name="standard"):
    if name not in PROFILES:
        raise ValueError("Unknown resource profile")
    return dict(PROFILES[name])
