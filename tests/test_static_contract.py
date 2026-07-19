from __future__ import annotations

import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
SRC = ROOT / "src"


class SurfaceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.inline_scripts = 0
        self.inline_styles = 0
        self.remote_assets: list[str] = []
        self.scripts: list[str] = []
        self.stylesheets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "script":
            source = values.get("src")
            if source is None:
                self.inline_scripts += 1
            else:
                self.scripts.append(source)
        if tag == "style":
            self.inline_styles += 1
        if tag == "link" and values.get("rel") == "stylesheet":
            self.stylesheets.append(values.get("href") or "")
        asset = values.get("src") or (values.get("href") if tag == "link" else None)
        if asset and asset.startswith(("http://", "https://")):
            self.remote_assets.append(asset)


class StaticContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = (APP / "index.html").read_text(encoding="utf-8")
        cls.sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(SRC.rglob("*"))
            if path.suffix in {".js", ".jsx"}
        )
        cls.styles = (SRC / "styles.css").read_text(encoding="utf-8")
        cls.bundle = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((APP / "assets").glob("*.js"))
        )
        cls.nginx = (ROOT / "deploy" / "nginx-buildweek.conf").read_text(
            encoding="utf-8"
        )
        cls.service = (ROOT / "deploy" / "kyn-agent-studio.service").read_text(
            encoding="utf-8"
        )
        cls.user_service = (
            ROOT / "deploy" / "kyn-agent-studio-user.service"
        ).read_text(encoding="utf-8")

    def test_active_product_is_agent_studio_with_professional_workbenches(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        combined = f"{self.sources}\n{readme}"
        self.assertIn("Kyn.ist Agent Studio", combined)
        self.assertNotIn("Kyn.ist Flight Recorder", combined)
        for phrase in (
            "Flow Studio",
            "Actions",
            "Agents",
            "Prompts",
            "Skills",
            "Authoritative operations console",
            "Publish successor",
            "Outcome routes",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, combined)

    def test_built_application_is_self_hosted_and_csp_compatible(self) -> None:
        parser = SurfaceParser()
        parser.feed(self.index)
        self.assertEqual(parser.remote_assets, [])
        self.assertEqual(parser.inline_scripts, 0)
        self.assertEqual(parser.inline_styles, 0)
        self.assertEqual(len(parser.scripts), 1)
        self.assertGreaterEqual(len(parser.stylesheets), 1)
        self.assertRegex(parser.scripts[0], r"^/app/assets/studio-[A-Za-z0-9_-]+\.js$")
        for stylesheet in parser.stylesheets:
            self.assertRegex(
                stylesheet, r"^/app/assets/studio-[A-Za-z0-9_-]+\.css$"
            )
        self.assertNotIn("node_modules", self.index)
        self.assertNotIn("@import url", self.styles)

    def test_browser_uses_safe_react_rendering_and_no_html_parsing_sink(self) -> None:
        for token in (
            ".innerHTML",
            "dangerouslySetInnerHTML",
            "insertAdjacentHTML",
            "document.write",
            "eval(",
            "new Function",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, self.sources)

    def test_browser_calls_only_same_origin_bounded_routes(self) -> None:
        api_source = (SRC / "api.js").read_text(encoding="utf-8")
        self.assertIn('path.startsWith("/api/v1/")', api_source)
        self.assertIn('fetch("/healthz"', api_source)
        self.assertNotRegex(api_source, r"fetch\([\"']https?://")
        for route in (
            "/api/v1/workspaces",
            "/api/v1/workspace",
            "/studio/actions/",
            "/studio/flows/",
            "/studio/runs/",
            "/diagnoses",
            "/repairs",
            "/proof",
            "/approvals/",
            "/triggers",
        ):
            with self.subTest(route=route):
                self.assertIn(route, self.sources)

    def test_workspace_cookie_and_openai_key_have_separate_authority(self) -> None:
        api_source = (SRC / "api.js").read_text(encoding="utf-8")
        self.assertNotIn("localStorage", self.sources)
        self.assertNotIn("document.cookie", self.sources)
        self.assertIn("sessionStorage", api_source)
        self.assertIn('OPENAI_KEY_SLOT = "kyn.openai.api-key.v1"', api_source)
        self.assertIn('credentials: "same-origin"', api_source)
        self.assertIn('headers["X-OpenAI-API-Key"]', api_source)
        self.assertIn('keyMode === "required"', api_source)
        self.assertIn("Use a restricted, temporary project key", self.sources)
        self.assertIn("developers.openai.com/api/reference/overview#authentication", self.sources)
        self.assertNotIn("OPENAI_API_KEY", self.bundle)
        self.assertNotRegex(self.bundle, r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{20,}")

    def test_flow_surface_has_real_reactflow_ports_subflows_and_full_canvas_controls(self) -> None:
        flow_source = (SRC / "components" / "FlowStudio.jsx").read_text(
            encoding="utf-8"
        )
        for contract in (
            "@xyflow/react",
            "MiniMap",
            "Controls",
            "screenToFlowPosition",
            "sourceHandle",
            "targetHandle",
            "nodeOutcomes",
            'type: "flow"',
            "Auto layout",
            "Hide node library",
            "Hide inspector",
            "Publish successor",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, flow_source)

    def test_all_first_class_resources_expose_successor_endpoints(self) -> None:
        for endpoint in (
            "/studio/actions/${resource.id}/versions",
            "/prompts/${resource.id}/versions",
            "/skills/${resource.id}/versions",
            "/agents/${resource.id}/versions",
            "/studio/flows/${draft.id}/versions",
        ):
            with self.subTest(endpoint=endpoint):
                self.assertIn(endpoint, self.sources)

    def test_run_console_exposes_evidence_approval_lineage_and_maintenance(self) -> None:
        for phrase in (
            "Steps",
            "Timeline",
            "OpenAI",
            "Receipts",
            "Effects",
            "Maintenance",
            "Human gate",
            "Linked rerun",
            "Evidence → diagnosis → successor → proof",
            "Run lineage",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.sources)

    def test_live_documentation_names_security_runtime_and_private_boundary(self) -> None:
        for phrase in (
            "Your OpenAI key lives only in this browser tab",
            "never written to SQLite",
            "Official OpenAI Responses SDK",
            "A published Flow is a first-class node",
            "A node can own up to twelve outputs",
            "Diagnose, approve a successor, then prove it",
            "Parts/Entities",
            "Bricks/Packs/Frames",
            "Ainou",
            "CE",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.sources)

    def test_motion_is_bounded_reduced_and_focus_is_visible(self) -> None:
        self.assertNotRegex(self.styles, r"transition\s*:\s*all\b")
        self.assertNotIn("scale(0)", self.styles)
        self.assertIn("prefers-reduced-motion: reduce", self.styles)
        self.assertIn(":focus-visible", self.styles)
        transition_durations = [
            int(value)
            for value in re.findall(
                r"(?:transition[^;]*?|duration:)\s*(?:[^;]*?\s)?(\d+)ms",
                self.styles,
            )
        ]
        self.assertTrue(transition_durations)
        self.assertLessEqual(max(transition_durations), 320)

    def test_accessibility_landmarks_dialogs_and_feedback_exist(self) -> None:
        for fragment in (
            "skip-link",
            'id="main-content"',
            'role="dialog"',
            'aria-modal="true"',
            "aria-describedby",
            'role="status"',
            'role="alert"',
            "prefers-reduced-motion",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, f"{self.sources}\n{self.styles}")

    def test_server_has_no_operator_key_fallback_and_uses_official_sdk(self) -> None:
        server = (ROOT / "serve.py").read_text(encoding="utf-8")
        transport = (ROOT / "backend" / "openai_client.py").read_text(
            encoding="utf-8"
        )
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertNotIn('os.environ.get("OPENAI_API_KEY"', server)
        self.assertNotIn('"OPENAI_API_KEY",', transport)
        self.assertNotIn("urllib", transport)
        self.assertRegex(transport, r"(?m)^from openai import ")
        self.assertRegex(requirements, r"(?m)^openai==[0-9]+\.[0-9]+\.[0-9]+$")

    def test_deployment_matches_256k_api_and_hardened_same_origin_service(self) -> None:
        self.assertIn("proxy_pass http://host.docker.internal:4173", self.nginx)
        self.assertIn("proxy_set_header X-Forwarded-Proto", self.nginx)
        self.assertIn("client_max_body_size 256k", self.nginx)
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
        self.assertIn("StateDirectory=kyn-agent-studio", self.service)
        self.assertIn(
            "ReadWritePaths=/opt/server/projects/buildweek.kyn.ist/var",
            self.user_service,
        )


if __name__ == "__main__":
    unittest.main()
