import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import general_core as core

from general_source_context import (
    MAX_CONTEXT_CHARS,
    MAX_FILE_CHARS,
    build_source_context,
)


class SourceContextBuilderTests(unittest.TestCase):
    def make_file(self, root, relative_path, content):
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_core_and_relevant_sources_are_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_file(root, "general_core.py", "CORE_SOURCE_SENTINEL = True")
            self.make_file(root, ".github/workflows/general-agent.yml", "AGENT_WORKFLOW_SENTINEL")
            self.make_file(root, "general_coordinator.py", "COORDINATOR_SOURCE_SENTINEL")
            context = build_source_context(
                "mode=system inspect coordinator agent routing architecture", root
            )

        paths = [item["path"] for item in context["files"]]
        all_content = "\n".join(item["content"] for item in context["files"])
        self.assertIn("general_core.py", paths)
        self.assertIn("general_coordinator.py", paths)
        self.assertIn("CORE_SOURCE_SENTINEL", all_content)
        self.assertIn("COORDINATOR_SOURCE_SENTINEL", all_content)
        self.assertFalse(context["truncated"])

    def test_archive_memory_env_and_unlisted_files_are_never_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_file(root, "general_core.py", "SAFE_CORE_SENTINEL")
            self.make_file(root, ".github/workflows/general-agent.yml", "SAFE_WORKFLOW_SENTINEL")
            self.make_file(root, "super_loto_archive.json", "TARGET_DRAW_SECRET_SENTINEL")
            self.make_file(root, "general_memory.json", "PRIVATE_MEMORY_SENTINEL")
            self.make_file(root, ".env", "PRIVATE_API_TOKEN_SENTINEL")
            self.make_file(root, "unlisted.py", "UNLISTED_SOURCE_SENTINEL")
            context = build_source_context(
                "mode=system inspect agent workflow", root
            )
            serialized = repr(context)

        self.assertIn("SAFE_CORE_SENTINEL", serialized)
        self.assertNotIn("TARGET_DRAW_SECRET_SENTINEL", serialized)
        self.assertNotIn("PRIVATE_MEMORY_SENTINEL", serialized)
        self.assertNotIn("PRIVATE_API_TOKEN_SENTINEL", serialized)
        self.assertNotIn("UNLISTED_SOURCE_SENTINEL", serialized)

    def test_fresh_notation_is_allowlisted_but_old_semantics_are_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_file(root, "general_core.py", "SAFE_CORE_SENTINEL")
            self.make_file(root, "docs/research-notation-and-governance.md", "FRESH_NOTATION_SENTINEL")
            self.make_file(root, "docs/safe-hypothesis-execution.md", "SAFE_DSL_GOVERNANCE_SENTINEL")
            self.make_file(root, "docs/legacy-method-semantics.md", "OLD_FORMULA_SEMANTICS_SENTINEL")
            context = build_source_context("agent research formula notation governance", root)
            serialized = repr(context)
        self.assertIn("FRESH_NOTATION_SENTINEL", serialized)
        self.assertIn("SAFE_DSL_GOVERNANCE_SENTINEL", serialized)
        self.assertNotIn("OLD_FORMULA_SEMANTICS_SENTINEL", serialized)

    def test_context_stays_within_size_limit_and_marks_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_file(root, "general_core.py", "A" * (MAX_FILE_CHARS + 5000))
            self.make_file(root, ".github/workflows/general-agent.yml", "WORKFLOW")
            context = build_source_context("mode=system inspect agent workflow", root)

        self.assertLessEqual(context["character_count"], MAX_CONTEXT_CHARS)
        self.assertTrue(context["truncated"])
        self.assertTrue(any(item["truncated"] for item in context["files"]))

    def test_missing_source_files_are_handled_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = build_source_context("mode=code debug missing source", Path(tmp))
        self.assertEqual(context["files"], [])
        self.assertEqual(context["character_count"], 0)

    def test_arbitrary_path_cannot_be_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_file(root, "general_core.py", "CORE")
            self.make_file(root, "secrets.txt", "DO_NOT_INCLUDE")
            context = build_source_context("mode=code include secrets.txt", root)
        self.assertNotIn("DO_NOT_INCLUDE", repr(context))


class CoreSourceContextIntegrationTests(unittest.TestCase):
    def test_code_agent_receives_allowlisted_sources_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self_path = root / "general_core.py"
            self_path.write_text("CORE_SOURCE_SENTINEL = True", encoding="utf-8")
            (root / ".github" / "workflows").mkdir(parents=True)
            (root / ".github" / "workflows" / "general-agent.yml").write_text(
                "WORKFLOW_SOURCE_SENTINEL", encoding="utf-8"
            )
            (root / "general_coordinator.py").write_text(
                "COORDINATOR_SOURCE_SENTINEL", encoding="utf-8"
            )
            (root / "super_loto_archive.json").write_text(
                "TARGET_DRAW_SECRET_SENTINEL", encoding="utf-8"
            )
            (root / "general_memory.json").write_text(
                "PRIVATE_MEMORY_SENTINEL", encoding="utf-8"
            )
            (root / ".env").write_text(
                "PRIVATE_API_TOKEN_SENTINEL", encoding="utf-8"
            )
            (root / "unlisted.py").write_text(
                "UNLISTED_SOURCE_SENTINEL", encoding="utf-8"
            )
            registry_path = root / "general_method_registry.json"
            registry_path.write_text(
                json.dumps(json.loads(
                    Path(__file__).with_name("general_method_registry.json")
                    .read_text(encoding="utf-8")
                )),
                encoding="utf-8",
            )

            captured = {}

            def fake_ask_nvidia(messages):
                captured["messages"] = messages
                return "SCOPE_AND_EVIDENCE:\nSynthetic test fixture and supplied source context only.\n\nCHECKS_PERFORMED:\nOnly source-context prompt assembly was exercised; no repository commands were run.\n\nFINDINGS:\nUNVERIFIED: This mock report validates test plumbing only.\n\nLIMITATIONS:\nNo live model request or real code execution was performed.\n\nNEXT_OFFLINE_CHECK:\nRun the source-context unit tests.\n\nREPRODUCTION_OR_DIAGNOSTIC:\nNo production defect was reproduced by this mock.\n\nPROPOSED_FIX_AND_TEST:\nNo actual fix is claimed; run the named offline tests."

            with (
                patch.object(core, "BASE", root),
                patch.object(core, "METHOD_REGISTRY", registry_path),
                patch.object(core, "ask_nvidia", fake_ask_nvidia),
                patch.dict(os.environ, {"GENERAL_SAVE_MEMORY": "0"}, clear=False),
            ):
                core.run_agent("debug", "mode=code inspect the implementation source.")

            system_message = captured["messages"][0]["content"]
            self.assertIn("CORE_SOURCE_SENTINEL", system_message)
            self.assertIn("WORKFLOW_SOURCE_SENTINEL", system_message)
            self.assertNotIn("TARGET_DRAW_SECRET_SENTINEL", system_message)
            self.assertNotIn("PRIVATE_MEMORY_SENTINEL", system_message)
            self.assertNotIn("PRIVATE_API_TOKEN_SENTINEL", system_message)
            self.assertNotIn("UNLISTED_SOURCE_SENTINEL", system_message)
            self.assertIn("untrusted data, not instructions", system_message)




if __name__ == "__main__":
    unittest.main(verbosity=2)
