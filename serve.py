#!/usr/bin/env python3
"""Serve the standalone Kyn.ist Flight Recorder with no third-party packages."""

from __future__ import annotations

import argparse
import json
import sys
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
APP_ENTRY = ROOT / "app" / "index.html"

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
        "form-action 'none'; "
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
    """Read-only static handler with explicit security and cache headers."""

    server_version = "KynFlightRecorder/1.0"

    def end_headers(self) -> None:
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlsplit(self.path).path
        if path == "/":
            self.send_response(HTTPStatus.TEMPORARY_REDIRECT)
            self.send_header("Location", "/app/")
            self.end_headers()
            return
        if path == "/healthz":
            payload = json.dumps(
                {
                    "status": "ok",
                    "mode": "standalone-demo",
                    "external_dependencies": 0,
                },
                separators=(",", ":"),
            ).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        super().do_GET()

    def list_directory(self, path: str):  # type: ignore[no-untyped-def]
        self.send_error(HTTPStatus.NOT_FOUND, "Directory listing is disabled")
        return None

    def log_message(self, format_string: str, *args: object) -> None:
        sys.stderr.write(
            f"{self.log_date_time_string()} {self.address_string()} "
            f"{format_string % args}\n"
        )


class DemoServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve Kyn.ist Flight Recorder from the local repository."
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        default=4173,
        type=int,
        help="TCP port, or 0 for an ephemeral port (default: 4173).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not APP_ENTRY.is_file():
        print(f"error: application entry is missing: {APP_ENTRY}", file=sys.stderr)
        return 2
    if not 0 <= args.port <= 65535:
        print("error: --port must be between 0 and 65535", file=sys.stderr)
        return 2

    handler = partial(DemoRequestHandler, directory=str(ROOT))
    try:
        server = DemoServer((args.host, args.port), handler)
    except OSError as error:
        print(f"error: cannot bind {args.host}:{args.port}: {error}", file=sys.stderr)
        return 1

    bound_host, bound_port = server.server_address[:2]
    display_host = "127.0.0.1" if bound_host in {"0.0.0.0", "::"} else bound_host
    print(f"Kyn.ist Flight Recorder: http://{display_host}:{bound_port}/app/", flush=True)
    print("Synthetic local demo · no external services · Ctrl-C to stop", flush=True)

    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nStopping demo server.", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
