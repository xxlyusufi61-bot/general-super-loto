import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path


# ==========================================
# GENERAL AI CORE
# Version: 0.2.0
# ==========================================

MODEL = "z-ai/glm-5.3-flash"

BASE_DIR = Path(__file__).resolve().parent
MEMORY_FILE = BASE_DIR / "general_memory.json"
MAX_MEMORY_ENTRIES = 100
MAX_ENTRY_CHARS = 1200

PROJECT_RULES = """
You are part of GENERAL, a Super Loto research project.

BINDING RULES:
1. Never use a hidden target draw for training or discovery.
2. Preserve chronological, leakage-free evaluation.
3. Respect archive integrity and verify data before analysis.
4. Investigate A->B->C, A->T and MOD5->T methods.
5. Use walk-forward evaluation and cross-validation.
6. Never claim a hypothesis is proven without test results.
7. Never silently change established project rules.
8. Ask the user before changing binding rules.
9. Do not invent archive data, test results or findings.
10. Lottery outcomes are uncertain; do not promise predictions.

The system must distinguish hypotheses from verified findings.
"""

BINDING_RULES = [
    "Target draw must remain hidden during discovery and training.",
    "Evaluation must be chronological and leakage-free.",
    "Archive integrity must be checked before analysis.",
    "Preserve A->B->C, A->T and MOD5->T research methods.",
    "Use walk-forward evaluation and cross-validation.",
    "Never invent data, experiments, or results.",
    "Never silently change binding project rules.",
    "Ask the user before changing binding rules.",
    "Separate hypotheses from experimentally verified findings.",
    "Lottery outcomes are uncertain; never promise predictions."
]

AGENTS = {
    "research": {
        "name": "Research",
        "role": """
Investigate mathematical hypotheses and possible patterns.
Explore A->B->C, A->T, MOD5->T and alternative transformations.
Propose experiments without claiming unverified discoveries.
"""
    },
    "debug": {
        "name": "Debug",
        "role": """
Investigate code errors, logic errors and inconsistent results.
Explain likely causes and propose reproducible fixes.
Never claim code was executed unless execution evidence exists.
"""
    },
    "validation": {
        "name": "Validation",
        "role": """
Check archive integrity, chronology, target isolation and leakage.
Review walk-forward testing, cross-validation and baselines.
Identify missing evidence and invalid evaluation methods.
"""
    },
    "crossover": {
        "name": "Crossover",
        "role": """
Investigate combining independently evaluated methods.
Compare method stability and test combinations chronologically.
Reject unsupported improvements and avoid overfitting.
"""
    }
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def empty_memory():
    return {
        "schema_version": 1,
        "project": "GENERAL Super Loto",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "binding_rules": BINDING_RULES,
        "entries": []
    }


def load_memory():
    if not MEMORY_FILE.exists():
        return empty_memory()

    try:
        with MEMORY_FILE.open("r", encoding="utf-8") as file:
            memory = json.load(file)
    except (json.JSONDecodeError, OSError) as error:
        raise RuntimeError(
            "Memory file could not be read: " + str(error)
        ) from None

    if not isinstance(memory, dict):
        raise RuntimeError("Memory file has an invalid format.")

    entries = memory.get("entries")
    if not isinstance(entries, list):
        raise RuntimeError("Memory entries must be a list.")

    # Rules in code remain authoritative.
    memory["binding_rules"] = BINDING_RULES
    memory["entries"] = [
        entry for entry in entries if isinstance(entry, dict)
    ]

    return memory


def save_memory(memory):
    if not isinstance(memory, dict):
        raise ValueError("Memory must be a dictionary.")

    if not isinstance(memory.get("entries"), list):
        raise ValueError("Memory entries must be a list.")

    memory["binding_rules"] = BINDING_RULES
    memory["updated_at"] = utc_now()

    temporary_file = MEMORY_FILE.with_suffix(".tmp")

    with temporary_file.open("w", encoding="utf-8") as file:
        json.dump(
            memory,
            file,
            ensure_ascii=False,
            indent=2
        )
        file.write("\n")

    os.replace(temporary_file, MEMORY_FILE)


def add_memory_entry(category, title, content, source="GENERAL"):
    category = str(category).strip()
    title = str(title).strip()
    content = str(content).strip()

    if not category or not title or not content:
        raise ValueError("Memory category, title and content are required.")

    memory = load_memory()
    entries = memory["entries"]

    next_id = max(
        [int(item.get("id", 0)) for item in entries
         if str(item.get("id", "")).isdigit()] or [0]
    ) + 1

    entry = {
        "id": next_id,
        "timestamp": utc_now(),
        "category": category,
        "title": title,
        "content": content[:MAX_ENTRY_CHARS],
        "source": str(source),
        "status": "unverified"
    }

    entries.append(entry)
    memory["entries"] = entries[-MAX_MEMORY_ENTRIES:]
    save_memory(memory)

    return entry


def find_relevant_memory(task, limit=8):
    memory = load_memory()
    entries = memory["entries"]

    if not entries:
        return []

    # Lightweight keyword matching; this is retrieval, not proof.
    words = {
        word.lower().strip(".,:;!?()[]{}\"'")
        for word in task.split()
        if len(word.strip(".,:;!?()[]{}\"'")) >= 3
    }

    scored = []

    for entry in entries:
        text = " ".join([
            str(entry.get("category", "")),
            str(entry.get("title", "")),
            str(entry.get("content", ""))
        ]).lower()

        score = sum(1 for word in words if word in text)
        if score:
            scored.append((score, entry))

    scored.sort(key=lambda item: item[0], reverse=True)

    return [
        entry for _, entry in scored[:limit]
    ]


def format_memory(entries):
    if not entries:
        return "No matching saved memory entries were found."

    blocks = []

    for entry in entries:
        blocks.append(
            "[{category}] {title} | status={status}\n{content}".format(
                category=entry.get("category", "unknown"),
                title=entry.get("title", "untitled"),
                status=entry.get("status", "unverified"),
                content=str(entry.get("content", ""))[:MAX_ENTRY_CHARS]
            )
        )

    return "\n\n".join(blocks)


def ask_nvidia(messages):
    api_key = os.environ.get("NVIDIA_API_KEY")

    if not api_key:
        raise RuntimeError(
            "NVIDIA_API_KEY environment variable is missing."
        )

    payload = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": 1200,
        "temperature": 0.2,
        "stream": False
    }

    request = urllib.request.Request(
        "https://integrate.api.nvidia.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "Accept": "application/json"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise RuntimeError(
            "NVIDIA API HTTP error: " + str(error.code)
        ) from None
    except urllib.error.URLError as error:
        raise RuntimeError(
            "NVIDIA API connection error: " + str(error.reason)
        ) from None

    choices = result.get("choices", [])
    if not choices:
        raise RuntimeError("NVIDIA API returned no choices.")

    message = choices[0].get("message") or {}
    answer = (
        message.get("content")
        or message.get("reasoning_content")
        or message.get("reasoning")
    )

    if not isinstance(answer, str) or not answer.strip():
        raise RuntimeError("The model returned no readable text.")

    return answer.strip()


