import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import workboard


class ProjectionTests(unittest.TestCase):
    def test_allowlist_drops_unknown_fields_and_rejects_bad_shapes(self):
        value = workboard.project({"token": "PRIVATE_CANARY", "tasks": [None, [], {
            "id": "task", "title": "Example", "status": "working", "secret": "PRIVATE_CANARY"}],
            "team": {"members": [True, {"name": "Helper", "status": "injected class"},
                                 {"name": "Malformed", "status": {"bad": True}}]}})
        self.assertNotIn("PRIVATE_CANARY", json.dumps(value))
        self.assertEqual(len(value["tasks"]), 1)
        self.assertEqual(value["team"]["members"][0]["status"], "queued")
        for malformed in (None, [], True, "text"):
            self.assertEqual(workboard.project(malformed)["tasks"], [])

    def test_links_and_progress_fail_closed(self):
        for url in ("javascript:alert(1)", "//evil.example", "/\\evil.example", "https://user:pass@example.test"):
            self.assertIsNone(workboard.safe_url(url))
        self.assertEqual(workboard.safe_url("https://example.test/help"), "https://example.test/help")
        self.assertEqual(workboard.safe_url("/preview"), "/preview")
        for progress in ({"value": True, "max": 5}, {"value": 1, "max": 0},
                         {"value": float("nan"), "max": 5}, {"value": 6, "max": 5}):
            card = workboard.project({"cards": [{"label": "Example", "value": 5, "progress": progress}]})["cards"][0]
            self.assertNotIn("progress", card)


