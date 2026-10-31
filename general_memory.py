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
    "Lottery outcomes are uncertain; never promise predictions.",
    "The current target draw must be hidden from discovery, training, model selection, formula tuning, and scoring. Exclude the target row and every row on or after its date from pre-target discovery and training.",
    "Validate archive integrity before every analysis: canonical chronology, unique dates, valid six-number sets, regime-specific number ranges, exact target isolation, archive hash, and immutable snapshot when a claim is made.",
    "Preserve A->B->C, A->T, MOD5->T, and crossover research families, but do not silently assign or assume their mathematical semantics. Their exact definitions remain unconfirmed until the user explicitly approves them; an open PR such as PR #59 is not an approval.",
    "Forward discovery and testing must run chronologically from the selected 2020 starting draw through the latest pre-target draw. At each historical cutoff, discover/select using only earlier draws and score only the next eligible target; replay selection at every cutoff. Never apply a formula selected after seeing later rows to older forward targets and label that result leakage-free.",
    "Reverse discovery and testing must independently run from the latest pre-target draw toward 2020, use reverse-ordered discovery context, and use only strictly later historical rows for each reverse target. Report this direction separately and label it retrospective stress testing only, never predictive evidence.",
    "Forward and reverse discovery are two independent model requests: the forward request receives only oldest-to-newest discovery rows, and the reverse request only newest-to-oldest rows; neither request may receive the other direction's payload or candidates. Validate responses separately, require exact direction labels forward/reverse (never both to bypass isolation), then namespace IDs before combining sanitized outputs. Every proposal labels its discovery direction. Freeze the first eligible candidate in each direction before validation; do not switch to an alternative after viewing either holdout's scores.",
    "Where there is enough R60 history, reserve disjoint directional holdouts: oldest pre-target rows for reverse testing, middle rows for discovery, newest pre-target rows for forward testing. Both holdouts stay out of model discovery; the current target is outside all three partitions. Never reduce the minimum training/validation counts silently.",
    "Preserve the R54/R60 split and boundary 2020-08-02. Validate, evaluate, cross-validate, and report the regimes separately; never pool their number pools or hit-rate baselines.",
    "Run chronological walk-forward evaluation and blocked time-ordered cross-validation separately for forward and reverse protocols where supported. Do not shuffle. State when folds reuse the same interval and are therefore correlated, not independent confirmation.",
    "Per-draw reports must include date, direction, frozen formula/candidate identifier and parameter/spec hash, direction-appropriate history count, hit count, and which top-six column positions 1-6 matched. Report missed rows and aggregate hit distributions. Do not disclose target number sets or predicted number values in the shared report.",
    "Report actual test coverage, date ranges, methods, candidate/parameter versions, and limitations. Keep hypotheses, synthetic test passes, real-archive tests, and verified mechanisms distinct. Never invent data, tests, results, or claims.",
    "Check and preserve earlier project versions, decisions, test outcomes, rule-change approvals, and repository state before every Super Loto change. Flag conflicts and failures instead of silently replacing prior decisions.",
    "Keep archive snapshots content-addressed and claims/ledgers append-only. Once a date is used in either directional holdout, do not re-use it as a fresh independent validation date. Persist the claim and snapshot before scoring; if evaluation fails, the claimed interval remains consumed.",
    "Do not let a DSL-safe generic baseline stand in for the formally approved meaning of A->B->C, A->T, MOD5->T, or crossover. Unsupported semantics must remain untested rather than being approximated silently.",
    "All paid services, credit-card-required setups, paid conversion, and automatic billing transitions are prohibited. Hosted NVIDIA calls require the existing explicit user consent, verified free-only/no-billing status, and global safety lock; offline tests must not make live calls.",
    "Never promise winning numbers, predictive certainty, or an edge. Reverse-time results cannot be used as evidence that a formula predicts future draws.",
    "PR #63 must remain unmerged until two-direction discovery, full eligible-history per-cutoff forward evaluation, reverse retrospective testing, cross-validation, and per-draw column-hit reports are verified by CI and the user explicitly approves merging.",
    "Code edits should be made on a feature branch, not main. Use no more than three declared development steps, provide complete verified artifacts rather than implying partial completion is final, and do not report a step complete before its tests pass."
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
