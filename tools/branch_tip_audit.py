#!/usr/bin/env python3
"""Fail-closed guard for forbidden legacy number-pool references in active files."""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PATTERN = re.compile(r"(?i)\bR\s*[-_ ]?\s*54\b")
CODE_AND_DATA_SUFFIXES = {
    ".py", ".json", ".yml", ".yaml", ".sh", ".bash", ".js", ".ts",
    ".toml", ".ini", ".cfg", ".ps1", ".ipynb", ".xml", ".html",
    ".txt", ".env", ".sql", ".rb", ".go", ".java", ".c", ".h",
    ".cpp", ".rs", ".properties",
}
INSTRUCTION_PREFIXES = (
    ".github/", ".claude/", "agents/", "prompts/", "instructions/",
    "skills/",
)


def is_active_text_file(relative_path: str) -> bool:
    path = Path(relative_path)
    if path.suffix.lower() in CODE_AND_DATA_SUFFIXES:
        return True
    return (
        path.suffix.lower() in {".md", ".txt"}
        and any(relative_path.startswith(prefix) for prefix in INSTRUCTION_PREFIXES)
    )


def tracked_paths() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", errors="replace"))
    return [
        item.decode("utf-8", errors="strict")
        for item in proc.stdout.split(b"\0")
        if item
    ]


def main() -> int:
    matches = []
    errors = []
    checked_files = 0

    for relative_path in tracked_paths():
        if not is_active_text_file(relative_path):
            continue
        path = ROOT / relative_path
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append({"path": relative_path, "error": str(exc)})
            continue

        checked_files += 1
        for line_number, line in enumerate(content.splitlines(), start=1):
            if FORBIDDEN_PATTERN.search(line):
                matches.append({
                    "path": relative_path,
                    "line": line_number,
                    "text": line.strip(),
                })

    print(json.dumps({
        "mode": "active_tree_guard",
        "active_text_files_checked": checked_files,
        "forbidden_reference_count": len(matches),
        "forbidden_references": matches,
        "read_errors": errors,
    }, ensure_ascii=False, indent=2))

    if matches or errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
