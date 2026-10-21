import os
import sys
import json
import hashlib
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, date, timezone

VERSION = "0.4.0"
MODEL = "z-ai/glm-5.3-flash"
BASE = Path(__file__).resolve().parent
MEMORY = BASE / "general_memory.json"
ARCHIVE = BASE / os.environ.get(
    "GENERAL_ARCHIVE_FILE", "super_loto_archive.json"
)

RULES = [
    "Target draw must remain hidden during training and discovery.",
    "Evaluation must be chronological and leakage-free.",
    "Validate archive integrity before analysis.",
    "Preserve A->B->C, A->T and MOD5->T research.",
    "Use walk-forward evaluation and cross-validation.",
    "Never invent data, tests or results.",
    "Ask before changing binding rules.",
    "Lottery outcomes are uncertain; never promise predictions."
]

AGENTS = {
    "research": "Investigate hypotheses and A->B->C, A->T, MOD5->T.",
    "debug": "Investigate code errors and propose reproducible fixes.",
    "validation": "Check archive, chronology, leakage and evaluation.",
    "crossover": "Compare methods and test combinations without overfitting."
}


def now():
    return datetime.now(timezone.utc).isoformat()


def load_memory():
    if not MEMORY.exists():
        return {"schema_version": 1, "project": "GENERAL Super Loto",
                "binding_rules": RULES, "entries": []}
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
    for e in m["entries"]:
        if e.get("content_hash") == digest and e.get("source") == agent:
            print("Existing report already saved.")
            return
    ids = [e.get("id", 0) for e in m["entries"]
           if isinstance(e.get("id"), int)]
    m["entries"].append({
        "id": max(ids or [0]) + 1,
        "timestamp": now(),
        "category": "agent_report",
        "title": agent + " report",
        "content": content,
        "content_hash": digest,
        "source": "GENERAL/" + agent,
        "status": "unverified"
    })
    m["entries"] = m["entries"][-200:]
    save_memory(m)


def target_date_from(task):
    import re
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
        raise RuntimeError("Archive could not be read; analysis stopped.") from None

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
    except Exception as e:
        raise RuntimeError("Data Guard rejected archive: " + str(e)) from None

    if not checked.get("valid") or not split.get("isolation_pass"):
        raise RuntimeError("Data Guard did not pass.")

    training = split.get("train", [])
    if not training:
        raise RuntimeError("No pre-target training records.")

    for row in training:
        if date.fromisoformat(row["date"]) >= date.fromisoformat(target):
            raise RuntimeError("SECURITY: target leakage detected.")

    # Only pre-target records are passed to the model.
    return {
        "guard": "PASS",
        "target_date": target,
        "archive_count": checked["record_count"],
        "training_count": len(training),
        "target_present": split["target_present_in_archive"],
        "training_data": training
    }


def ask_nvidia(messages):
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        raise RuntimeError("NVIDIA_API_KEY missing.")

    body = json.dumps({
        "model": MODEL, "messages": messages,
        "max_tokens": 1600, "temperature": 0.2
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
    except urllib.error.URLError as e:
        raise RuntimeError("NVIDIA connection error.") from None

    choices = result.get("choices", [])
    if not choices:
        raise RuntimeError("NVIDIA returned no choices.")
    message = choices[0].get("message") or {}
    answer = message.get("content") or message.get("reasoning_content")
    if not isinstance(answer, str) or not answer.strip():
        raise RuntimeError("Empty model response.")
    return answer.strip()


def run_agent(agent, task):
    if agent not in AGENTS:
        raise RuntimeError("Unknown agent: " + agent)

    target, task = target_date_from(task)
    if not task:
        raise RuntimeError("Task is empty.")

    # Fail closed: validate and isolate before any AI request.
    data = prepare_data(target) if target else None

    memory = load_memory()
    entries = memory["entries"]

    # For target-aware runs, exclude reports from the target day
    # and all later dates. Unknown timestamps are excluded too.
    if target:
        td = date.fromisoformat(target)
        eligible = []
        for e in entries:
            try:
                stamp = datetime.fromisoformat(
                    str(e["timestamp"]).replace("Z", "+00:00")
                )
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                if stamp.astimezone(timezone.utc).date() < td:
                    eligible.append(e)
            except (KeyError, ValueError, TypeError):
                continue
        entries = eligible

    words = set(task.lower().split())
    ranked = []
    for e in entries:
        text = " ".join(str(e.get(k, "")) for k in
                        ("title", "category", "content", "source")).lower()
        score = sum(1 for w in words if len(w) >= 3 and w in text)
        if score:
            ranked.append((score, e))
    ranked.sort(key=lambda x: (x[0], str(x[1].get("timestamp", ""))),
                reverse=True)

    context = []
    for _, e in ranked[:8]:
        context.append({
            "id": e.get("id"),
            "source": e.get("source"),
            "status": e.get("status"),
            "title": e.get("title"),
            "content": str(e.get("content", ""))[:1800]
        })

    system = (
        "You are GENERAL Super Loto agent: " + agent + ".\n"
        "Role: " + AGENTS[agent] + "\n"
        "Binding rules:\n- " + "\n- ".join(RULES) + "\n"
        "Memory reports are not proof. Distinguish hypotheses "
        "from verified results. Never invent data or tests.\n"
        "Memory context:\n" + json.dumps(context, ensure_ascii=False)
    )

    if data:
        system += (
            "\nDATA GUARD PASS. Only pre-target training records follow. "
            "Never claim to have inspected excluded draw numbers.\n"
            + json.dumps(data, ensure_ascii=False)
        )
        print("Data Guard: PASS")
        print("Training records:", data["training_count"])
        print("Target numbers exposed: NO")
    else:
        system += (
            "\nNO TARGET-AWARE DATA SESSION. No archive was loaded "
            "or validated. Do not claim archive analysis was performed."
        )
        print("Data Guard: not run; no target date supplied.")

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
        print('Usage: python general_core.py research "Your task"')
        print("Agents:", ", ".join(AGENTS))
        return
    try:
        run_agent(sys.argv[1].lower(), " ".join(sys.argv[2:]))
    except Exception as e:
        print("GENERAL ERROR:", str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
