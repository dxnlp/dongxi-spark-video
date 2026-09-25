import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import resource_guard as guard
import resource_entrypoint as entry

CID = "a" * 64


def info(status, running=False):
    return {
        "Id": CID,
        "State": {"Running": running, "ExitCode": 0},
        "Config": {
            "Labels": {
                "local.project": guard.PROJECT,
                "local.resource-guard": "required",
            },
            "Hostname": CID[:12],
            "Entrypoint": ["python3"],
            "Cmd": [
                "/guard-code/resource_entrypoint.py",
                "--status",
                f"/guard-status/{status.name}",
                "--",
                "sleep",
                "1",
            ],
        },
        "HostConfig": {
            "RestartPolicy": {"Name": "no"},
            "Memory": 128 * 1024**2,
            "MemorySwap": 128 * 1024**2,
        },
        "Mounts": [
            {"Destination": "/guard-code", "RW": False},
            {
                "Destination": "/guard-status",
                "RW": False,
                "Source": str(status.parent.resolve()),
            },
        ],
    }


def memory(available=118, swap_used=0, free=40):
    return {
        "MemTotal": 121 * guard.GIB,
        "MemAvailable": available * guard.GIB,
        "MemFree": free * guard.GIB,
        "Cached": 77 * guard.GIB,
        "SwapTotal": 16 * guard.GIB,
        "SwapFree": 16 * guard.GIB - swap_used,
    }


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.status = Path(self.temp.name) / "test.json"
        self.info = info(self.status)

    def test_rejects_wrong_owner_old_container_and_unsafe_limits(self):
        guard.validate_target(self.info, CID)
        mutations = [
            lambda x: x["Config"]["Labels"].pop("local.resource-guard"),
            lambda x: x["Config"]["Labels"].update({"local.project": "another"}),
            lambda x: x["State"].update(Running=True),
            lambda x: x["HostConfig"]["RestartPolicy"].update(Name="always"),
            lambda x: x["HostConfig"].update(Memory=116 * guard.GIB),
            lambda x: x["HostConfig"].update(MemorySwap=-1),
            lambda x: x["Config"].update(Entrypoint=["vllm"]),
            lambda x: x.update(Id="b" * 64),
        ]
        for mutation in mutations:
            bad = copy.deepcopy(self.info)
            mutation(bad)
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                guard.validate_target(bad, CID)
        with self.assertRaises(ValueError):
            guard.validate_target(self.info, CID[:12])

    def test_stop_only_exact_owned_container_and_fallback(self):
        running = info(self.status, True)
        runner = MagicMock(side_effect=[subprocess.TimeoutExpired("docker", 4), ""])
        inspector = MagicMock(side_effect=[running, self.info])
        guard.stop_owned(CID, inspector, runner)
        self.assertEqual(runner.call_args_list[0].args, ("stop", "--time", "1", CID))
        self.assertEqual(runner.call_args_list[1].args, ("kill", CID))
        foreign = copy.deepcopy(running)
        foreign["Config"]["Labels"]["local.project"] = "foreign"
        runner.reset_mock()
        with self.assertRaises(RuntimeError):
            guard.stop_owned(CID, lambda _: foreign, runner)
        runner.assert_not_called()

    def run_guard(
        self,
        memories,
        *,
        pressure=None,
        duration=1,
        slow_inspect=False,
        memory_profile="standard",
        budget=80,
    ):
        clock = [0.0]
        count = [0]

        def inspect(_):
            count[0] += 1
            if slow_inspect and count[0] > 1:
                clock[0] += 2.1
            return self.info if count[0] == 1 else info(self.status, True)

        child = MagicMock()
        child.poll.return_value = 0

        def sleep(seconds):
            clock[0] += seconds

        pressure_reader = MagicMock(return_value=pressure or {"full": 0, "some": 0})
        if isinstance(pressure, list):
            pressure_reader.side_effect = pressure
        with (
            patch.object(guard, "inspect", side_effect=inspect),
            patch.object(guard, "read_memory", side_effect=memories),
            patch.object(guard, "read_pressure", pressure_reader),
            patch.object(guard, "stop_owned") as stop,
            patch.object(guard.subprocess, "Popen", return_value=child) as start,
            patch.object(guard.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(guard.time, "sleep", side_effect=sleep),
        ):
            with self.assertRaises((RuntimeError, OSError)) as caught:
                guard.supervise(CID, self.status, duration, budget, memory_profile)
            state = (
                json.loads(self.status.read_text()) if self.status.exists() else None
            )
            return str(caught.exception), state, stop, start

    def test_admission_refuses_before_start(self):
        error, state, stop, start = self.run_guard([memory(100)])
        self.assertIn("Admission refused", error)
        self.assertIsNone(state)
        stop.assert_not_called()
        start.assert_not_called()

    def test_low_memory_triggers_early_stop(self):
        error, state, stop, _ = self.run_guard([memory(), memory(31)])
        self.assertIn("below 32 GiB", error)
        self.assertEqual(state["state"], "aborted_container_stopped")
        self.assertEqual(state["minimum_available_bytes"], 31 * guard.GIB)
        self.assertEqual(state["memory"]["MemAvailable"], 31 * guard.GIB)
        self.assertEqual(state["pressure"], {"some": 0, "full": 0})
        stop.assert_called_once_with(CID)

    def test_sensor_failure_fails_closed(self):
        error, state, stop, _ = self.run_guard([memory(), OSError("sensor unreadable")])
        self.assertIn("sensor unreadable", error)
        stop.assert_called_once_with(CID)

    def test_free_pages_stop_even_when_available_is_high(self):
        error, state, stop, _ = self.run_guard([memory(), memory(90, free=7)])
        self.assertIn("8 GiB reserve", error)
        self.assertEqual(state["minimum_free_bytes"], 7 * guard.GIB)
        self.assertEqual(state["initial_memory"]["MemFree"], 40 * guard.GIB)
        self.assertEqual(state["memory"]["Cached"], 77 * guard.GIB)
        stop.assert_called_once_with(CID)

    def test_pressure_abort_retains_trigger_reading(self):
        samples = [{"full": 0, "some": 0}, {"full": 1.25, "some": 1.3}]
        error, state, stop, _ = self.run_guard([memory(), memory(90)], pressure=samples)
        self.assertIn("memory pressure", error)
        self.assertEqual(state["pressure"], samples[-1])
        self.assertEqual(state["maximum_pressure_avg10"], samples[-1])
        self.assertEqual(state["memory"]["MemAvailable"], 90 * guard.GIB)
        stop.assert_called_once_with(CID)

    def test_stage_budget_aborts_before_absolute_reserve(self):
        error, _, stop, _ = self.run_guard([memory(), memory(37)])
        self.assertIn("exceeded the stage budget", error)
        stop.assert_called_once_with(CID)

    def test_deadline_stops_job(self):
        error, _, stop, _ = self.run_guard([memory()] * 10)
        self.assertIn("runtime exceeded", error)
        stop.assert_called_once_with(CID)

    def test_inspect_does_not_mask_scheduling_gap(self):
        error, _, stop, _ = self.run_guard([memory()] * 10, slow_inspect=True)
        self.assertIn("scheduling gap", error)
        stop.assert_called_once_with(CID)

    def test_swap_growth_stops_job(self):
        error, _, stop, _ = self.run_guard([memory(), memory(swap_used=256 * 1024**2)])
        self.assertIn("swap consumption", error)
        stop.assert_called_once_with(CID)

    def test_pressure_and_parsers(self):
        self.assertIsNotNone(guard.abort_reason(memory(), {"full": 1, "some": 0}))
        self.assertIsNotNone(guard.abort_reason(memory(), {"full": 0, "some": 5}))
        p = Path(self.temp.name) / "meminfo"
        p.write_text(
            "MemTotal: 100 kB\nMemAvailable: 50 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n"
        )
        self.assertEqual(guard.read_memory(p)["MemAvailable"], 50 * 1024)
        p.write_text("some avg10=nan\nfull avg10=0\n")
        with self.assertRaises(ValueError):
            guard.read_pressure(p)

    def test_lease_requires_fresh_matching_watching_record(self):
        record = {"state": "watching", "container_id": CID, "monotonic": 10}
        self.status.write_text(json.dumps(record))
        self.assertTrue(entry.lease_valid(self.status, CID[:12], 11))
        self.assertFalse(entry.lease_valid(self.status, CID[:12], 13))
        self.assertFalse(entry.lease_valid(self.status, "b" * 12, 11))
        self.assertFalse(entry.lease_valid(self.status, CID[:12], 9))
        for state in ("armed", "aborting", "aborted_container_stopped"):
            record["state"] = state
            self.status.write_text(json.dumps(record))
            self.assertFalse(entry.lease_valid(self.status, CID[:12], 11))

    def test_capacity_profile_cannot_silently_weaken_standard(self):
        self.info["Config"]["Labels"]["local.memory-profile"] = "capacity-84"
        self.info["HostConfig"].update(Memory=92 * guard.GIB, MemorySwap=92 * guard.GIB)
        with self.assertRaises(ValueError):
            guard.validate_target(self.info, CID)
        guard.validate_target(self.info, CID, "capacity-84")
        self.info["HostConfig"].update(Memory=93 * guard.GIB, MemorySwap=93 * guard.GIB)
        with self.assertRaises(ValueError):
            guard.validate_target(self.info, CID, "capacity-84")
        with self.assertRaises(ValueError):
            guard.profile("unlimited")
        self.assertEqual(guard.profile()["cuda_gib"], 72)
        self.assertEqual(guard.profile()["reserve_gib"], 32)

    def test_capacity_reserve_and_sample_history_preserve_trigger(self):
        self.info["Config"]["Labels"]["local.memory-profile"] = "capacity-84"
        error, state, stop, _ = self.run_guard(
            [memory(), memory(23)], memory_profile="capacity-84", budget=92
        )
        self.assertIn("24 GiB", error)
        self.assertEqual(state["absolute_available_reserve_bytes"], 24 * guard.GIB)
        samples = [
            json.loads(s)
            for s in self.status.with_suffix(".samples.jsonl").read_text().splitlines()
        ]
        self.assertEqual(samples[-1]["memory"]["MemAvailable"], 23 * guard.GIB)
        stop.assert_called_once_with(CID)

    def test_capacity_admission_needs_growth_budget_plus_reserve(self):
        self.info["Config"]["Labels"]["local.memory-profile"] = "capacity-84"
        error, _, _, start = self.run_guard(
            [memory(115)], memory_profile="capacity-84", budget=92
        )
        self.assertIn("116 GiB", error)
        start.assert_not_called()

    def test_capacity_runtime_and_budget_cannot_be_extended(self):
        for budget, duration in ((93, 300), (92, 301)):
            with self.assertRaises(ValueError):
                guard.supervise(CID, self.status, duration, budget, "capacity-84")


if __name__ == "__main__":
    unittest.main(verbosity=2)
