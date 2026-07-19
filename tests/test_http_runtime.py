from __future__ import annotations

import json
import tempfile
import threading
import unittest
from functools import partial
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend.http_api import ApiApplication, ApiRequest
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
        cls.received_api_keys: list[str] = []

        def client_factory(api_key: str) -> ScriptedResponsesClient:
            cls.received_api_keys.append(api_key)
            return cls.client

        plane = ControlPlane(store, cls.client, client_factory=client_factory)
        handler = partial(DemoRequestHandler, directory=str(ROOT))
        cls.server = DemoServer(
            ("127.0.0.1", 0),
            handler,
            control_plane=plane,
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
        api_key: str | None = None,
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
        if api_key:
            headers["X-OpenAI-API-Key"] = api_key
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
        self.assertEqual(payload["credential_mode"], "browser-session-byok")
        self.assertEqual(payload["openai_transport"], "official-python-sdk")
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
            api_key="test-browser-owned-key-for-http-contract",
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
            api_key="test-browser-owned-key-for-http-contract",
        )
        self.assertEqual(status, 201)
        diagnosis = payload["data"]

        status, _headers, payload = self.request(
            "POST",
            f"/api/v1/diagnoses/{diagnosis['id']}/repairs",
            body={},
            cookie=cookie,
            origin=self.base_url,
            api_key="test-browser-owned-key-for-http-contract",
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
            api_key="test-browser-owned-key-for-http-contract",
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

        model_calls_before_retry = self.client.store.count_rows("model_calls")
        releases_before_retry = self.client.store.count_rows("sandbox_releases")
        original_limit = self.server.api.workspace_model_call_limit
        self.server.api.workspace_model_call_limit = model_calls_before_retry
        try:
            status, _headers, payload = self.request(
                "POST",
                f"/api/v1/runs/{blocked['id']}/rerun",
                body={},
                cookie=cookie,
                origin=self.base_url,
            )
        finally:
            self.server.api.workspace_model_call_limit = original_limit
        self.assertEqual(status, 201)
        self.assertEqual(payload["data"]["id"], rerun["id"])
        self.assertEqual(self.client.store.count_rows("model_calls"), model_calls_before_retry)
        self.assertEqual(
            self.client.store.count_rows("sandbox_releases"), releases_before_retry
        )
        self.assertGreaterEqual(len(self.received_api_keys), 4)
        database_bytes = Path(self.temporary.name, "http.sqlite3").read_bytes()
        self.assertNotIn(b"test-browser-owned-key-for-http-contract", database_bytes)

    def test_new_model_action_requires_a_valid_browser_owned_key(self) -> None:
        cookie, bootstrap = self.create_workspace()
        flow_id = bootstrap["snapshot"]["flows"][0]["id"]

        status, _headers, payload = self.request(
            "POST",
            f"/api/v1/flows/{flow_id}/runs",
            body={},
            cookie=cookie,
            origin=self.base_url,
        )
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "openai_key_required")

        status, _headers, payload = self.request(
            "POST",
            f"/api/v1/flows/{flow_id}/runs",
            body={},
            cookie=cookie,
            origin=self.base_url,
            api_key="short",
        )
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "openai_key_required")

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
            api_key="test-browser-owned-key-for-isolation-contract",
        )
        self.assertEqual(status, 201)
        run_id = payload["data"]["id"]

        status, _headers, payload = self.request(
            "GET", f"/api/v1/runs/{run_id}", cookie=second_cookie
        )
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "not_found")


class BlockingResponsesClient(ScriptedResponsesClient):
    def __init__(self, store: Store) -> None:
        super().__init__(store)
        self.entered = threading.Event()
        self.release = threading.Event()
        self._blocked_once = False

    def create(self, payload: dict[str, object]) -> dict[str, object]:
        if not self._blocked_once:
            self._blocked_once = True
            self.entered.set()
            if not self.release.wait(timeout=3):
                raise AssertionError("concurrency test did not release the provider")
        return super().create(payload)


class RuntimeHttpConcurrencyTest(unittest.TestCase):
    def test_same_workspace_cannot_start_two_model_actions_concurrently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = Store(Path(temporary) / "concurrency.sqlite3")
            store.initialize()
            client = BlockingResponsesClient(store)
            plane = ControlPlane(store, client)
            bootstrap = plane.create_workspace(seed=True)
            workspace_id = bootstrap["workspace_id"]
            flow_id = bootstrap["snapshot"]["flows"][0]["id"]
            token = bootstrap["workspace_token"]
            application = ApiApplication(plane)

            def request() -> ApiRequest:
                return ApiRequest(
                    method="POST",
                    path=f"/api/v1/flows/{flow_id}/runs",
                    headers={
                        "Origin": "https://runtime.test",
                        "Sec-Fetch-Site": "same-origin",
                        "Content-Type": "application/json",
                        "Cookie": f"kyn_workspace={token}",
                        "X-OpenAI-API-Key": "test-browser-owned-key-for-concurrency-contract",
                    },
                    body=b"{}",
                    remote_address="192.0.2.10",
                    scheme="https",
                    host="runtime.test",
                )

            first_response: list[object] = []
            first = threading.Thread(
                target=lambda: first_response.append(application.dispatch(request()))
            )
            first.start()
            self.assertTrue(client.entered.wait(timeout=2))
            concurrent = application.dispatch(request())
            self.assertEqual(concurrent.status, 429)
            self.assertIn("already running", concurrent.payload["error"]["message"])
            client.release.set()
            first.join(timeout=5)
            self.assertFalse(first.is_alive())
            self.assertEqual(first_response[0].status, 201)
            self.assertEqual(len(plane.snapshot(workspace_id)["runs"]), 1)


if __name__ == "__main__":
    unittest.main()