def run_agent(agent_id, task):
    if agent_id not in AGENTS:
        raise ValueError("Unknown agent: " + agent_id)

    agent = AGENTS[agent_id]
    memory = load_memory()
    relevant_entries = find_relevant_memory(task)

    memory_context = format_memory(relevant_entries)

    system_prompt = (
        PROJECT_RULES
        + "\n\nYour agent name: "
        + agent["name"]
        + "\nYour responsibilities:\n"
        + agent["role"]
        + "\n\nSAVED PROJECT MEMORY (context only; verify claims):\n"
        + memory_context
        + "\n\nMemory entries are not automatically verified facts. "
          "Respect their status and do not treat an unverified claim "
          "as established evidence."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task}
    ]

    print("GENERAL | Agent:", agent["name"])
    print("GENERAL | Memory entries:", len(memory["entries"]))
    print("GENERAL | Relevant memory entries:", len(relevant_entries))
    print("GENERAL | Sending task to NVIDIA AI...")

    answer = ask_nvidia(messages)

    print("\n--- AGENT REPORT ---")
    print(answer)
    print("--- END OF REPORT ---")

    # Saving is opt-in until persistent workflow storage is configured.
    if os.environ.get("GENERAL_SAVE_MEMORY") == "1":
        entry = add_memory_entry(
            category="agent_report",
            title=agent["name"] + " report",
            content=answer,
            source="GENERAL/" + agent_id
        )
        print("GENERAL | Report saved locally; entry ID:", entry["id"])
    else:
        print(
            "GENERAL | Report not saved. "
            "Persistent saving has not been enabled."
        )

    return answer


def main():
    if len(sys.argv) < 3:
        print("GENERAL AI Core 0.2.0")
        print('Usage: python general_core.py research "Your task"')
        print("Available agents:")
        for agent_id, agent in AGENTS.items():
            print("-", agent_id, ":", agent["name"])
        return

    agent_id = sys.argv[1].lower()
    task = " ".join(sys.argv[2:]).strip()

    if not task:
        print("ERROR: Task cannot be empty.")
        sys.exit(1)

    try:
        run_agent(agent_id, task)
    except Exception as error:
        print("GENERAL ERROR:", str(error))
        sys.exit(1)


if __name__ == "__main__":
    main()