class FileTests(unittest.TestCase):
    def test_last_good_snapshot_survives_partial_json_and_updates_after_replace(self):
        with tempfile.TemporaryDirectory() as folder:
            config = workboard.Config(state_dir=Path(folder))
            board = workboard.Board(config)
            path = Path(folder) / "tasks.json"
            path.write_text(json.dumps({"tasks": [{"title": "First"}]}))
            self.assertEqual(board.snapshot()["tasks"][0]["title"], "First")
            path.write_text("{")
            self.assertEqual(board.snapshot()["tasks"][0]["title"], "First")
            self.assertIn("warning", board.snapshot())
            temp = path.with_suffix(".new")
            temp.write_text(json.dumps({"tasks": [{"title": "Second"}]})); temp.replace(path)
            self.assertEqual(board.snapshot()["tasks"][0]["title"], "Second")
            self.assertNotIn("warning", board.snapshot())

    def test_producer_failure_retains_data_without_exposing_exception(self):
        def failed():
            raise ValueError("PRIVATE_CANARY /secret/file")
        board = workboard.Board(snapshot_fn=lambda: {"tasks": [{"title": "Good"}]})
        board.snapshot(); board.snapshot_fn = failed
        value = board.snapshot()
        self.assertEqual(value["tasks"][0]["title"], "Good")
        self.assertIn("warning", value)
        self.assertNotIn("PRIVATE_CANARY", json.dumps(value))

    def test_init_preserves_existing_data_and_uses_only_synthetic_examples(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            workboard.initialize(path)
            self.assertTrue((path / "tasks.json").exists())
            (path / "tasks.json").write_text("existing data")
            workboard.initialize(path)
            self.assertEqual((path / "tasks.json").read_text(), "existing data")


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.state = {"tasks": [{"title": "First"}], "secret": "PRIVATE_CANARY"}
        root = Path(self.folder.name)
        (root / "secret.txt").write_text("PRIVATE_CANARY")
        preview = root / "preview.html"; preview.write_text("Approved preview")
        self.board = workboard.Board(workboard.Config(state_dir=root, title="Example <project>",
                                    file_routes={"/preview": preview}), snapshot_fn=lambda: self.state)
        self.server = workboard.make_server(self.board, host="127.0.0.1", port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(2); self.folder.cleanup()

    def request(self, path, method="GET"):
        c = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        c.request(method, path); response = c.getresponse()
        body = response.read(); result = response.status, response.getheaders(), body
        c.close(); return result

    def test_allowlisted_routes_methods_and_private_projection(self):
        self.assertIn(b"Example &lt;project&gt;", self.request("/")[2])
        self.assertNotIn(b"PRIVATE_CANARY", self.request("/api/status")[2])
        self.assertEqual(self.request("/preview")[2], b"Approved preview")
        for path in ("/secret.txt", "/../secret.txt", "/%2e%2e/secret.txt", "/api/other"):
            self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request("/api/status", "POST")[0], 405)
        self.assertEqual(self.request("/", "HEAD")[2], b"")

    def test_stream_reconnect_receives_current_state(self):
        for title in ("First", "Changed"):
            self.state["tasks"][0]["title"] = title
            c = http.client.HTTPConnection(*self.server.server_address, timeout=3)
            c.request("GET", "/api/events"); r = c.getresponse()
            self.assertEqual(r.status, 200)
            self.assertEqual(r.getheader("X-Accel-Buffering"), "no")
            frame = []
            while (line := r.readline()) != b"\n": frame.append(line)
            data = next(line[6:] for line in frame if line.startswith(b"data: "))
            self.assertEqual(json.loads(data)["tasks"][0]["title"], title)
            self.assertNotIn(b"PRIVATE_CANARY", data)
            r.close(); c.close()

    def test_hub_tabs_are_cached_and_producer_endpoint_does_not_aggregate(self):
        path = Path(self.folder.name) / "sources.json"
        url = "http://%s:%s/api/status" % self.server.server_address
        path.write_text(json.dumps({"sources": [{"id": "example", "title": "Example project", "url": url}]}))
        hub = workboard.Board(workboard.Config(sources_file=path, include_local=True),
                              snapshot_fn=lambda: {"tasks": [{"title": "Local"}]})
        with patch("workboard.fetch_source", wraps=workboard.fetch_source) as fetch:
            value = hub.snapshot(); hub.snapshot()
            self.assertEqual(fetch.call_count, 1)
        self.assertEqual([w["id"] for w in value["workspaces"]], ["local", "example"])
        self.assertEqual(value["workspaces"][1]["data"]["tasks"][0]["title"], "First")
        self.assertNotIn(url, json.dumps(value))
        self.assertNotIn("PRIVATE_CANARY", json.dumps(value))
        self.assertNotIn("workspaces", hub.local_snapshot())
        hub._next_poll = 0
        with patch("workboard.fetch_source", side_effect=OSError("PRIVATE_CANARY")):
            offline = hub.snapshot()["workspaces"][1]
        self.assertFalse(offline["available"])
        self.assertEqual(offline["data"]["tasks"][0]["title"], "First")
        self.assertNotIn("PRIVATE_CANARY", json.dumps(offline))
        # Replacing a destination must not reuse another workspace's cached data.
        path.write_text(json.dumps({"sources": [{"id": "example", "title": "Replacement", "url": "http://127.0.0.1:1/api/status"}]}))
        hub._next_poll = 0
        with patch("workboard.fetch_source", side_effect=OSError()):
            self.assertEqual(hub.snapshot()["workspaces"][1]["data"]["tasks"], [])

    def test_invalid_source_configuration_makes_no_requests(self):
        path = Path(self.folder.name) / "sources.json"
        for value in ([None], [{"id": "local", "title": "Reserved", "url": "http://127.0.0.1/api/status"}],
                      [{"id": "bad", "title": "Bad", "url": "http://user:password@127.0.0.1/api/status"}],
                      [{"id": "bad", "title": "Bad", "url": "http://127.0.0.1/private"}]):
            path.write_text(json.dumps({"sources": value}))
            hub = workboard.Board(workboard.Config(sources_file=path))
            with patch("workboard.fetch_source") as fetch:
                self.assertIn("warning", hub.snapshot()); fetch.assert_not_called()

    def test_remote_relative_links_cannot_route_to_the_hubs_local_workspace(self):
        self.state["tasks"][0]["url"] = "/preview"
        self.state["links"] = [{"label": "Remote preview", "url": "/preview"},
                               {"label": "Project", "url": "https://example.com/project"}]
        url = "http://%s:%s/api/status" % self.server.server_address
        remote = workboard.fetch_source(url)
        self.assertNotIn("url", remote["tasks"][0])
        self.assertEqual(remote["links"], [{"label": "Project", "url": "https://example.com/project"}])
        self.assertEqual(self.board.local_snapshot()["tasks"][0]["url"], "/preview")


class StreamTests(unittest.TestCase):
    def test_changes_and_heartbeats_without_clock_only_updates(self):
        samples = iter([{"served_at": "a", "tasks": []}, {"served_at": "b", "tasks": []},
                        {"served_at": "c", "tasks": []}, {"served_at": "d", "tasks": [1]}])
        clock = [0]
        def wait(seconds): clock[0] += seconds
        stream = workboard.event_frames(lambda: next(samples), wait=wait, clock=lambda: clock[0], heartbeat_seconds=2)
        self.assertIn(b"event: status", next(stream))
        self.assertEqual(next(stream), b": heartbeat\n\n")
        self.assertIn(b'"tasks": [1]', next(stream))


if __name__ == "__main__":
    unittest.main()
