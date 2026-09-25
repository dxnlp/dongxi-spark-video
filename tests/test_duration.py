import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import runtime_checks as coordinate
from launch_gpu import aligned_frames, validate_frames


class DurationTests(unittest.TestCase):
    def test_upstream_frame_grid_and_search_bounds(self):
        self.assertEqual(
            [aligned_frames(n) for n in (124, 148, 158, 172, 175, 360)],
            [124, 158, 158, 175, 175, 362],
        )
        for n in (True, 123, 363, 148.0, "148"):
            with self.assertRaises(ValueError):
                validate_frames(n)

    def test_validation_accepts_only_expected_grid_and_mux_rounding(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "outputs/test"
            output.mkdir(parents=True)
            (root / "evidence").mkdir()
            (root / "evidence/base-image.json").write_text('{"prepared_image":"test"}')
            (output / "video.mp4").touch()
            events = [{"event": "forward_complete", "number": i} for i in range(1, 5)]
            events.append({"event": "upstream_example_complete"})
            (output / "observer-1.jsonl").write_text("\n".join(map(json.dumps, events)))

            def report(frames, audio_duration=6.575):
                return json.dumps(
                    {
                        "streams": [
                            {
                                "codec_type": "video",
                                "width": 832,
                                "height": 480,
                                "nb_read_frames": str(frames),
                                "avg_frame_rate": "24/1",
                                "duration": str(frames / 24),
                            },
                            {"codec_type": "audio", "duration": str(audio_duration)},
                        ]
                    }
                )

            with (
                patch.object(coordinate, "ROOT", root),
                patch.object(coordinate.subprocess, "run") as decode,
            ):
                for frames in (157, 158):
                    with patch.object(
                        coordinate.subprocess,
                        "check_output",
                        return_value=report(frames),
                    ):
                        coordinate.validate_output("test", {"ExitCode": 0}, 148)
                self.assertEqual(decode.call_count, 2)
                for frames, audio in ((156, 6.575), (159, 6.575), (157, 6.0)):
                    with (
                        patch.object(
                            coordinate.subprocess,
                            "check_output",
                            return_value=report(frames, audio),
                        ),
                        self.assertRaises(AssertionError),
                    ):
                        coordinate.validate_output("test", {"ExitCode": 0}, 148)
                self.assertEqual(decode.call_count, 2)


if __name__ == "__main__":
    unittest.main()
