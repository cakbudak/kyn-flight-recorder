#!/usr/bin/env python3
"""Run the guard ablation suite and report whether every guard is load-bearing.

This is a repository verification artifact. It is not reachable from `serve.py`,
the `/api/v1` HTTP API, or any runtime module: ablation exists only inside
`tests/test_guard_ablation.py`, which recompiles a product function with one
guard expression removed for the duration of a `with` block, or drops a trigger
on a throwaway SQLite file. A deployed Kyn.ist Agent Studio contains no switch
that turns off its own authority gate.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import test_guard_ablation as suite  # noqa: E402


RULE = "─"


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    lines.append(current)
    return lines or [""]


def _render(outcomes: list[suite.GuardOutcome]) -> str:
    columns = (
        ("GUARD", 30, lambda item: item.guard),
        ("VIOLATION IT PREVENTS", 38, lambda item: item.violation),
        ("BASELINE (guard intact)", 40, lambda item: item.baseline),
        ("ABLATED (guard removed)", 40, lambda item: item.ablated),
    )
    total = sum(width for _, width, _ in columns) + 3 * (len(columns) - 1)
    out: list[str] = []
    out.append("")
    out.append("GUARD ABLATION — is each guard the reason the system holds?")
    out.append(RULE * total)
    out.append(
        "   ".join(title.ljust(width) for title, width, _ in columns)
    )
    out.append(RULE * total)
    for item in outcomes:
        cells = [_wrap(getter(item), width) for _, width, getter in columns]
        height = max(len(cell) for cell in cells)
        for row in range(height):
            out.append(
                "   ".join(
                    (cell[row] if row < len(cell) else "").ljust(width)
                    for cell, (_, width, _) in zip(cells, columns)
                ).rstrip()
            )
        verdict = (
            "LOAD-BEARING"
            if item.load_bearing
            else "REDUNDANT (property still enforced elsewhere)"
            if item.redundancy_probe
            else "DECORATIVE — ABLATION CHANGED NOTHING"
        )
        out.append(f"   → {verdict}")
        out.append(f"     site: {item.site}")
        if item.note:
            for line in _wrap(f"note: {item.note}", total - 5):
                out.append(f"     {line}")
        out.append(RULE * total)
    return "\n".join(out)


def main() -> int:
    loader = unittest.TestLoader()
    tests = loader.loadTestsFromModule(suite)
    result = unittest.TextTestRunner(verbosity=2).run(tests)

    outcomes = suite.ordered_outcomes()
    print(_render(outcomes))

    if not result.wasSuccessful():
        print("\nFAIL: a guard ablation experiment did not reach its verdict.")
        return 1
    if not outcomes:
        print("\nFAIL: the ablation suite recorded no guard outcomes.")
        return 1

    decorative = [
        item for item in outcomes if not item.load_bearing and not item.redundancy_probe
    ]
    if decorative:
        for item in decorative:
            print(f"\nFAIL: {item.guard} is decorative — ablating it changed nothing.")
        return 1

    load_bearing = sum(1 for item in outcomes if item.load_bearing)
    redundant = sum(1 for item in outcomes if item.redundancy_probe)
    print(
        f"\nPASS: {load_bearing} of {len(outcomes)} ablations made a documented "
        f"product-level violation reachable; {redundant} reported as redundant "
        "with the property still enforced."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
