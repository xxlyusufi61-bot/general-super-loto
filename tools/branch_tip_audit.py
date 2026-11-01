#!/usr/bin/env python3
"""Read-only audit of current remote branch tips for a prohibited legacy regime token."""

import json
import subprocess
import sys

PATTERN = r"R[[:space:]_-]*54([^0-9]|$)"
RUNTIME_SUFFIXES = {".py", ".json", ".yml", ".yaml", ".sh", ".bash", ".js", ".ts", ".toml", ".ini", ".cfg", ".ps1"}
PROMPT_PREFIXES = ("prompts/", "agents/", "instructions/", ".claude/", ".github/prompts/", "skills/")


def run_git(*args):
    return subprocess.run(
        ["git", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


refs_result = run_git("for-each-ref", "--format=%(refname)", "refs/remotes/origin")
if refs_result.returncode != 0:
    print(refs_result.stderr, file=sys.stderr)
    raise SystemExit(2)

refs = [line.strip() for line in refs_result.stdout.splitlines()
        if line.strip() and not line.rstrip().endswith("/HEAD")]
if not refs:
    print("No remote branch refs were found.", file=sys.stderr)
    raise SystemExit(2)

runtime_hits = []
document_hits = []
errors = []

for ref in refs:
    result = run_git("grep", "-n", "-i", "-I", "-E", PATTERN, ref)
    if result.returncode == 1:
        continue
    if result.returncode not in (0, 1):
        errors.append({"ref": ref, "error": result.stderr.strip()})
        continue
    for row in result.stdout.splitlines():
        try:
            path, line_no, text = row.split(":", 2)
        except ValueError:
            errors.append({"ref": ref, "error": "Unparseable git-grep result", "row": row})
            continue
        record = {"ref": ref, "path": path, "line": int(line_no), "text": text.strip()}
        suffix = "." + path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
        operational = (
            suffix in RUNTIME_SUFFIXES
            or path.startswith(".github/workflows/")
            or any(path.startswith(prefix) for prefix in PROMPT_PREFIXES)
        )
        if path == "tools/branch_tip_audit.py":
            operational = False
        (runtime_hits if operational else document_hits).append(record)

print("REMOTE_BRANCH_TIP_AUDIT")
print(json.dumps({
    "branch_tips_checked": len(refs),
    "runtime_or_config_hit_count": len(runtime_hits),
    "documentation_or_other_hit_count": len(document_hits),
    "runtime_or_config_hits": runtime_hits,
    "documentation_or_other_hits": document_hits,
    "errors": errors,
}, ensure_ascii=False, indent=2))

if errors:
    raise SystemExit(2)
if runtime_hits:
    raise SystemExit(1)
