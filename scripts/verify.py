#!/usr/bin/env python3
"""Run all dependency-free verification gates for the standalone cut."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(label: str, command: list[str]) -> None:
    print(f"\n[{label}] {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=ROOT, check=True)  # noqa: S603 - fixed repository commands


def main() -> int:
    run("python", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"])

    node = shutil.which("node")
    if node is None:
        print("\n[node] required for the JavaScript contract tests but not found", file=sys.stderr)
        return 2
    run("node", [node, "--test", "tests/core.test.mjs"])

    print("\nPASS: Python server/static gates and JavaScript state-machine gates are green.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
