import concurrent.futures
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import common
import server


class PortalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.jobs = self.root / "portal/jobs"
        for folder in (
            "portal/jobs",
            "logs",
            "status",
            "outputs/portal-20260101-000000-aaaaaaaa",
            "evidence",
        ):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        for module in (common, server):
            for key, value in (("ROOT", self.root), ("JOBS", self.jobs)):
                patcher = patch.object(module, key, value)
                patcher.start()
                self.addCleanup(patcher.stop)

    def test_prompt_is_data_and_inputs_are_bounded(self):
        literal = "A neon sign $(touch /tmp/no) `echo no` <script>alert(1)</script>"
        self.assertEqual(
            common.validate_request({"prompt": literal, "seed": 7}), (literal, 7)
        )
        for data in (
            {"prompt": ""},
            {"prompt": " "},
            {"prompt": "x" * 1201},
            {"prompt": "\0"},
            {"prompt": "valid", "seed": True},
            {"prompt": "valid", "seed": -1},
            [],
            {},
        ):
            with self.subTest(data=str(data)[:30]), self.assertRaises(ValueError):
                common.validate_request(data)

    def test_path_traversal_and_unfinished_media_rejected(self):
        for job_id in (
            "../secret",
            "/etc/passwd",
            "portal-20260101-000000-aaaaaaaa/../../a",
            "%2e%2e",
            "portal-invalid",
        ):
            with self.assertRaises(ValueError):
                common.job_path(job_id)
        common.write_json(
            common.job_path("portal-20260101-000000-aaaaaaaa"),
            {"id": "portal-20260101-000000-aaaaaaaa", "state": "running"},
        )
        with self.assertRaises(FileNotFoundError):
            common.media_path("portal-20260101-000000-aaaaaaaa", "video")

    def test_only_measured_duration_choices_are_accepted(self):
        self.assertEqual(common.request_frames({}), 124)
        for value in (True, 148, 175, 1200, "124", 124.0):
            with self.assertRaises(ValueError):
                common.request_frames({"frames": value})
        common.write_json(
            self.root / "portal/duration-presets.json",
            [
                {
                    "frames": 148,
                    "seconds": 157 / 24,
                    "render_seconds": 157,
                    "label": "6.5 seconds",
                },
                {"frames": 9999, "seconds": 999},
            ],
        )
        self.assertEqual(common.request_frames({"frames": 148}), 148)
        self.assertEqual([p["frames"] for p in common.duration_presets()], [124, 148])
        with self.assertRaises(ValueError):
            server.Store().submit({"prompt": "Ocean", "frames": 175})

    def test_byte_ranges_support_video_seeking(self):
        self.assertEqual(common.byte_range(None, 100), (0, 99, False))
        self.assertEqual(common.byte_range("bytes=10-19", 100), (10, 19, True))
        self.assertEqual(common.byte_range("bytes=90-", 100), (90, 99, True))
        self.assertEqual(common.byte_range("bytes=-10", 100), (90, 99, True))
        for value in (
            "bytes=100-",
            "bytes=10-1",
            "bytes=-0",
            "bytes=0-1,4-5",
            "bytes=-",
        ):
            with self.assertRaises(ValueError):
                common.byte_range(value, 100)

    def test_progress_uses_events_never_time_or_premature_100(self):
        events = [{"event": "generation_request_start"}]
        self.assertEqual(common.progress(events, "running")["percent"], 5)
        events += [
            {"event": "checkpoint_cache_released", "file": f"transformer/{i}"}
            for i in range(14)
        ]
        values = [common.progress(events, "running")["percent"]]
        for step in range(1, 5):
            events.append({"event": "forward_complete", "number": step})
            values.append(common.progress(events, "running")["percent"])
        self.assertEqual(values, sorted(values))
        self.assertLess(max(values), 100)
        events.append({"event": "generation_request_complete"})
        self.assertEqual(common.progress(events, "validating")["percent"], 99)
        self.assertEqual(common.progress(events, "complete")["percent"], 100)
        self.assertLess(common.progress(events, "failed")["percent"], 100)

    def test_concurrent_submissions_start_exactly_one_worker(self):
        store = server.Store()
        process = Mock(pid=123)
        process.wait.return_value = 0
        mem = {"available": 118 * common.GIB, "free": 108 * common.GIB}
        with (
            patch.object(server, "memory", return_value=mem),
            patch.object(server.subprocess, "check_output", return_value=""),
            patch.object(server.subprocess, "Popen", return_value=process) as launch,
        ):

            def submit(_):
                try:
                    return store.submit({"prompt": "A quiet blue ocean"})["id"]
                except server.BusyError:
                    return "busy"

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                result = list(pool.map(submit, range(8)))
            self.assertEqual(result.count("busy"), 7)
            launch.assert_called_once()
            self.assertFalse(launch.call_args.kwargs.get("shell", False))

    def test_low_memory_and_foreign_workload_refuse_before_spawn(self):
        store = server.Store()
        with (
            patch.object(
                server,
                "memory",
                return_value={"available": 100 * common.GIB, "free": 90 * common.GIB},
            ),
            patch.object(server.subprocess, "Popen") as spawn,
        ):
            with self.assertRaises(server.BusyError):
                store.submit({"prompt": "Ocean"})
            spawn.assert_not_called()
        with (
            patch.object(
                server,
                "memory",
                return_value={"available": 118 * common.GIB, "free": 108 * common.GIB},
            ),
            patch.object(server.subprocess, "check_output", return_value="foreign-id"),
            patch.object(server.subprocess, "Popen") as spawn,
        ):
            with self.assertRaises(server.BusyError):
                store.submit({"prompt": "Ocean"})
            spawn.assert_not_called()

    def test_cancel_marks_only_the_selected_active_job(self):
        store = server.Store()
        job_id = "portal-20260925-120000-deadbeef"
        common.write_json(
            common.job_path(job_id),
            {
                "id": job_id,
                "state": "running",
                "created_at": 1,
                "prompt": "Ocean",
                "seed": 0,
            },
        )
        result = store.cancel(job_id)
        self.assertTrue(result["cancel_requested"])
        self.assertTrue((self.jobs / f"{job_id}.cancel").exists())
        self.assertEqual(len(list(self.jobs.glob("*.cancel"))), 1)

    def test_restart_marks_orphan_without_starting_new_gpu_work(self):
        store = server.Store()
        job_id = "portal-20260925-120000-deadbeef"
        common.write_json(
            common.job_path(job_id),
            {"id": job_id, "state": "running", "created_at": 1, "prompt": "Ocean"},
        )
        common.write_json(self.jobs / f"{job_id}.runner.json", {"pid": 999999999})
        with patch.object(server.subprocess, "Popen") as spawn:
            store.recover()
            spawn.assert_not_called()
        self.assertEqual(common.read_json(common.job_path(job_id))["state"], "failed")

    def test_gpu_reading_zero_after_container_exit(self):
        job = {
            "id": "portal-20260101-000000-aaaaaaaa",
            "state": "validating",
            "created_at": 1,
        }
        (
            self.root / "outputs/portal-20260101-000000-aaaaaaaa/observer-1.jsonl"
        ).write_text(
            json.dumps(
                {
                    "event": "forward_complete",
                    "number": 4,
                    "wall_time": 2,
                    "allocated_bytes": 70 * common.GIB,
                }
            )
            + "\n"
        )
        common.write_json(
            self.root / "status/portal-20260101-000000-aaaaaaaa-guard.json",
            {"state": "container_exited"},
        )
        self.assertEqual(common.job_view(job)["cuda_allocated"], 0)

    def test_http_origin_host_and_media_range(self):
        common.write_json(
            common.job_path("portal-20260101-000000-aaaaaaaa"),
            {"id": "portal-20260101-000000-aaaaaaaa", "state": "complete"},
        )
        content = bytes(range(100))
        (self.root / "outputs/portal-20260101-000000-aaaaaaaa/test.mp4").write_bytes(
            content
        )
        webserver = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        webserver.store = Mock()
        thread = threading.Thread(target=webserver.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(webserver.server_close)
        self.addCleanup(webserver.shutdown)
        host = f"127.0.0.1:{webserver.server_port}"

        def request(method, path, body=None, headers=None):
            conn = http.client.HTTPConnection(
                "127.0.0.1", webserver.server_port, timeout=3
            )
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            result = (response.status, dict(response.getheaders()), response.read())
            conn.close()
            return result

        code, headers, body = request(
            "GET",
            "/media/portal-20260101-000000-aaaaaaaa/video",
            headers={"Range": "bytes=10-19"},
        )
        self.assertEqual(code, 206)
        self.assertEqual(body, content[10:20])
        self.assertEqual(headers["Content-Range"], "bytes 10-19/100")
        self.assertEqual(
            request("HEAD", "/media/portal-20260101-000000-aaaaaaaa/video")[2], b""
        )
        self.assertEqual(
            request(
                "GET",
                "/media/portal-20260101-000000-aaaaaaaa/video",
                headers={"Range": "bytes=100-"},
            )[0],
            416,
        )
        self.assertEqual(
            request("GET", "/", headers={"Host": "attacker.example"})[0], 403
        )
        self.assertEqual(
            request(
                "POST",
                "/api/jobs",
                "{}",
                {
                    "Origin": "https://attacker.example",
                    "Content-Type": "application/json",
                },
            )[0],
            403,
        )
        webserver.store.submit.assert_not_called()
        self.assertEqual(
            request(
                "POST",
                "/api/jobs",
                "{}",
                {"Origin": "http://" + host, "Content-Type": "text/plain"},
            )[0],
            415,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
