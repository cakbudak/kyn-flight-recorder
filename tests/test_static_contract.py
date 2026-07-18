from __future__ import annotations

import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


class SurfaceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.inline_scripts = 0
        self.inline_styles = 0
        self.buttons_without_type: list[int] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "script" and "src" not in values:
            self.inline_scripts += 1
        if tag == "style":
            self.inline_styles += 1
        if tag == "button" and "type" not in values:
            self.buttons_without_type.append(self.getpos()[0])


class StaticContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = (APP / "index.html").read_text(encoding="utf-8")
        cls.client = (APP / "app.mjs").read_text(encoding="utf-8")
        cls.core = (APP / "core.mjs").read_text(encoding="utf-8")
        cls.styles = (APP / "styles.css").read_text(encoding="utf-8")
        cls.fixture = json.loads((APP / "data" / "demo-run.json").read_text(encoding="utf-8"))
        cls.schema = json.loads(
            (ROOT / "schema" / "kyn-flight-trace-v1.schema.json").read_text(encoding="utf-8")
        )

    def test_application_has_no_remote_asset_or_script_dependency(self) -> None:
        remote_asset = re.compile(r"(?:src|href)=[\"']https?://", re.IGNORECASE)
        self.assertIsNone(remote_asset.search(self.index))
        self.assertNotIn("@import url", self.styles)
        self.assertNotIn("node_modules", self.index)

    def test_content_security_policy_can_reject_inline_code(self) -> None:
        parser = SurfaceParser()
        parser.feed(self.index)
        self.assertEqual(parser.inline_scripts, 0)
        self.assertEqual(parser.inline_styles, 0)

    def test_every_button_has_an_explicit_type(self) -> None:
        parser = SurfaceParser()
        parser.feed(self.index)
        self.assertEqual(parser.buttons_without_type, [])

    def test_dynamic_fixture_values_never_enter_html_parsing_sinks(self) -> None:
        forbidden = (".innerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function")
        combined = f"{self.client}\n{self.core}"
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, combined)

    def test_client_fetches_only_the_local_fixture(self) -> None:
        fetch_calls = re.findall(r"fetch\(([^\n]+)", self.client)
        self.assertEqual(len(fetch_calls), 1)
        self.assertIn("FIXTURE_URL", fetch_calls[0])

    def test_motion_rules_are_bounded_and_reduced_motion_is_present(self) -> None:
        self.assertNotRegex(self.styles, r"transition\s*:\s*all\b")
        self.assertNotIn("scale(0)", self.styles)
        self.assertIn("prefers-reduced-motion: reduce", self.styles)
        durations = [int(value) for value in re.findall(r"(\d+)ms", self.styles)]
        ui_durations = [duration for duration in durations if duration > 1]
        self.assertTrue(ui_durations)
        self.assertLessEqual(max(ui_durations), 3200, "toast residence may be longer; UI transitions may not")
        transition_durations = [
            int(value)
            for value in re.findall(r"(?:transition[^;]*?|duration:)\s*(?:[^;]*?\s)?(\d+)ms", self.styles)
        ]
        self.assertTrue(transition_durations)
        self.assertLessEqual(max(transition_durations), 300)

    def test_text_palette_meets_small_text_contrast_on_the_brightest_surface(self) -> None:
        def luminance(color: str) -> float:
            channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
            linear = [
                channel / 12.92
                if channel <= 0.04045
                else ((channel + 0.055) / 1.055) ** 2.4
                for channel in channels
            ]
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

        def contrast(foreground: str, background: str) -> float:
            light, dark = sorted(
                (luminance(foreground), luminance(background)), reverse=True
            )
            return (light + 0.05) / (dark + 0.05)

        evaluated_surfaces = {
            "brightest regular surface": "#191e24",
            "brightest diagnosis tint": "#282621",
        }
        palette = {
            "text": "#f0f2f3",
            "text-soft": "#c0c6cc",
            "muted": "#858e98",
            "muted-strong": "#9ea6ae",
            "source": "#929ba4",
        }
        for name, foreground in palette.items():
            for surface_name, background in evaluated_surfaces.items():
                with self.subTest(name=name, surface=surface_name):
                    self.assertGreaterEqual(contrast(foreground, background), 4.5)

    def test_accessibility_landmarks_and_live_feedback_exist(self) -> None:
        for fragment in (
            'class="skip-link"',
            'id="main-content"',
            'aria-live="polite"',
            'prefers-reduced-motion',
            'aria-labelledby="dialog-title"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, f"{self.index}\n{self.styles}")

    def test_fixture_stays_small_synthetic_and_network_inert(self) -> None:
        fixture_path = APP / "data" / "demo-run.json"
        self.assertLess(fixture_path.stat().st_size, 1024 * 1024)
        self.assertEqual(self.fixture["fixture"]["classification"], "synthetic_demo")
        self.assertFalse(self.fixture["run"]["impact"]["external_effect"])
        self.assertFalse(self.fixture["intervention"]["resolution"]["events"][1]["detail"]["external_effect"])

    def test_runtime_has_one_entry_and_one_fixture(self) -> None:
        self.assertTrue((ROOT / "serve.py").is_file())
        self.assertEqual([path.name for path in (APP / "data").glob("*.json")], ["demo-run.json"])
        scripts = re.findall(r'<script[^>]+src="([^"]+)"', self.index)
        self.assertEqual(scripts, ["./app.mjs"])

    def test_machine_readable_schema_matches_the_v1_top_level_envelope(self) -> None:
        self.assertEqual(self.schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(self.schema["properties"]["schema_version"]["const"], "1.0")
        self.assertFalse(self.schema["additionalProperties"])
        self.assertEqual(set(self.schema["required"]), set(self.fixture))
        self.assertEqual(self.schema["$defs"]["run"]["properties"]["impact"]["properties"]["external_effect"]["const"], False)

    def test_runtime_uses_the_machine_readable_schema_as_its_structural_authority(self) -> None:
        self.assertIn(
            'import TRACE_SCHEMA from "../schema/kyn-flight-trace-v1.schema.json"',
            self.core,
        )
        self.assertIn("validateSchemaValue(input, TRACE_SCHEMA", self.core)


if __name__ == "__main__":
    unittest.main()
