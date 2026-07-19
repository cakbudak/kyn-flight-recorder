from __future__ import annotations

import json
import tempfile
import threading
import unittest
from functools import partial
from http.client import HTTPResponse
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from serve import DemoRequestHandler, DemoServer, ROOT, SECURITY_HEADERS, main


class DemoServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        handler = partial(DemoRequestHandler, directory=str(ROOT))
        cls.server = DemoServer(("127.0.0.1", 0), handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def open(self, path: str) -> HTTPResponse:
        return urlopen(f"{self.base_url}{path}", timeout=2)  # noqa: S310 - loopback fixture

    def test_root_redirects_to_the_single_app_entry(self) -> None:
        with self.open("/") as response:
            self.assertEqual(response.status, 200)
            self.assertTrue(response.geturl().endswith("/app/"))
            self.assertIn(b"Kyn.ist Flight Recorder", response.read())

    def test_health_is_explicit_about_runtime_availability(self) -> None:
        with self.open("/healthz") as response:
            payload = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(
                payload,
                {
                    "status": "ok",
                    "mode": "closed-loop-agent-runtime",
                    "sqlite": "unavailable",
                    "openai_configured": False,
                },
            )

    def test_security_headers_cover_html_and_json(self) -> None:
        for path in ("/app/", "/app/data/demo-run.json", "/healthz"):
            with self.subTest(path=path), self.open(path) as response:
                for name, expected in SECURITY_HEADERS.items():
                    self.assertEqual(response.headers[name], expected)
                self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_directory_listing_is_disabled(self) -> None:
        with self.assertRaises(HTTPError) as raised:
            self.open("/docs/")
        self.assertEqual(raised.exception.code, 404)

    def test_missing_and_traversal_paths_do_not_escape_the_repo(self) -> None:
        for path in ("/missing", "/..%2F..%2Fetc%2Fpasswd"):
            with self.subTest(path=path), self.assertRaises(HTTPError) as raised:
                self.open(path)
            self.assertEqual(raised.exception.code, 404)

    def test_server_exposes_no_write_method(self) -> None:
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            request = Request(f"{self.base_url}/healthz", data=b"{}", method=method)
            with self.subTest(method=method), self.assertRaises(HTTPError) as raised:
                urlopen(request, timeout=2)  # noqa: S310 - loopback fixture
            self.assertEqual(raised.exception.code, 405)
            self.assertEqual(raised.exception.headers["Allow"], "GET, HEAD")

    def test_head_matches_get_routes_without_a_body(self) -> None:
        for path, status in (("/", 200), ("/healthz", 200), ("/app/", 200)):
            request = Request(f"{self.base_url}{path}", method="HEAD")
            with self.subTest(path=path), urlopen(request, timeout=2) as response:  # noqa: S310
                self.assertEqual(response.status, status)
                self.assertEqual(response.read(), b"")

    def test_symlink_paths_cannot_escape_the_served_root(self) -> None:
        with tempfile.TemporaryDirectory() as root_name, tempfile.TemporaryDirectory() as outside_name:
            root = Path(root_name)
            outside = Path(outside_name)
            (outside / "secret.txt").write_text("must-not-be-served", encoding="utf-8")
            (root / "escape").symlink_to(outside, target_is_directory=True)

            handler = partial(DemoRequestHandler, directory=str(root))
            server = DemoServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with self.assertRaises(HTTPError) as raised:
                    urlopen(  # noqa: S310 - loopback fixture
                        f"http://127.0.0.1:{server.server_port}/escape/secret.txt",
                        timeout=2,
                    )
                self.assertEqual(raised.exception.code, 404)
                self.assertTrue(raised.exception.geturl().endswith("/escape/secret.txt"))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_invalid_cli_port_fails_before_bind(self) -> None:
        self.assertEqual(main(["--port", "-1"]), 2)
        self.assertEqual(main(["--port", "70000"]), 2)


if __name__ == "__main__":
    unittest.main()
