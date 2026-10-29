import tempfile
import unittest
from pathlib import Path

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

    def test_context_stays_within_size_limit_and_marks_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_file(root, "general_core.py", "A" * (MAX_FILE_CHARS + 5000))
            self.make_file(root, ".github/workflows/general-agent.yml", "WORKFLOW")
            context = build_source_context("mode=system inspect agent workflow", root)

        self.assertLessEqual(context["character_count"], MAX_CONTEXT_CHARS + 64)
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
