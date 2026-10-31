"""Telegram-backed rule-change request queue for GENERAL.

Proposals are recorded as GitHub Issues. A Telegram callback records approval
or rejection, but approval NEVER changes binding rules or merges code.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERSION = "0.1.0"
MARKER = re.compile(r"\A\s*<!-- GENERAL_RULE_CHANGE_STATUS:([A-Z_]+) -->")
BLOCK = re.compile(r"<RULE_CHANGE_REQUEST_JSON>\s*(\{.*?\})\s*</RULE_CHANGE_REQUEST_JSON>", re.DOTALL)
FIELDS = ("change_type", "rule_id", "current_rule", "proposed_rule", "reason", "evidence", "risk_assessment", "tests_required", "rollback_plan")
MAX_FIELD_LENGTH = 2500

def extract_request(report: str) -> dict[str, str] | None:
    matches = list(BLOCK.finditer(report))
    if not matches:
        if "RULE_CHANGE_REQUEST_JSON" in report:
            raise ValueError("Rule-change block is malformed; no request was created.")
        return None
    if len(matches) != 1:
        raise ValueError("Exactly one rule-change request block is allowed.")
    try:
        data = json.loads(matches[0].group(1))
    except json.JSONDecodeError:
        raise ValueError("Rule-change JSON is invalid; no request was created.") from None
    if not isinstance(data, dict):
        raise ValueError("Rule-change request must be a JSON object.")
    clean: dict[str, str] = {}
    for name in FIELDS:
        value = data.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Missing or invalid rule-change field: " + name)
        value = value.strip()
        limit = 160 if name == "rule_id" else MAX_FIELD_LENGTH
        if name == "rule_id" and (chr(10) in value or chr(13) in value):
            raise ValueError("Rule ID must be a single line.")
        if len(value) > limit:
            raise ValueError("Rule-change field exceeds length limit: " + name)
        clean[name] = value
    if clean["change_type"] not in {"ADD", "REVISE", "REMOVE"}:
        raise ValueError("change_type must be ADD, REVISE, or REMOVE.")
    if clean["change_type"] == "ADD":
        if clean["current_rule"] != "NONE" or clean["proposed_rule"] == "REMOVE":
            raise ValueError("ADD requests must use current_rule NONE and include new wording.")
    elif clean["change_type"] == "REMOVE":
        if clean["current_rule"] == "NONE" or clean["proposed_rule"] != "REMOVE":
            raise ValueError("REMOVE requests must identify the current rule and set proposed_rule to REMOVE.")
    else:
        if clean["current_rule"] == "NONE" or clean["current_rule"] == clean["proposed_rule"]:
            raise ValueError("REVISE requests must change an existing rule.")
    return clean

def status_from_body(body: str) -> str | None:
    match = MARKER.match(body)
    return match.group(1) if match else None

def build_issue_body(request: dict[str, str], source: dict[str, str]) -> str:
    provenance = (
        "- Source workflow: " + source.get("workflow", "unknown") + "\n"
        "- Source run ID: " + source.get("run_id", "unknown") + "\n"
        "- Source commit: " + source.get("sha", "unknown") + "\n"
        "- Submitted at (UTC): " + source.get("submitted_at", "unknown") + "\n"
    )
    return (
        "<!-- GENERAL_RULE_CHANGE_STATUS:PENDING_USER_APPROVAL -->\n"
        "# GENERAL rule-change request\n\n"
        "**Status:** PENDING_USER_APPROVAL\n\n"
        "No rule has been changed. Telegram approval authorizes follow-up review/implementation only; "
        "it does not edit the main branch or merge code.\n\n"
        "## Provenance\n" + provenance + "\n"
        "## Proposed change\n\n"
        "Change type: " + request["change_type"] + "\n"
        "Rule ID: " + request["rule_id"] + "\n\n"
        "### Current rule\n" + request["current_rule"] + "\n\n"
        "### Proposed rule\n" + request["proposed_rule"] + "\n\n"
        "### Reason\n" + request["reason"] + "\n\n"
        "### Evidence\n" + request["evidence"] + "\n\n"
        "### Risk assessment\n" + request["risk_assessment"] + "\n\n"
        "### Required tests\n" + request["tests_required"] + "\n\n"
        "### Rollback plan\n" + request["rollback_plan"] + "\n\n"
        "## Decision audit\nPending; an authorized Telegram approval/rejection command is required.\n"
    )

def decision_status(body: str, decision: str, actor_id: int, actor_name: str, callback_id: str) -> str:
    if status_from_body(body) != "PENDING_USER_APPROVAL":
        raise ValueError("Request is not pending.")
    if decision not in ("APPROVED", "REJECTED"):
        raise ValueError("Invalid decision.")
    status = "APPROVED_PENDING_IMPLEMENTATION" if decision == "APPROVED" else "REJECTED"
    updated = MARKER.sub("<!-- GENERAL_RULE_CHANGE_STATUS:" + status + " -->", body, count=1)
    stamp = datetime.now(timezone.utc).isoformat()
    safe_name = actor_name.replace(chr(13), " ").replace(chr(10), " ")[:128] if actor_name else "(not provided)"
    updated += (
        "\n\n---\n## Decision audit entry\n"
        "- Decision: **" + status + "**\n"
        "- Telegram approver user ID: " + str(actor_id) + "\n"
        "- Telegram name: " + safe_name + "\n"
        "- Decided at (UTC): " + stamp + "\n"
        "- Telegram command reference: " + callback_id + "\n"
    )
    if decision == "APPROVED":
        updated += "\nApproval does not modify binding rules or merge code; a separately reviewed implementation pull request is required.\n"
    return updated

def parse_decision_command(text: Any) -> tuple[str, int] | None:
    """Only exact approval/rejection commands are actionable."""
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r"\s*/(approve|reject)(?:@[A-Za-z0-9_]+)?\s+#?([1-9][0-9]{0,8})\s*", text, re.IGNORECASE)
    if not match:
        return None
    decision = "APPROVED" if match.group(1).lower() == "approve" else "REJECTED"
    return decision, int(match.group(2))

def is_authorized(message_update: dict[str, Any], chat_id: str, approver_user_id: str = "") -> bool:
    message, actor = message_update.get("message"), message_update.get("from")
    if not isinstance(message, dict) or not isinstance(actor, dict):
        return False
    chat = message.get("chat")
    if not isinstance(chat, dict) or str(chat.get("id", "")) != str(chat_id):
        return False
    actor_id = actor.get("id")
    if type(actor_id) is not int:
        return False
    expected = str(approver_user_id).strip()
    if not expected:
        # In private Telegram chats the chat ID equals the user's ID. Group
        # chats fail closed unless TELEGRAM_APPROVER_USER_ID is configured.
        try:
            if int(chat_id) <= 0:
                return False
        except ValueError:
            return False
        expected = str(chat_id)
    return expected.isdigit() and actor_id == int(expected)

class Api:
    def __init__(self, token: str, base_url: str | None = None):
        if not token:
            raise RuntimeError("GITHUB_TOKEN is missing.")
        self.token = token
        self.base_url = (base_url or os.environ.get("GITHUB_API_URL", "https://api.github.com")).rstrip("/")
    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            self.base_url + path, data=data,
            headers={"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json",
                     "User-Agent": "GENERAL-Rule-Change-Approval"},
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=25) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise RuntimeError("GitHub API request failed with HTTP " + str(exc.code)) from None
        except (urllib.error.URLError, TimeoutError):
            raise RuntimeError("GitHub API connection/timeout error.") from None
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise RuntimeError("GitHub API returned invalid JSON.") from None

class Telegram:
    def __init__(self, token: str):
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is missing.")
        self.base = "https://api.telegram.org/bot" + token
    def call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        encoded = {
            key: json.dumps(value, separators=(",", ":")) if isinstance(value, (dict, list)) else str(value)
            for key, value in payload.items()
        }
        req = urllib.request.Request(
            self.base + "/" + method, data=urllib.parse.urlencode(encoded).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=25) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError("Telegram API request failed with HTTP " + str(exc.code)) from None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            raise RuntimeError("Telegram API connection/response error.") from None
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise RuntimeError("Telegram API did not confirm the request.")
        return result

def repo_path() -> str:
    value = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise RuntimeError("GITHUB_REPOSITORY is missing or invalid.")
    return "/repos/" + value

def submit_report(path: str) -> int:
    report = Path(path).read_text(encoding="utf-8")
    request = extract_request(report)
    if request is None:
        print("RULE CHANGE REQUEST: none proposed; no issue or Telegram message created.")
        return 0
    repo = repo_path()
    github = Api(os.environ.get("GITHUB_TOKEN", ""))
    telegram = Telegram(os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not chat_id or not chat_id.lstrip("-").isdigit():
        raise RuntimeError("TELEGRAM_CHAT_ID is missing or invalid.")
    source = {
        "workflow": os.environ.get("GITHUB_WORKFLOW", "unknown"),
        "run_id": os.environ.get("GITHUB_RUN_ID", "unknown"),
        "sha": os.environ.get("GITHUB_SHA", "unknown"),
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }
    body = build_issue_body(request, source)
    issue = github.request("POST", repo + "/issues", {
        "title": "[RULE CHANGE REQUEST] " + request["rule_id"],
        "body": body,
    })
    number = issue.get("number") if isinstance(issue, dict) else None
    if type(number) is not int or number < 1:
        raise RuntimeError("GitHub did not return a valid rule-change issue number.")
    preview = " ".join(request["proposed_rule"].split())
    if len(preview) > 380:
        preview = preview[:377] + "..."
    issue_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/") + "/" + os.environ["GITHUB_REPOSITORY"] + "/issues/" + str(number)
    message = (
        "GENERAL — KURAL DEĞİŞİKLİĞİ TALEBİ\n"
        f"Talep no: #{number}\nTür: {request['change_type']}\nKural: {request['rule_id']}\nÖneri: {preview}\n"
        f"Talep ayrıntısı: {issue_url}\n\n"
        f"Onaylamak için bu sohbete /approve {number} yaz.\n"
        f"Reddetmek için bu sohbete /reject {number} yaz.\n\n"
        "Komut yalnızca yetkili Telegram hesabından ve bu sohbetten gelirse işlenir. "
        "Kontrol yaklaşık 30 dakikada bir çalışır; gecikme olabilir. Onay kuralı otomatik değiştirmez; uygulama için ayrıca incelenen bir PR gerekir."
    )
    try:
        sent = telegram.call("sendMessage", {
            "chat_id": chat_id, "text": message, "disable_web_page_preview": "true",
        })
        result = sent.get("result", {})
        if not isinstance(result, dict) or type(result.get("message_id")) is not int:
            raise RuntimeError("Telegram delivery was not confirmed.")
    except RuntimeError:
        failed_body = MARKER.sub("<!-- GENERAL_RULE_CHANGE_STATUS:DELIVERY_FAILED_CLOSED -->", body, count=1)
        failed_body += "\n\nTelegram delivery was not confirmed; request closed without a decision.\n"
        try:
            github.request("PATCH", repo + "/issues/" + str(number), {
                "body": failed_body, "state": "closed", "state_reason": "not_planned",
            })
        except RuntimeError:
            pass
        raise RuntimeError(
            f"Rule-change issue #{number} was created, but Telegram delivery was not confirmed."
        ) from None
    print("RULE CHANGE REQUEST: created; issue #" + str(number))
    print("Telegram delivery: confirmed; status: PENDING_USER_APPROVAL")
    print("No binding rule changed.")
    return 0

def send_chat_text(telegram: Telegram, chat_id: str, text: str, reply_to: int | None = None) -> None:
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text[:3500], "disable_web_page_preview": "true"}
    if type(reply_to) is int:
        payload["reply_to_message_id"] = reply_to
    telegram.call("sendMessage", payload)

def handle_message(message: dict[str, Any], github: Api, telegram: Telegram,
                   chat_id: str, approver_user_id: str, repo: str) -> str:
    parsed = parse_decision_command(message.get("text"))
    if parsed is None:
        return "ignored"
    message_id = message.get("message_id")
    if type(message_id) is not int:
        return "ignored"
    auth_context = {"message": message, "from": message.get("from")}
    chat = message.get("chat", {})
    if not is_authorized(auth_context, chat_id, approver_user_id):
        if isinstance(chat, dict) and str(chat.get("id", "")) == str(chat_id):
            send_chat_text(telegram, chat_id, "Yetkisiz kullanıcı; talep değiştirilmedi.", message_id)
        return "unauthorized"

    decision, number = parsed
    issue = github.request("GET", repo + "/issues/" + str(number))
    if not isinstance(issue, dict) or not str(issue.get("title", "")).startswith("[RULE CHANGE REQUEST]"):
        send_chat_text(telegram, chat_id, f"#{number} numaralı kural değişikliği talebi bulunamadı.", message_id)
        return "not_found"
    body = issue.get("body")
    if not isinstance(body, str):
        send_chat_text(telegram, chat_id, "Talep kaydı okunamadı; karar uygulanmadı.", message_id)
        return "invalid_issue"
    if status_from_body(body) != "PENDING_USER_APPROVAL" or issue.get("state") != "open":
        send_chat_text(telegram, chat_id, f"#{number} talebi daha önce işlendi veya artık beklemede değil.", message_id)
        return "already_handled"

    actor = message.get("from", {})
    actor_id = actor.get("id")
    if type(actor_id) is not int:
        send_chat_text(telegram, chat_id, "Telegram kullanıcı doğrulaması başarısız.", message_id)
        return "unauthorized"
    actor_name = str(actor.get("username") or actor.get("first_name") or "")
    updated_body = decision_status(body, decision, actor_id, actor_name, "message-" + str(message_id))
    state_reason = "completed" if decision == "APPROVED" else "not_planned"
    github.request("PATCH", repo + "/issues/" + str(number), {
        "body": updated_body, "state": "closed", "state_reason": state_reason,
    })
    outcome = (
        f"GENERAL — Talep #{number} ONAYLANDI. Kurallar henüz değişmedi; ayrıca incelenen bir PR gerekir."
        if decision == "APPROVED" else f"GENERAL — Talep #{number} REDDEDİLDİ. Kural değişmedi."
    )
    send_chat_text(telegram, chat_id, outcome, message_id)
    return "approved" if decision == "APPROVED" else "rejected"

def poll_approvals() -> int:
    github = Api(os.environ.get("GITHUB_TOKEN", ""))
    telegram = Telegram(os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    approver = os.environ.get("TELEGRAM_APPROVER_USER_ID", "").strip()
    if not chat_id or not chat_id.lstrip("-").isdigit():
        raise RuntimeError("TELEGRAM_CHAT_ID is missing or invalid.")
    if approver and not approver.isdigit():
        raise RuntimeError("TELEGRAM_APPROVER_USER_ID must be numeric.")
    repo = repo_path()
    webhook = telegram.call("getWebhookInfo", {}).get("result", {})
    if isinstance(webhook, dict) and webhook.get("url"):
        raise RuntimeError("Telegram webhook is configured; getUpdates polling stopped without changing it.")
    counts = {"approved": 0, "rejected": 0, "unauthorized": 0, "ignored": 0, "already_handled": 0}
    offset = 0
    for _ in range(10):
        payload = telegram.call("getUpdates", {
            "offset": offset, "timeout": 0, "allowed_updates": ["message"],
        })
        updates = payload.get("result", [])
        if not isinstance(updates, list) or not updates:
            break
        for update in updates:
            if not isinstance(update, dict) or type(update.get("update_id")) is not int:
                continue
            offset = max(offset, update["update_id"] + 1)
            message = update.get("message")
            if not isinstance(message, dict):
                counts["ignored"] += 1
                continue
            outcome = handle_message(message, github, telegram, chat_id, approver, repo)
            counts[outcome] = counts.get(outcome, 0) + 1
    # Confirm the highest processed update ID. If a newer callback arrives
    # in this final response, it remains available for the next poll.
    telegram.call("getUpdates", {
        "offset": offset, "timeout": 0, "allowed_updates": ["message"],
    })
    print("RULE CHANGE POLL: completed")
    print("Decisions approved:", counts["approved"])
    print("Decisions rejected:", counts["rejected"])
    print("Unauthorized approval commands ignored:", counts["unauthorized"])
    print("No rule was automatically modified.")
    return 0

def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    submit = sub.add_parser("submit-report")
    submit.add_argument("--report", required=True)
    sub.add_parser("poll")
    args = parser.parse_args()
    try:
        return submit_report(args.report) if args.command == "submit-report" else poll_approvals()
    except (OSError, ValueError, RuntimeError) as exc:
        print("RULE CHANGE WORKFLOW FAILED:", str(exc))
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
