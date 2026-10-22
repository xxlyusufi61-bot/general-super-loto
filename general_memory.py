import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


# ==========================================
# GENERAL MEMORY SYSTEM
# Version: 0.1.0
# ==========================================

BASE_DIR = Path(__file__).resolve().parent
MEMORY_FILE = BASE_DIR / "general_memory.json"

PROJECT_RULES = [
    "Target draw must remain hidden during discovery and training.",
    "Evaluation must be chronological and leakage-free.",
    "Archive integrity must be checked before analysis.",
    "Preserve A->B->C, A->T and MOD5->T research methods.",
    "Use walk-forward evaluation and cross-validation.",
    "Never invent data, experiments, or results.",
    "Never silently change binding project rules.",
    "Ask the user before changing binding rules.",
    "Separate hypotheses from experimentally verified findings.",
    "For mechanism/formula discovery and testing, evaluate both forward (2020 to latest pre-target) and reverse (latest pre-target/2026 toward 2020) chronology; reverse discovery is retrospective stress testing, not predictive evidence.",
    "Lottery outcomes are uncertain; never promise predictions."
]


def empty_memory():
    return {
        "schema_version": 1,
        "project": "GENERAL Super Loto",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "binding_rules": PROJECT_RULES,
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

    if not isinstance(memory.get("entries"), list):
        raise RuntimeError("Memory entries are invalid.")

    # Binding rules are defined by the project code.
    # Stored memory cannot silently replace them.
    memory["binding_rules"] = PROJECT_RULES

    return memory


def save_memory(memory):
    if not isinstance(memory, dict):
        raise ValueError("Memory must be a dictionary.")

    if not isinstance(memory.get("entries"), list):
        raise ValueError("Memory entries must be a list.")

    memory["binding_rules"] = PROJECT_RULES
    memory["updated_at"] = datetime.now(timezone.utc).isoformat()

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


def add_entry(category, title, content, source="GENERAL"):
    category = str(category).strip()
    title = str(title).strip()
    content = str(content).strip()

    if not category or not title or not content:
        raise ValueError(
            "Category, title and content must not be empty."
        )

    memory = load_memory()

    entry = {
        "id": len(memory["entries"]) + 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "category": category,
        "title": title,
        "content": content,
        "source": str(source),
        "status": "unverified"
    }

    memory["entries"].append(entry)
    save_memory(memory)

    return entry


def search_memory(query):
    query = str(query).strip().lower()
    memory = load_memory()

    if not query:
        return []

    matches = []

    for entry in memory["entries"]:
        searchable = " ".join([
            str(entry.get("category", "")),
            str(entry.get("title", "")),
            str(entry.get("content", "")),
            str(entry.get("source", ""))
        ]).lower()

        if query in searchable:
            matches.append(entry)

    return matches


def show_recent(limit=10):
    memory = load_memory()
    entries = memory["entries"][-limit:]

    print("GENERAL MEMORY")
    print("Total entries:", len(memory["entries"]))
    print()

    if not entries:
        print("No memory entries yet.")
        return

    for entry in reversed(entries):
        print(
            "[{id}] {category} | {title} | {status}".format(
                id=entry.get("id", "?"),
                category=entry.get("category", "unknown"),
                title=entry.get("title", "untitled"),
                status=entry.get("status", "unknown")
            )
        )
        print(entry.get("content", ""))
        print()


def main():
    if len(sys.argv) < 2:
        print("GENERAL Memory 0.1.0")
        print("Commands:")
        print("  python general_memory.py init")
        print("  python general_memory.py list")
        print('  python general_memory.py search "query"')
        return

    command = sys.argv[1].lower()

    if command == "init":
        if MEMORY_FILE.exists():
            print("Memory file already exists; not overwriting it.")
        else:
            save_memory(empty_memory())
            print("GENERAL memory initialized.")

    elif command == "list":
        show_recent()

    elif command == "search":
        if len(sys.argv) < 3:
            print('Usage: python general_memory.py search "query"')
            sys.exit(1)

        results = search_memory(" ".join(sys.argv[2:]))

        if not results:
            print("No matching memory entries.")
        else:
            for entry in results:
                print(
                    "[{id}] {title} — {status}".format(
                        id=entry.get("id", "?"),
                        title=entry.get("title", ""),
                        status=entry.get("status", "unknown")
                    )
                )
                print(entry.get("content", ""))
                print()

    else:
        print("Unknown command:", command)
        sys.exit(1)


if __name__ == "__main__":
    main()
