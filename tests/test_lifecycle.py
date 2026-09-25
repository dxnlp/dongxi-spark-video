"""Checkout portability and shutdown ownership regressions; no Docker or GPU."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import common
import launch_gpu
import capacity_monitor
import manage
import server
import setup


class LifecycleTests(unittest.TestCase):
    def test_setup_container_is_cpu_only_and_bounded(self):
        with patch.object(setup.subprocess, "run") as command:
            setup.cpu_stage("install", 10800)
        argv = command.call_args.args[0]
        self.assertNotIn("--gpus", argv)
        self.assertIn("NVIDIA_VISIBLE_DEVICES=void", argv)
        self.assertEqual(argv[argv.index("--memory") + 1], "12g")
        self.assertEqual(argv[argv.index("--memory-swap") + 1], "12g")
        self.assertNotIn(str(Path.home()), argv)

    def test_roots_follow_checkout(self):
        expected = Path(__file__).resolve().parent.parent
        self.assertEqual(common.ROOT, expected)
        self.assertEqual(launch_gpu.ROOT, expected)
        self.assertEqual(capacity_monitor.ROOT, expected)
        self.assertEqual(manage.ROOT, expected)

    def test_empty_history_stays_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(server, "JOBS", Path(tmp)):
                self.assertEqual(server.Store().jobs(), [])
                self.assertEqual(server.Store().jobs(), [])

    def test_stop_does_not_signal_foreign_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "status").mkdir()
            (root / "status/portal-server.json").write_text(json.dumps({"pid": 12345}))
            with (
                patch.object(manage, "ROOT", root),
                patch.object(manage, "JOBS", root / "jobs"),
                patch.object(manage, "process_matches", return_value=False),
                patch.object(manage, "owned_containers", return_value=[]),
                patch.object(manage.os, "kill") as kill,
            ):
                manage.stop()
                kill.assert_not_called()

    def test_container_filter_requires_this_checkout_mount(self):
        foreign = {
            "Id": "a" * 64,
            "Mounts": [{"Destination": "/work", "Source": "/someone/else"}],
        }
        ours = {
            "Id": "b" * 64,
            "Mounts": [{"Destination": "/work", "Source": str(manage.ROOT)}],
        }
        with patch.object(
            manage.subprocess,
            "check_output",
            side_effect=[
                "a" * 64 + "\n" + "b" * 64,
                json.dumps([foreign]),
                json.dumps([ours]),
            ],
        ):
            self.assertEqual(manage.owned_containers(), [ours])

    def test_disabled_generation_stops_before_external_commands(self):
        with (
            patch.dict(launch_gpu.os.environ, {"SPARK_VIDEO_DISABLE_GENERATION": "1"}),
            patch.object(launch_gpu.subprocess, "check_output") as command,
        ):
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                launch_gpu.launch("portal-20260925-000000-12345678")
            command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
