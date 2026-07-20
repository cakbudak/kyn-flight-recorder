#!/usr/bin/env python3
"""Serve the standalone Kyn.ist Agent Studio."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from backend.contracts import ContractViolation, ProviderFailure
from backend.http_api import MAX_API_BODY, ApiApplication, ApiRequest, ApiResponse
from backend.openai_client import (
    ResponsesClient,
    UnavailableResponsesClient,
    load_env_file,
)
from backend.service import ControlPlane
from backend.store import Store


ROOT = Path(__file__).resolve().parent
APP_ENTRY = ROOT / "app" / "index.html"
DEFAULT_DATABASE = ROOT / "var" / "kyn-agent-studio.sqlite3"
HOST_RE = re.compile(r"^[A-Za-z0-9.:-]{1,255}$")

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), geolocation=(), microphone=(), payment=()",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


class DemoRequestHandler(SimpleHTTPRequestHandler):
    """Static application plus a thin same-origin JSON API adapter."""

    server_version = "KynAgentStudio/3.0"

    @property
    def runtime_server(self) -> "DemoServer":
        return self.server  # type: ignore[return-value]

    def end_headers(self) -> None:
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _redirect_to_app(self) -> None:
        self.send_response(HTTPStatus.TEMPORARY_REDIRECT)
        self.send_header("Location", "/app/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _health_payload(self) -> bytes:
        return json.dumps(
            {
                "status": "ok",
                "mode": "closed-loop-agent-runtime",
                "sqlite": "ready" if self.runtime_server.database_ready else "unavailable",
                "credential_mode": "browser-session-byok",
                "openai_transport": "official-python-sdk",
            },
            separators=(",", ":"),
        ).encode("utf-8")

    def _send_health(self, *, include_body: bool) -> None:
        payload = self._health_payload()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if include_body:
            self.wfile.write(payload)

    def _method_not_allowed(self, *, api: bool = False) -> None:
        if api:
            self._send_api_response(
                ApiResponse(
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    {"error": {"code": "method_not_allowed", "message": "API route does not allow this method"}},
                    {"Allow": "GET, POST"},
                )
            )
            return
        self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
        self.send_header("Allow", "GET, HEAD")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def translate_path(self, path: str) -> str:
        served_root = Path(self.directory).resolve()
        candidate = Path(super().translate_path(path)).resolve()
        try:
            candidate.relative_to(served_root)
        except ValueError:
            return str(served_root / ".kyn-agent-studio-path-denied")
        return str(candidate)

    def _api_request(self, method: str, path: str) -> bool:
        if not path.startswith("/api/"):
            return False
        application = self.runtime_server.api
        if application is None:
            self._send_api_response(
                ApiResponse(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": {"code": "runtime_unavailable", "message": "runtime API is unavailable"}},
                )
            )
            return True

        body = b""
        body_too_large = False
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                content_length = 0
            else:
                try:
                    content_length = int(raw_length)
                except ValueError:
                    self._send_api_response(
                        ApiResponse(
                            HTTPStatus.BAD_REQUEST,
                            {"error": {"code": "invalid_length", "message": "Content-Length is invalid"}},
                        )
                    )
                    return True
            if content_length < 0:
                self._send_api_response(
                    ApiResponse(
                        HTTPStatus.BAD_REQUEST,
                        {"error": {"code": "invalid_length", "message": "Content-Length is invalid"}},
                    )
                )
                return True
            if content_length > MAX_API_BODY:
                body_too_large = True
                self.close_connection = True
            else:
                body = self.rfile.read(content_length)

        forwarded = self.headers.get("X-Forwarded-Proto", "").lower()
        scheme = forwarded if forwarded in {"http", "https"} else "http"
        host = self.headers.get("Host", "")
        if not HOST_RE.fullmatch(host):
            self._send_api_response(
                ApiResponse(
                    HTTPStatus.BAD_REQUEST,
                    {"error": {"code": "invalid_host", "message": "Host header is invalid"}},
                )
            )
            return True
        request = ApiRequest(
            method=method,
            path=path,
            headers={key: value for key, value in self.headers.items()},
            body=body,
            remote_address=self._remote_address(),
            scheme=scheme,
            host=host,
            body_too_large=body_too_large,
        )
        self._send_api_response(application.dispatch(request))
        return True

    def _remote_address(self) -> str:
        forwarded = self.headers.get("X-Real-IP", "").strip()
        if forwarded and re.fullmatch(r"[0-9A-Fa-f:.]{3,64}", forwarded):
            return forwarded
        return str(self.client_address[0])

    def _send_api_response(self, response: ApiResponse) -> None:
        payload = json.dumps(response.payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(int(response.status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        for name, value in response.headers.items():
            self.send_header(name, value)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlsplit(self.path).path
        if self._api_request("GET", path):
            return
        if path == "/":
            self._redirect_to_app()
            return
        if path == "/healthz":
            self._send_health(include_body=True)
            return
        super().do_GET()

    def copyfile(self, source: object, outputfile: object) -> None:
        """Treat a client closing a static response early as a normal disconnect."""

        try:
            super().copyfile(source, outputfile)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlsplit(self.path).path
        if path.startswith("/api/"):
            self._method_not_allowed(api=True)
            return
        if path == "/":
            self._redirect_to_app()
            return
        if path == "/healthz":
            self._send_health(include_body=False)
            return
        super().do_HEAD()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlsplit(self.path).path
        if self._api_request("POST", path):
            return
        self._method_not_allowed()

    def do_PUT(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlsplit(self.path).path
        if self._api_request("PUT", path):
            return
        self._method_not_allowed()

    do_PATCH = do_PUT
    do_DELETE = do_PUT
    do_OPTIONS = do_PUT

    def list_directory(self, path: str) -> Any:
        self.send_error(HTTPStatus.NOT_FOUND, "Directory listing is disabled")
        return None

    def log_message(self, format_string: str, *args: object) -> None:
        sys.stderr.write(
            f"{self.log_date_time_string()} {self.client_address[0]} "
            f"{format_string % args}\n"
        )


class DemoServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler: type[SimpleHTTPRequestHandler],
        *,
        control_plane: ControlPlane | None = None,
        model_configured: bool = False,
        workspace_model_call_limit: int = 12,
        global_model_call_limit_per_hour: int = 120,
        address_model_call_limit_per_hour: int = 24,
    ) -> None:
        self.control_plane = control_plane
        self.database_ready = control_plane is not None
        self.model_configured = model_configured
        self.api = (
            ApiApplication(
                control_plane,
                workspace_model_call_limit=workspace_model_call_limit,
                global_model_call_limit_per_hour=global_model_call_limit_per_hour,
                address_model_call_limit_per_hour=address_model_call_limit_per_hour,
            )
            if control_plane is not None
            else None
        )
        self._scheduler_stop = threading.Event()
        self._scheduler_thread: threading.Thread | None = None
        super().__init__(server_address, request_handler)
        if control_plane is not None:
            self._scheduler_thread = threading.Thread(
                target=self._schedule_loop,
                name="kyn-schedule-pump",
                daemon=True,
            )
            self._scheduler_thread.start()

    def _schedule_loop(self) -> None:
        while not self._scheduler_stop.wait(2.0):
            try:
                assert self.control_plane is not None
                self.control_plane.fire_due_studio_schedules()
            except Exception:
                # A schedule fault must not take down the HTTP control plane.
                time.sleep(0.1)

    def server_close(self) -> None:
        self._scheduler_stop.set()
        if self._scheduler_thread is not None:
            self._scheduler_thread.join(timeout=2.5)
        super().server_close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve Kyn.ist Agent Studio."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind address.")
    parser.add_argument("--port", default=4173, type=int, help="TCP port, or 0 for ephemeral.")
    parser.add_argument(
        "--database",
        default=None,
        help="SQLite path (default: KYN_DATABASE_PATH or var/kyn-agent-studio.sqlite3).",
    )
    parser.add_argument("--model", default=None, help="OpenAI model (default: OPENAI_MODEL or gpt-5.6).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not APP_ENTRY.is_file():
        print(f"error: application entry is missing: {APP_ENTRY}", file=sys.stderr)
        return 2
    if not 0 <= args.port <= 65535:
        print("error: --port must be between 0 and 65535", file=sys.stderr)
        return 2

    load_env_file(ROOT / ".env")
    database_path = Path(
        args.database or os.environ.get("KYN_DATABASE_PATH", str(DEFAULT_DATABASE))
    )
    model = args.model or os.environ.get("OPENAI_MODEL", "gpt-5.6")
    store = Store(database_path)
    try:
        store.initialize()
        client = UnavailableResponsesClient()
        control_plane = ControlPlane(
            store,
            client,
            default_model=model,
            client_factory=lambda browser_key: ResponsesClient(browser_key),
        )
    except (OSError, ContractViolation, ProviderFailure) as error:
        print(f"error: runtime initialization failed: {error}", file=sys.stderr)
        return 1

    handler = partial(DemoRequestHandler, directory=str(ROOT))
    try:
        server = DemoServer(
            (args.host, args.port),
            handler,
            control_plane=control_plane,
            model_configured=False,
            workspace_model_call_limit=int(os.environ.get("KYN_WORKSPACE_MODEL_CALL_LIMIT", "24")),
            global_model_call_limit_per_hour=int(
                os.environ.get("KYN_PUBLIC_MODEL_CALLS_PER_HOUR", "120")
            ),
            # The per-address hour cap has to be configurable alongside the other
            # two budgets, and it must not sit below the per-workspace budget:
            # a workspace granted N calls that one address may only spend 24 of
            # per hour has a budget it cannot actually spend. Leaving this one
            # unreachable from configuration is what let the deployed workspace
            # limit be raised past a ceiling nobody could see.
            address_model_call_limit_per_hour=int(
                os.environ.get("KYN_ADDRESS_MODEL_CALLS_PER_HOUR", "24")
            ),
        )
    except (OSError, ValueError) as error:
        print(f"error: cannot bind {args.host}:{args.port}: {error}", file=sys.stderr)
        return 1

    bound_host, bound_port = server.server_address[:2]
    display_host = "127.0.0.1" if bound_host in {"0.0.0.0", "::"} else bound_host
    print(f"Kyn.ist Agent Studio: http://{display_host}:{bound_port}/app/", flush=True)
    print(
        "SQLite runtime · browser-session BYOK · official OpenAI Responses SDK · Ctrl-C to stop",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nStopping runtime server.", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
