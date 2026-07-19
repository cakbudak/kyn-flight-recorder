from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.contracts import ContractViolation, render_prompt
from backend.service import ControlPlane
from backend.store import Store

from tests.test_runtime_contract import ScriptedResponsesClient


class ResourceContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name) / "resources.sqlite3")
        self.store.initialize()
        self.plane = ControlPlane(self.store, ScriptedResponsesClient(self.store))
        bootstrap = self.plane.create_workspace(seed=False)
        self.workspace_id = bootstrap["workspace_id"]

    def test_prompt_renderer_is_exact_and_bounded(self) -> None:
        rendered = render_prompt(
            "Ship {{artifact}} to {{environment}}.",
            declared_variables=["artifact", "environment"],
            values={"artifact": "kyn@1", "environment": "staging"},
        )
        self.assertEqual(rendered, "Ship kyn@1 to staging.")
        with self.assertRaisesRegex(ContractViolation, "missing"):
            render_prompt(
                "Ship {{artifact}} to {{environment}}.",
                declared_variables=["artifact", "environment"],
                values={"artifact": "kyn@1"},
            )
        with self.assertRaisesRegex(ContractViolation, "unexpected"):
            render_prompt(
                "Ship {{artifact}}.",
                declared_variables=["artifact"],
                values={"artifact": "kyn@1", "secret": "must-not-enter"},
            )

    def test_custom_resources_are_explicit_and_version_pinned(self) -> None:
        prompt = self.plane.create_prompt(
            self.workspace_id,
            name="Custom executor prompt",
            slug="custom-executor",
            template="Inspect policy, then stage {{artifact}} in {{requested_environment}}.",
            variables=["artifact", "requested_environment"],
        )
        skill = self.plane.create_skill(
            self.workspace_id,
            name="Safe release staging",
            slug="safe-release-staging",
            instructions="Inspect the pinned policy before requesting the sandbox stage tool.",
            allowed_tools=["inspect_release_policy", "stage_release"],
        )
        agent = self.plane.create_agent(
            self.workspace_id,
            name="Custom release executor",
            slug="custom-release-executor",
            role="executor",
            model="gpt-5.6",
            instructions="Use receipts as truth.",
            prompt_version_id=prompt["version"]["id"],
            skill_version_ids=[skill["version"]["id"]],
        )

        self.assertEqual(prompt["version"]["version"], 1)
        self.assertEqual(skill["version"]["allowed_tools"], ["inspect_release_policy", "stage_release"])
        self.assertEqual(agent["version"]["prompt_version_id"], prompt["version"]["id"])
        self.assertEqual(agent["version"]["skill_version_ids"], [skill["version"]["id"]])
        self.assertEqual(agent["version"]["effective_tools"], ["inspect_release_policy", "stage_release"])

    def test_skill_cannot_register_server_code_by_naming_an_unknown_tool(self) -> None:
        with self.assertRaisesRegex(ContractViolation, "unknown tool"):
            self.plane.create_skill(
                self.workspace_id,
                name="Unsafe shell",
                slug="unsafe-shell",
                instructions="Run whatever command the model proposes.",
                allowed_tools=["run_shell"],
            )


if __name__ == "__main__":
    unittest.main()
