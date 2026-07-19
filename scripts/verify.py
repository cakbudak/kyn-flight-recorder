#!/usr/bin/env python3
"""Run the standalone runtime's reproducible verification gates."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(label: str, command: list[str]) -> None:
    print(f"\n[{label}] {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=ROOT, check=True)  # noqa: S603 - repository-owned commands


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--browser",
        action="store_true",
        help="also execute the complete deterministic Chromium journey",
    )
    parser.add_argument(
        "--performance",
        action="store_true",
        help="also execute the 64-node runtime and Chromium load gates",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run(
        "python",
        [
            sys.executable,
            "-W",
            "error::ResourceWarning",
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_*.py",
            "-v",
        ],
    )

    node = shutil.which("node")
    if node is None:
        print("\n[node] required for browser-state tests but not found", file=sys.stderr)
        return 2
    run("node-state", [node, "--test", "tests/core.test.mjs"])

    if args.browser or args.performance:
        chromium = shutil.which("chromium") or shutil.which("chromium-browser")
        if chromium is None:
            print("\n[browser] requested verification needs Chromium", file=sys.stderr)
            return 2
    if args.browser:
        run("chromium", [node, "scripts/browser_verify.mjs"])
    if args.performance:
        run("runtime-load", [sys.executable, "scripts/performance_verify.py"])
        run("editor-load", [node, "scripts/editor_load_verify.mjs"])

    print("\nPASS: runtime, database, HTTP, security, server, UI, and requested load contracts are green.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
