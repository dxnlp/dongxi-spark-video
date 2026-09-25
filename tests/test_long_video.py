"""Verify duration dispatch and protection without allocating GPU memory."""

from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "guard"))
import common
import server
import run_job as worker

JOB = "portal-20260925-120000-abcdef12"


class LongVideoTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.jobs = self.root / "portal/jobs"
        for folder in ("portal/jobs", "logs", "status", "evidence", "outputs"):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        for module in (common, server, worker):
            for key, value in (("ROOT", self.root), ("JOBS", self.jobs)):
                p = patch.object(module, key, value)
                p.start()
                self.addCleanup(p.stop)
        common.write_json(
            self.root / "portal/duration-presets.json",
            [
                {
                    "frames": 362,
                    "seconds": 361 / 24,
                    "render_seconds": 264,
                    "label": "15 seconds",
                    "memory_profile": "capacity-84",
                }
            ],
        )

    def test_server_selects_measured_profile_ignoring_client_override(self):
        process = Mock(pid=12)
        process.wait.return_value = 0
        with (
            patch.object(
                server,
                "memory",
                return_value={"available": 118 * common.GIB, "free": 108 * common.GIB},
            ),
            patch.object(server.subprocess, "check_output", return_value=""),
            patch.object(server.subprocess, "Popen", return_value=process),
        ):
            job = server.Store().submit(
                {"prompt": "Ocean", "frames": 362, "memory_profile": "standard"}
            )
        stored = common.read_json(common.job_path(job["id"]))
        self.assertEqual(stored["frames"], 362)
        self.assertEqual(stored["memory_profile"], "capacity-84")
        self.assertEqual(job["host_reserve"], 24 * common.GIB)

    def test_15_seconds_requires_116_gib_before_creating_a_job(self):
        with (
            patch.object(
                server,
                "memory",
                return_value={"available": 115 * common.GIB, "free": 108 * common.GIB},
            ),
            patch.object(server.subprocess, "Popen") as spawn,
        ):
            with self.assertRaisesRegex(server.BusyError, "116 GiB"):
                server.Store().submit({"prompt": "Ocean", "frames": 362})
            spawn.assert_not_called()
        self.assertFalse(list(self.jobs.glob("*.json")))

    def test_bad_preset_cannot_pair_long_video_with_short_profile(self):
        common.write_json(
            self.root / "portal/duration-presets.json",
            [{"frames": 362, "seconds": 15, "memory_profile": "standard"}],
        )
        with self.assertRaises(ValueError):
            common.request_preset({"frames": 362})
        self.assertEqual(common.request_preset({})["memory_profile"], "standard")

    def prepare_worker(self):
        common.write_json(
            common.job_path(JOB),
            dict(
                id=JOB,
                state="starting",
                created_at=1,
                prompt="Ocean",
                seed=2026,
                frames=362,
                memory_profile="capacity-84",
            ),
        )

    def test_unhealthy_telemetry_prevents_any_model_launch(self):
        self.prepare_worker()
        with (
            patch.object(worker, "verify_dependencies"),
            patch.object(worker.signal, "signal"),
            patch.object(
                worker, "health_sample", side_effect=RuntimeError("sensor unavailable")
            ),
            patch.object(worker, "launch") as launch,
            patch.object(worker.traceback, "print_exc"),
        ):
            worker.run(JOB)
            launch.assert_not_called()
        self.assertEqual(common.read_json(common.job_path(JOB))["state"], "failed")

    def test_worker_dispatches_362_frames_and_monitor_with_validated_output(self):
        self.prepare_worker()
        item = dict(container_id="a" * 64, guard_pid=987654, deadline_seconds=300)

        def launch(*args, **kwargs):
            self.assertEqual(kwargs["num_frames"], 362)
            self.assertEqual(kwargs["memory_profile"], "capacity-84")
            common.write_json(
                self.root / "status" / f"{JOB}-guard.json",
                {"state": "container_exited"},
            )
            return item

        monitor = Mock(pid=123)
        monitor.poll.return_value = None
        with ExitStack() as stack:
            for obj, key, value in (
                (worker, "verify_dependencies", Mock()),
                (worker.signal, "signal", Mock()),
                (
                    worker,
                    "health_sample",
                    Mock(return_value={"kernel_faults": "", "temperature_c": 40}),
                ),
                (worker.subprocess, "Popen", Mock(return_value=monitor)),
                (worker.subprocess, "check_output", Mock(return_value="")),
                (worker, "launch", Mock(side_effect=launch)),
                (
                    worker,
                    "inspect",
                    Mock(
                        return_value={
                            "State": {
                                "Running": False,
                                "ExitCode": 0,
                                "StartedAt": "2026-09-25T12:00:00Z",
                            }
                        }
                    ),
                ),
                (
                    worker,
                    "validate_output",
                    Mock(
                        return_value={
                            "streams": [
                                {
                                    "codec_type": "video",
                                    "duration": "15.041667",
                                    "nb_read_frames": "361",
                                }
                            ]
                        }
                    ),
                ),
                (worker, "poster", Mock()),
                (
                    worker,
                    "events_for",
                    Mock(
                        return_value=[
                            {
                                "event": "generation_request_complete",
                                "wall_time": 1790337864,
                                "seconds": 250,
                            }
                        ]
                    ),
                ),
            ):
                stack.enter_context(patch.object(obj, key, value))
            worker.run(JOB)
            command = worker.subprocess.Popen.call_args.args[0]
            self.assertEqual(command[-1], JOB)
            self.assertTrue(command[-2].endswith("/scripts/capacity_monitor.py"))
            worker.validate_output.assert_called_once_with(
                JOB, unittest.mock.ANY, expected_frames=362
            )
        job = common.read_json(common.job_path(JOB))
        self.assertEqual(job["state"], "complete")
        self.assertEqual(job["frames"], 361)
        self.assertEqual(job["requested_frames"], 362)
        self.assertEqual(job["memory_profile"], "capacity-84")
        monitor.wait.assert_called_once_with(timeout=6)


if __name__ == "__main__":
    unittest.main()
