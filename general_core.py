import os
import sys
import json
import hashlib
import re
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, date, timezone

VERSION = "0.5.1"
MODEL = "z-ai/glm-5.3-flash"
BASE = Path(__file__).resolve().parent
MEMORY = BASE / "general_memory.json"
ARCHIVE = BASE / os.environ.get(
    "GENERAL_ARCHIVE_FILE", "super_loto_archive.json"
)

RULES = [
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

AGENTS = {
    "research": "Investigate hypotheses and A->B->C, A->T, MOD5->T.",
    "debug": "Investigate code errors and propose reproducible fixes.",
    "validation": "Check archive, chronology, leakage and evaluation.",
    "crossover": "Compare methods and test combinations without overfitting."
}

TARGET_REQUIRED_AGENTS = {"research", "crossover"}

DATA_TASK_MARKERS = (
    "super loto", "super-loto", "lottery", "çekiliş", "cekilis",
    "draw", "archive", "arşiv", "arsiv", "prediction", "predict",
    "tahmin", "frequency", "frekans", "cross-validation", "walk-forward",
    "crossover", "mod5", "a->b->c", "a->t", "target isolation",
    "target leakage", "hedef izolasyon", "hedef çekiliş", "hedef cekilis",
    "veri sızıntı", "veri sizinti", "number pattern", "number distribution",
    "istatistik", "statistics"
)


def now():
    return datetime.now(timezone.utc).isoformat()


def load_memory():
    if not MEMORY.exists():
        return {
            "schema_version": 1,
            "project": "GENERAL Super Loto",
            "binding_rules": RULES,
            "entries": []
        }
    with MEMORY.open(encoding="utf-8") as f:
        m = json.load(f)
    if not isinstance(m, dict) or not isinstance(m.get("entries"), list):
        raise RuntimeError("Invalid memory file.")
    m["binding_rules"] = RULES
    return m


def save_memory(m):
    m["binding_rules"] = RULES
    m["updated_at"] = now()
    temp = MEMORY.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(temp, MEMORY)


def add_memory(agent, content):
    m = load_memory()
    digest = hashlib.sha256(content.encode()).hexdigest()
    source = "GENERAL/" + agent
    for e in m["entries"]:
        if e.get("content_hash") == digest and e.get("source") == source:
            print("Existing report already saved.")
            return
    ids = [
        e.get("id", 0) for e in m["entries"]
        if isinstance(e.get("id"), int)
    ]
    m["entries"].append({
        "id": max(ids or [0]) + 1,
        "timestamp": now(),
        "category": "agent_report",
        "title": agent + " report",
        "content": content,
        "content_hash": digest,
        "source": source,
        "status": "unverified"
    })
    m["entries"] = m["entries"][-200:]
    save_memory(m)


def target_date_from(task):
    matches = re.findall(
        r"(?i)(?:target_date|hedef_tarihi)\s*[:=]\s*(\d{4}-\d{2}-\d{2})",
        task
    )
    env_target = os.environ.get("GENERAL_TARGET_DATE", "").strip()

    if len(set(matches)) > 1:
        raise RuntimeError("Conflicting target dates.")

    task_target = matches[0] if matches else ""

    if env_target and task_target and env_target != task_target:
        raise RuntimeError("Conflicting target dates.")

    target = env_target or task_target

    if target:
        try:
            if date.fromisoformat(target).isoformat() != target:
                raise ValueError()
        except ValueError:
            raise RuntimeError("Target date must be YYYY-MM-DD.") from None

    task = re.sub(
        r"(?i)(?:target_date|hedef_tarihi)\s*[:=]\s*\d{4}-\d{2}-\d{2}",
        " ", task
    )
    return target or None, " ".join(task.split())


def task_mode_from(task):
    matches = re.findall(
        r"(?i)(?:^|\s)mode\s*[:=]\s*(data|code|system)(?=\s|$)",
        task
    )
    normalized = {m.lower() for m in matches}

    if len(normalized) > 1:
        raise RuntimeError("Conflicting task modes.")

    mode = next(iter(normalized)) if normalized else None

    task = re.sub(
        r"(?i)(?:^|\s)mode\s*[:=]\s*(?:data|code|system)(?=\s|$)",
        " ", task
    )
    return mode, " ".join(task.split())


def is_data_task(task):
    normalized = task.casefold()
    return any(marker in normalized for marker in DATA_TASK_MARKERS)



def latest_archive_target_date():
    """Validate the current archive and select its latest valid draw date."""
    try:
        import general_data_guard as guard
    except ImportError:
        raise RuntimeError("Data Guard module missing.") from None

    if not ARCHIVE.is_file():
        raise RuntimeError("Archive missing; analysis stopped.")

    try:
        with ARCHIVE.open(encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        raise RuntimeError(
            "Archive could not be read; analysis stopped."
        ) from None

    if isinstance(payload, dict):
        rows = payload.get("draws", payload.get("records"))
    else:
        rows = payload

    if not isinstance(rows, list):
        raise RuntimeError("Archive must contain a list of records.")

    try:
        checked = guard.validate_archive(rows)
    except Exception:
        raise RuntimeError(
            "Data Guard rejected archive while selecting dynamic target."
        ) from None

    if not isinstance(checked, dict) or checked.get("valid") is not True:
        raise RuntimeError(
            "Data Guard archive validation failed; target not selected."
        )

    target = checked.get("last_date")
    if not isinstance(target, str):
        raise RuntimeError(
            "Data Guard did not provide the latest valid archive date."
        )

    try:
        parsed = date.fromisoformat(target)
    except ValueError:
        raise RuntimeError(
            "Data Guard provided an invalid latest archive date."
        ) from None

    if parsed.isoformat() != target:
        raise RuntimeError(
            "Data Guard provided a non-canonical latest archive date."
        )

    return target

def prepare_data(target):
    try:
        import general_data_guard as guard
    except ImportError:
        raise RuntimeError("Data Guard module missing.") from None

    if not ARCHIVE.is_file():
        raise RuntimeError("Archive missing; analysis stopped.")

    try:
        with ARCHIVE.open(encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        raise RuntimeError(
            "Archive could not be read; analysis stopped."
        ) from None

    if isinstance(payload, dict):
        if "draws" in payload and "records" in payload:
            raise RuntimeError("Ambiguous archive format.")
        rows = payload.get("draws", payload.get("records"))
    else:
        rows = payload

    if not isinstance(rows, list):
        raise RuntimeError("Archive must contain a list of records.")

    try:
        checked = guard.validate_archive(rows)
        split = guard.isolate_target(rows, target)
    except Exception:
        raise RuntimeError(
            "Data Guard rejected archive or target isolation."
        ) from None

    if not isinstance(checked, dict) or checked.get("valid") is not True:
        raise RuntimeError("Data Guard archive validation failed.")

    if not isinstance(split, dict) or split.get("isolation_pass") is not True:
        raise RuntimeError("Data Guard target isolation failed.")

    training_raw = split.get("train")
    excluded_dates = split.get("excluded_dates")
    target_present = split.get("target_present_in_archive")

    if not isinstance(training_raw, list):
        raise RuntimeError("Data Guard returned invalid training records.")

    if not isinstance(excluded_dates, list):
        raise RuntimeError("Data Guard returned invalid excluded-date list.")

    if not isinstance(target_present, bool):
        raise RuntimeError("Data Guard returned invalid target-presence status.")

    # Historical hidden-target analysis requires the target draw to exist
    # in the source archive; Data Guard has already validated unique dates.
    if target_present is not True:
        raise RuntimeError(
            "Target date is missing from archive; analysis stopped."
        )

    target_day = date.fromisoformat(target)
    training = []

    for row in training_raw:
        if not isinstance(row, dict):
            raise RuntimeError("Invalid training record.")

        day = row.get("date")
        if not isinstance(day, str):
            raise RuntimeError("Training record has an invalid date.")

        try:
            parsed_day = date.fromisoformat(day)
        except ValueError:
            raise RuntimeError("Training record has an invalid date.") from None

        if parsed_day.isoformat() != day or parsed_day >= target_day:
            raise RuntimeError(
                "SECURITY: target or future date found in training."
            )

        numbers = row.get("numbers")
        if not isinstance(numbers, (list, tuple)) or len(numbers) != 6:
            raise RuntimeError("Training record has invalid numbers.")

        if any(
            not isinstance(n, int)
            or isinstance(n, bool)
            or not 1 <= n <= 60
            for n in numbers
        ) or len(set(numbers)) != 6:
            raise RuntimeError("Training record has invalid numbers.")

        # Send only the expected fields to the model.
        training.append({
            "date": day,
            "numbers": list(numbers)
        })

    for day in excluded_dates:
        if not isinstance(day, str):
            raise RuntimeError("Data Guard returned an invalid excluded date.")

        try:
            parsed_day = date.fromisoformat(day)
        except ValueError:
            raise RuntimeError(
                "Data Guard returned an invalid excluded date."
            ) from None

        if parsed_day.isoformat() != day or parsed_day < target_day:
            raise RuntimeError(
                "SECURITY: pre-target date was incorrectly excluded."
            )

    if not training:
        raise RuntimeError("No pre-target training records.")

    return {
        "guard": "PASS",
        "target_date": target,
        "archive_count": checked.get("record_count"),
        "training_count": len(training),
        "target_present": target_present,
        "training_data": training
    }


def ask_nvidia(messages):
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        raise RuntimeError("NVIDIA_API_KEY missing.")

    body = json.dumps({
        "model": MODEL,
        "messages": messages,
        "max_tokens": 1600,
        "temperature": 0.2,
        "reasoning_effort": "low",
        "clear_thinking": True,
        "stream": False
    }).encode()

    req = urllib.request.Request(
        "https://integrate.api.nvidia.com/v1/chat/completions",
        data=body,
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            result = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError("NVIDIA HTTP error: " + str(e.code)) from None
    except urllib.error.URLError:
        raise RuntimeError("NVIDIA connection error.") from None

    choices = result.get("choices", [])
    if not choices:
        raise RuntimeError("NVIDIA returned no choices.")

    message = choices[0].get("message") or {}
    # Only the final content field is eligible for display or memory.
    # Reasoning fields must never be treated as a final report.
    answer = message.get("content")

    if not isinstance(answer, str) or not answer.strip():
        raise RuntimeError("Empty model response.")

    return answer.strip()


def run_agent(agent, task):
    if agent not in AGENTS:
        raise RuntimeError("Unknown agent: " + agent)

    target, task = target_date_from(task)
    mode, task = task_mode_from(task)

    if not task:
        raise RuntimeError("Task is empty.")

    if target and mode in {"code", "system"}:
        raise RuntimeError(
            "A target date conflicts with code/system-only mode."
        )

    needs_automatic_target = (
        mode == "data"
        or (
            mode is None
            and (
                agent in TARGET_REQUIRED_AGENTS
                or is_data_task(task)
            )
        )
    )
    if target is None and needs_automatic_target:
        target = latest_archive_target_date()
        print("Dynamic target selected from validated archive:", target)

    if mode == "data" and not target:
        raise RuntimeError(
            "Data mode requires a valid target date."
        )

    if target:
        mode = "data"
    elif mode is None:
        if agent in TARGET_REQUIRED_AGENTS or is_data_task(task):
            raise RuntimeError(
                "Target date required for data-analysis tasks. "
                "Add target_date=YYYY-MM-DD, or use mode=code for code-only "
                "work / mode=system for system-only work."
            )
        mode = "code" if agent == "debug" else "system"

    if mode == "data" and not target:
        raise RuntimeError(
            "Data analysis cannot run without a target date."
        )

    # Validate and isolate before any AI request.
    data = prepare_data(target) if mode == "data" else None

    # No-target sessions must not load potentially unfiltered project reports.
    entries = load_memory()["entries"] if target else []

    if target:
        target_day = date.fromisoformat(target)
        eligible = []

        for entry in entries:
            try:
                stamp = datetime.fromisoformat(
                    str(entry["timestamp"]).replace("Z", "+00:00")
                )

                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)

                if stamp.astimezone(timezone.utc).date() < target_day:
                    eligible.append(entry)

            except (KeyError, ValueError, TypeError):
                continue

        entries = eligible

    words = set(task.lower().split())
    ranked = []

    for entry in entries:
        content = " ".join(
            str(entry.get(key, ""))
            for key in ("title", "category", "content", "source")
        ).lower()

        score = sum(
            1 for word in words if len(word) >= 3 and word in content
        )

        if score:
            ranked.append((score, entry))

    ranked.sort(
        key=lambda item: (
            item[0],
            str(item[1].get("timestamp", ""))
        ),
        reverse=True
    )

    context = []

    for _, entry in ranked[:8]:
        context.append({
            "id": entry.get("id"),
            "source": entry.get("source"),
            "status": entry.get("status"),
            "title": entry.get("title"),
            "content": str(entry.get("content", ""))[:1800]
        })

    if mode == "data":
        role = AGENTS[agent]
        safety_text = (
            "MODE: TARGET-AWARE DATA ANALYSIS.\n"
            "DATA GUARD PASS. Data Guard validated the archive and isolated "
            "the target. Only records strictly before target_date are "
            "supplied below. The target draw values and all target/future "
            "records are not supplied. Do not claim to have inspected "
            "excluded values.\n"
        )

        print("Data Guard: PASS")
        print("Training records:", data["training_count"])
        print("Target numbers exposed: NO")

    else:
        role = (
            "Review code errors and propose reproducible fixes."
            if mode == "code"
            else "Review GENERAL project architecture and workflow only."
        )

        context = []
        safety_text = (
            "MODE: CODE/SYSTEM ONLY.\n"
            "No archive was loaded or validated. No project memory reports "
            "were provided. Do not analyze actual Super Loto draw data, "
            "discover number patterns, make data-based predictions, or "
            "claim archive validation. If the task requires draw/archive/"
            "statistical analysis, state that target_date=YYYY-MM-DD must "
            "be supplied.\n"
        )

        print("Data Guard: not run; no target date supplied.")
        print("Archive loaded: NO")
        print("Project memory included: NO")

    system = (
        "You are GENERAL Super Loto agent: " + agent + ".\n"
        "Role: " + role + "\n"
        "Binding rules:\n- " + "\n- ".join(RULES) + "\n"
        "Memory reports are not proof. Distinguish hypotheses from verified "
        "results. Never invent data or tests.\n"
        "Output only a final report. Do not include internal deliberation, scratchpad, "
        "or private step-by-step reasoning. State what was actually checked and what remains unknown.\n"
        + safety_text
        + "Memory context:\n"
        + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    )

    if data:
        system += (
            "\nGUARDED DATA JSON:\n"
            + json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        )

    print("GENERAL Core", VERSION, "| Agent:", agent)

    answer = ask_nvidia([
        {"role": "system", "content": system},
        {"role": "user", "content": task}
    ])

    print("\n--- AGENT REPORT ---\n" + answer + "\n--- END ---")

    if os.environ.get("GENERAL_SAVE_MEMORY") == "1":
        add_memory(agent, answer)
        print("Report saved to shared memory.")
    else:
        print("Report not saved: GENERAL_SAVE_MEMORY is not enabled.")


def main():
    if len(sys.argv) < 3:
        print("GENERAL Core", VERSION)
        print(
            'Usage: python general_core.py validation '
            '"target_date=YYYY-MM-DD Your task"'
        )
        print("Agents:", ", ".join(AGENTS))
        print(
            "Use mode=code or mode=system for work that does not "
            "analyze draw data."
        )
        return

    try:
        run_agent(sys.argv[1].lower(), " ".join(sys.argv[2:]))
    except Exception as e:
        print("GENERAL ERROR:", str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
