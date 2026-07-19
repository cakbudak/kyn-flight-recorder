#!/usr/bin/env python3
"""Serve the real HTTP/UI stack with a deterministic provider-shaped test seam."""

from __future__ import annotations

import argparse
from functools import partial
from pathlib import Path

from backend.service import ControlPlane
from backend.store import Store
from serve import DemoRequestHandler, DemoServer, ROOT
from tests.test_runtime_contract import ScriptedResponsesClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--database", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    store = Store(args.database)
    store.initialize()
    client = ScriptedResponsesClient(store)
    plane = ControlPlane(store, client)
    handler = partial(DemoRequestHandler, directory=str(ROOT))
    server = DemoServer(
        (args.host, args.port),
        handler,
        control_plane=plane,
        model_configured=True,
        workspace_model_call_limit=16,
    )
    print(f"Browser test runtime: http://{args.host}:{server.server_port}/app/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
