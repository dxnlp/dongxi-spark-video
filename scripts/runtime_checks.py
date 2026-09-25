"""CPU dependency checks and full media validation after a guarded render."""

import json
import subprocess
from launch_gpu import ROOT, aligned_frames


def inspect(name):
    return json.loads(
        subprocess.check_output(["docker", "inspect", name], text=True, timeout=10)
    )[0]


def verify_dependencies():
    report = (ROOT / "evidence/dependency-check.txt").read_text()
    if "Found 1 incompatibility" in report:
        # NVIDIA's SBSA wheel tag is nonstandard; verify the actual ELF target.
        assert "nvidia-cusparselt-cu13" in report and "different platform" in report
        import struct

        library = (
            ROOT
            / "runtime/venv/lib/python3.12/site-packages/nvidia/cusparselt/lib/libcusparseLt.so.0"
        )
        with library.open("rb") as f:
            header = f.read(20)
        assert header[:4] == b"\x7fELF" and struct.unpack("<H", header[18:20])[0] == 183
        return
    assert "All installed packages are compatible" in report, report


def validate_output(name, state, expected_frames=124):
    output = ROOT / "outputs" / name
    media = list(output.glob("*.mp4"))
    assert len(media) == 1, "Expected one MP4"
    events = [
        json.loads(line)
        for file in output.glob("observer-*.jsonl")
        for line in file.read_text().splitlines()
    ]
    forwards = [e for e in events if e["event"] == "forward_complete"]
    assert len(forwards) == 4 and sorted(e["number"] for e in forwards) == [1, 2, 3, 4]
    assert any(e["event"] == "upstream_example_complete" for e in events)
    # Decode validation is a small, bounded CPU-only container after GPU exit.
    image = json.loads((ROOT / "evidence/base-image.json").read_text())[
        "prepared_image"
    ]
    common = [
        "docker",
        "run",
        "--rm",
        "--label",
        "local.project=dongxi-spark-video",
        "--network",
        "none",
        "--cpus",
        "2",
        "--memory",
        "2g",
        "--memory-swap",
        "2g",
        "-e",
        "NVIDIA_VISIBLE_DEVICES=void",
        "--mount",
        f"type=bind,src={output},dst=/output,readonly",
    ]
    report = json.loads(
        subprocess.check_output(
            [
                *common,
                "--entrypoint",
                "ffprobe",
                image,
                "-v",
                "error",
                "-count_frames",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                "/output/" + media[0].name,
            ],
            text=True,
            timeout=45,
        )
    )
    video = next(s for s in report["streams"] if s["codec_type"] == "video")
    aligned = aligned_frames(expected_frames)
    actual = int(video["nb_read_frames"])
    # Upstream rounds to its causal frame grid and muxes with -shortest. Audio
    # quantization can remove one terminal video frame; verify the exact bracket
    # and A/V timing instead of accepting arbitrary mismatched frame counts.
    assert video["width"] == 832 and video["height"] == 480
    assert actual in (aligned - 1, aligned), (
        f"Expected {aligned - 1} or {aligned} frames, got {actual}"
    )
    assert video["avg_frame_rate"] == "24/1"
    audio = next(s for s in report["streams"] if s["codec_type"] == "audio")
    assert abs(float(video["duration"]) - actual / 24) < 0.002
    assert abs(float(audio["duration"]) - float(video["duration"])) <= 1 / 24 + 0.002, (
        "A/V duration mismatch"
    )
    subprocess.run(
        [
            *common,
            "--entrypoint",
            "ffmpeg",
            image,
            "-v",
            "error",
            "-i",
            "/output/" + media[0].name,
            "-f",
            "null",
            "-",
        ],
        check=True,
        timeout=45,
    )
    (output / "media-validation.json").write_text(
        json.dumps(
            {
                "ffprobe": report,
                "full_decode": "passed",
                "requested_frames": expected_frames,
                "aligned_model_frames": aligned,
                "actual_frames": actual,
                "forward_count": 4,
                "visual_review": "pending",
                "docker_state": state,
            },
            indent=2,
        )
        + "\n"
    )
    return report
