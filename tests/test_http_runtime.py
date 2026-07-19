from __future__ import annotations

import json
import tempfile
import threading
import unittest
from functools import partial
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend.service import ControlPlane
from backend.store import Store
from serve import DemoRequestHandler, DemoServer, ROOT
from tests.test_runtime_contract import ScriptedResponsesClient


class RuntimeHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        store = Store(Path(cls.temporary.name) / "http.sqlite3")
        store.initialize()
        cls.client = ScriptedResponsesClient(store)
        plane = ControlPlane(store, cls.client)
        handler = partial(DemoRequestHandler, directory=str(ROOT))
        cls.server = DemoServer(
            ("127.0.0.1", 0),
            handler,
            control_plane=plane,
            model_configured=True,
            workspace_model_call_limit=16,
        )
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.temporary.cleanup()

    def request(
        self,
        method: str,
        path: str,
        *,
        body: object | None = None,
        cookie: str | None = None,
        origin: str | None = None,
        raw_body: bytes | None = None,
    ) -> tuple[int, dict[str, object], object]:
        data = raw_body
        headers: dict[str, str] = {}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if cookie:
            headers["Cookie"] = cookie
        if origin:
            headers["Origin"] = origin
            headers["Sec-Fetch-Site"] = "same-origin"
        request = Request(f"{self.base_url}{path}", data=data, method=method, headers=headers)
        try:
            response = urlopen(request, timeout=10)  # noqa: S310 - loopback fixture
        except HTTPError as error:
            payload = json.loads(error.read() or b"{}")
            return error.code, dict(error.headers), payload
        with response:
            payload = json.loads(response.read() or b"{}")
            return response.status, dict(response.headers), payload

    def create_workspace(self) -> tuple[str, dict[str, object]]:
        status, headers, payload = self.request(
            "POST", "/api/v1/workspaces", body={}, origin=self.base_url
        )
        self.assertEqual(status, 201)
        cookie = str(headers["Set-Cookie"]).split(";", 1)[0]
        return cookie, payload["data"]

    def test_health_reports_real_runtime_without_exposing_a_secret(self) -> None:
        status, _headers, payload = self.request("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(payload["mode"], "closed-loop-agent-runtime")
        self.assertEqual(payload["sqlite"], "ready")
        self.assertTrue(payload["openai_configured"])
        serialized = json.dumps(payload).lower()
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("sk-", serialized)

    def test_workspace_bootstrap_is_cookie_isolated_and_contains_real_resources(self) -> None:
        cookie, bootstrap = self.create_workspace()
        snapshot = bootstrap["snapshot"]
        self.assertGreaterEqual(len(snapshot["prompts"]), 3)
        self.assertGreaterEqual(len(snapshot["skills"]), 3)
        self.assertGreaterEqual(len(snapshot["agents"]), 3)
        self.assertEqual(len(snapshot["flows"]), 1)
        self.assertTrue(cookie.startswith("kyn_workspace="))

        status, _headers, payload = self.request(
            "GET", "/api/v1/workspace", cookie=cookie
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["workspace"]["id"], bootstrap["workspace_id"])

    def test_http_closed_loop_uses_version_fenced_commands(self) -> None:
        cookie, bootstrap = self.create_workspace()
        flow_id = bootstrap["snapshot"]["flows"][0]["id"]

        status, _headers, payload = self.request(
            "POST",
            f"/api/v1/flows/{flow_id}/runs",
            body={},
            cookie=cookie,
            origin=self.base_url,
        )
        self.assertEqual(status, 201)
        blocked = payload["data"]
        self.assertEqual(blocked["status"], "blocked")

        status, _headers, payload = self.request(
            "POST",
            f"/api/v1/runs/{blocked['id']}/diagnoses",
            body={},
            cookie=cookie,
            origin=self.base_url,
        )
        self.assertEqual(status, 201)
        diagnosis = payload["data"]

        status, _headers, payload = self.request(
            "POST",
            f"/api/v1/diagnoses/{diagnosis['id']}/repairs",
            body={},
            cookie=cookie,
            origin=self.base_url,
        )
        self.assertEqual(status, 201)
        repair = payload["data"]

        status, _headers, payload = self.request(
            "POST",
            f"/api/v1/repairs/{repair['id']}/apply",
            body={
                "proposal_hash": repair["proposal_hash"],
                "expected_flow_revision": repair["expected_flow_revision"],
                "actor": "browser-judge",
                "reason": "Approve the evidence-bound sandbox repair and preserve immutable history.",
                "acknowledged": True,
            },
            cookie=cookie,
            origin=self.base_url,
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["flow_version"], 2)

        status, _headers, payload = self.request(
            "POST",
            f"/api/v1/runs/{blocked['id']}/rerun",
            body={},
            cookie=cookie,
            origin=self.base_url,
        )
        self.assertEqual(status, 201)
        rerun = payload["data"]
        self.assertEqual(rerun["status"], "completed")
        self.assertEqual(rerun["parent_run_id"], blocked["id"])
        self.assertEqual(len(rerun["sandbox_effects"]), 1)

    def test_mutations_reject_cross_origin_missing_cookie_and_oversize_body(self) -> None:
        status, _headers, payload = self.request(
            "POST", "/api/v1/workspaces", body={}, origin="https://attacker.invalid"
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "origin_rejected")

        status, _headers, payload = self.request("GET", "/api/v1/workspace")
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "unauthorized")

        status, _headers, payload = self.request(
            "POST",
            "/api/v1/workspaces",
            origin=self.base_url,
            raw_body=b"{" + b"x" * (32 * 1024) + b"}",
        )
        self.assertEqual(status, 413)
        self.assertEqual(payload["error"]["code"], "body_too_large")

    def test_workspace_cannot_read_another_workspaces_run(self) -> None:
        first_cookie, first = self.create_workspace()
        second_cookie, _second = self.create_workspace()
        flow_id = first["snapshot"]["flows"][0]["id"]
        status, _headers, payload = self.request(
            "POST",
            f"/api/v1/flows/{flow_id}/runs",
            body={},
            cookie=first_cookie,
            origin=self.base_url,
        )
        self.assertEqual(status, 201)
        run_id = payload["data"]["id"]

        status, _headers, payload = self.request(
            "GET", f"/api/v1/runs/{run_id}", cookie=second_cookie
        )
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "not_found")


if __name__ == "__main__":
    unittest.main()
