from __future__ import annotations

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
        self.remote_executable_assets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "script" and "src" not in values:
            self.inline_scripts += 1
        if tag == "style":
            self.inline_styles += 1
        if tag == "button" and "type" not in values:
            self.buttons_without_type.append(self.getpos()[0])
        asset = values.get("src") or (values.get("href") if tag == "link" else None)
        if asset and asset.startswith(("http://", "https://")):
            self.remote_executable_assets.append(asset)


class StaticContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = (APP / "index.html").read_text(encoding="utf-8")
        cls.client = (APP / "app.mjs").read_text(encoding="utf-8")
        cls.state = (APP / "state.mjs").read_text(encoding="utf-8")
        cls.styles = (APP / "styles.css").read_text(encoding="utf-8")
        cls.nginx = (ROOT / "deploy" / "nginx-buildweek.conf").read_text(encoding="utf-8")
        cls.service = (ROOT / "deploy" / "kyn-flight-recorder.service").read_text(
            encoding="utf-8"
        )
        cls.user_service = (
            ROOT / "deploy" / "kyn-flight-recorder-user.service"
        ).read_text(encoding="utf-8")

    def test_active_product_is_kyn_agent_studio_not_a_recorder_demo(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for surface in (self.index, readme):
            self.assertIn("Kyn.ist Agent Studio", surface)
            self.assertNotIn("Kyn.ist Flight Recorder", surface)
        for phrase in (
            "Define Actions",
            "Build Flows",
            "Observe Runs",
            "Human approval",
            "Versioned Agents, Prompts, and Skills",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.index)

    def test_application_has_no_remote_executable_or_style_dependency(self) -> None:
        parser = SurfaceParser()
        parser.feed(self.index)
        self.assertEqual(parser.remote_executable_assets, [])
        self.assertNotIn("@import url", self.styles)
        self.assertNotIn("node_modules", self.index)
        self.assertIn("https://github.com/cakbudak/kyn-flight-recorder", self.index)

    def test_content_security_policy_can_reject_inline_code(self) -> None:
        parser = SurfaceParser()
        parser.feed(self.index)
        self.assertEqual(parser.inline_scripts, 0)
        self.assertEqual(parser.inline_styles, 0)

    def test_every_button_has_an_explicit_type(self) -> None:
        parser = SurfaceParser()
        parser.feed(self.index)
        self.assertEqual(parser.buttons_without_type, [])

    def test_server_values_never_enter_html_parsing_sinks(self) -> None:
        forbidden = (".innerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function")
        combined = f"{self.client}\n{self.state}"
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, combined)

    def test_browser_calls_only_same_origin_runtime_routes(self) -> None:
        combined = f"{self.client}\n{self.state}"
        self.assertNotRegex(combined, r"fetch\([\"']https?://")
        self.assertIn('fetch("/healthz"', self.client)
        for route in (
            "/api/v1/workspaces",
            "/api/v1/workspace",
            "/runs",
            "/diagnoses",
            "/repairs",
            "/apply",
            "/rerun",
            "/api/v1/studio",
            "/api/v1/studio/actions",
            "/api/v1/studio/flows",
            "/api/v1/studio/runs",
            "/approvals",
        ):
            with self.subTest(route=route):
                self.assertIn(route, self.client)

    def test_workspace_authority_stays_in_the_httponly_cookie(self) -> None:
        combined = f"{self.client}\n{self.state}"
        self.assertNotIn("localStorage", combined)
        self.assertNotIn("document.cookie", combined)
        self.assertIn("sessionStorage", self.client)
        self.assertIn('const OPENAI_KEY_SLOT = "kyn.openai.api-key.v1"', self.client)
        self.assertIn('credentials: "same-origin"', self.client)

    def test_browser_key_is_attached_only_to_same_origin_model_actions(self) -> None:
        self.assertIn('"X-OpenAI-API-Key"', self.client)
        self.assertIn("modelAction = false", self.client)
        for route in ("/runs`,", "/diagnoses`,", "/repairs`,", "/rerun`,"):
            with self.subTest(route=route):
                start = self.client.index(route)
                call = self.client[start : start + 180]
                self.assertIn("modelAction: true", call)
        apply_start = self.client.index("/apply`,")
        self.assertNotIn("modelAction: true", self.client[apply_start : apply_start + 260])

    def test_configuration_and_live_documentation_explain_the_real_boundary(self) -> None:
        for phrase in (
            "Use your own OpenAI API key",
            "this browser tab only",
            "never written to SQLite",
            "official OpenAI SDK",
            "What actually happens",
            "Why this is more than a trace viewer",
            "What is deliberately bounded",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.index)
        for control in (
            'id="config-view"',
            'id="openai-api-key"',
            'type="password"',
            'id="save-api-key"',
            'id="clear-api-key"',
            'id="open-config"',
        ):
            with self.subTest(control=control):
                self.assertIn(control, self.index)

    def test_motion_rules_are_bounded_and_reduced_motion_is_present(self) -> None:
        self.assertNotRegex(self.styles, r"transition\s*:\s*all\b")
        self.assertNotIn("scale(0)", self.styles)
        self.assertIn("prefers-reduced-motion: reduce", self.styles)
        transition_durations = [
            int(value)
            for value in re.findall(
                r"(?:transition[^;]*?|duration:)\s*(?:[^;]*?\s)?(\d+)ms", self.styles
            )
        ]
        self.assertTrue(transition_durations)
        self.assertLessEqual(max(transition_durations), 300)

    def test_text_palette_meets_small_text_contrast_on_brightest_surface(self) -> None:
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
            light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
            return (light + 0.05) / (dark + 0.05)

        for name, foreground in {
            "text": "#f2f0ea",
            "text-soft": "#c3c7c9",
            "muted": "#929aa1",
        }.items():
            with self.subTest(name=name):
                self.assertGreaterEqual(contrast(foreground, "#1a2026"), 4.5)

    def test_accessibility_landmarks_and_live_feedback_exist(self) -> None:
        combined = f"{self.index}\n{self.styles}"
        for fragment in (
            'class="skip-link"',
            'id="main-content"',
            'aria-live="polite"',
            'aria-live="assertive"',
            'prefers-reduced-motion',
            'aria-labelledby="dialog-title"',
            'aria-describedby="dialog-description"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, combined)

    def test_first_class_agent_inputs_and_closed_loop_are_visible(self) -> None:
        for phrase in (
            "Agents, prompts, skills",
            "OpenAI Responses + strict tools",
            "Hash-linked SQLite events",
            "Human revision fence",
            "Child rerun + real receipt",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.index)

    def test_active_browser_surface_has_no_legacy_graph_or_fixture_model(self) -> None:
        combined = f"{self.index}\n{self.client}\n{self.state}"
        for legacy in ("demo-run.json", "core.mjs", "graph-node", "fixture"):
            with self.subTest(legacy=legacy):
                self.assertNotIn(legacy, combined)

    def test_runtime_has_one_script_entry_and_one_pure_state_module(self) -> None:
        scripts = re.findall(r'<script[^>]+src="([^"]+)"', self.index)
        self.assertEqual(scripts, ["./app.mjs"])
        self.assertIn('from "./state.mjs"', self.client)

    def test_secret_material_is_absent_from_the_browser_bundle(self) -> None:
        combined = f"{self.index}\n{self.client}\n{self.state}\n{self.styles}"
        self.assertNotIn("OPENAI_API_KEY", combined)
        self.assertNotRegex(combined, r"sk-[A-Za-z0-9_-]{12,}")

    def test_server_has_no_operator_api_key_fallback_and_uses_the_official_sdk(self) -> None:
        server = (ROOT / "serve.py").read_text(encoding="utf-8")
        transport = (ROOT / "backend" / "openai_client.py").read_text(encoding="utf-8")
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertNotIn('os.environ.get("OPENAI_API_KEY"', server)
        self.assertNotIn('"OPENAI_API_KEY",', transport)
        self.assertNotIn("urllib", transport)
        self.assertRegex(transport, r"^from openai import ", msg="official SDK must own transport")
        self.assertRegex(requirements, r"(?m)^openai==[0-9]+\.[0-9]+\.[0-9]+$")
        self.assertNotIn("OPENAI_API_KEY", env_example)

    def test_deployment_proxies_the_same_origin_api_to_a_hardened_service(self) -> None:
        self.assertIn("proxy_pass http://host.docker.internal:4173", self.nginx)
        self.assertIn("proxy_set_header X-Forwarded-Proto", self.nginx)
        self.assertIn("client_max_body_size 32k", self.nginx)
        self.assertNotIn("GET|HEAD", self.nginx)
        for directive in (
            "--host 172.17.0.1 --port 4173",
            ".venv/bin/python",
            "NoNewPrivileges=true",
            "ProtectSystem=strict",
            "UMask=0077",
        ):
            with self.subTest(directive=directive):
                self.assertIn(directive, self.service)
                self.assertIn(directive, self.user_service)
        self.assertIn("StateDirectory=kyn-flight-recorder", self.service)
        self.assertIn("ReadWritePaths=/opt/server/projects/buildweek.kyn.ist/var", self.user_service)
        self.assertNotIn("User=", self.user_service)
        self.assertIn("WantedBy=default.target", self.user_service)

    def test_superseded_static_runtime_files_are_absent(self) -> None:
        for relative in (
            "app/core.mjs",
            "app/data/demo-run.json",
            "schema/kyn-flight-trace-v1.schema.json",
            "scripts/gpt56_review.py",
        ):
            with self.subTest(relative=relative):
                self.assertFalse((ROOT / relative).exists())


if __name__ == "__main__":
    unittest.main()
