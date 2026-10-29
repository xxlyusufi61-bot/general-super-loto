import os
import json
from datetime import date
from pathlib import Path
from unittest.mock import patch
import tempfile

import general_core as core
import general_memory as memory_module
from general_live_consent import CONFIRMATION_PHRASE


# ==================================================
# 1. LOAD AND VALIDATE ARCHIVE STRUCTURE
# ==================================================

archive_path = Path("super_loto_archive.json")

if not archive_path.is_file():
    raise AssertionError(
        "SECURITY TEST FAILED: Archive file is missing."
    )

try:
    archive_payload = json.loads(
        archive_path.read_text(encoding="utf-8")
    )
except (OSError, json.JSONDecodeError) as exc:
    raise AssertionError(
        "SECURITY TEST FAILED: Archive JSON could not be read."
    ) from exc

if not isinstance(archive_payload, dict):
    raise AssertionError(
        "SECURITY TEST FAILED: Archive root must be a JSON object."
    )

archive = archive_payload.get("draws")

if not isinstance(archive, list) or not archive:
    raise AssertionError(
        "SECURITY TEST FAILED: Archive must contain a non-empty draws list."
    )


# ==================================================
# 2. VALIDATE EVERY ARCHIVE RECORD
# ==================================================

dates = []

for index, row in enumerate(archive):
    if not isinstance(row, dict):
        raise AssertionError(
            "SECURITY TEST FAILED: Invalid record at index "
            + str(index)
        )

    day = row.get("date")

    if not isinstance(day, str):
        raise AssertionError(
            "SECURITY TEST FAILED: Invalid date at index "
            + str(index)
        )

    try:
        parsed_day = date.fromisoformat(day)
    except ValueError as exc:
        raise AssertionError(
            "SECURITY TEST FAILED: Invalid ISO date at index "
            + str(index)
        ) from exc

    if parsed_day.isoformat() != day:
        raise AssertionError(
            "SECURITY TEST FAILED: Non-canonical date at index "
            + str(index)
        )

    dates.append(day)

    numbers = row.get("numbers")

    if (
        not isinstance(numbers, list)
        or len(numbers) != 6
        or len(set(numbers)) != 6
        or any(
            not isinstance(number, int)
            or isinstance(number, bool)
            or not 1 <= number <= 60
            for number in numbers
        )
    ):
        raise AssertionError(
            "SECURITY TEST FAILED: Invalid draw numbers at index "
            + str(index)
        )

if dates != sorted(dates):
    raise AssertionError(
        "SECURITY TEST FAILED: Archive is not chronological."
    )

if len(dates) != len(set(dates)):
    raise AssertionError(
        "SECURITY TEST FAILED: Duplicate archive dates found."
    )


# ==================================================
# 3. IDENTIFY THE LATEST DRAW AS THE TARGET
# ==================================================

target_date = dates[-1]

target_rows = [
    row for row in archive
    if row["date"] == target_date
]

if len(target_rows) != 1:
    raise AssertionError(
        "SECURITY TEST FAILED: Latest target must occur exactly once."
    )

target_numbers = target_rows[0]["numbers"]


# ==================================================
# 4. PREPARE MOCK MODEL AND SAVE ORIGINAL STATE
# ==================================================

captured = {}

original_ask_nvidia = core.ask_nvidia

old_save = os.environ.get("GENERAL_SAVE_MEMORY")
old_target = os.environ.get("GENERAL_TARGET_DATE")


def fake_nvidia(messages):
    captured["messages"] = messages
    return "MOCK_RESPONSE"


# ==================================================
# 5. RUN SECURITY TEST
# ==================================================

try:
    os.environ.pop("GENERAL_SAVE_MEMORY", None)
    os.environ["GENERAL_TARGET_DATE"] = target_date

    core.ask_nvidia = fake_nvidia

    core.run_agent(
        "validation",
        "target_date="
        + target_date
        + " Check archive integrity and target isolation."
    )

    if "messages" not in captured:
        raise AssertionError(
            "SECURITY TEST FAILED: Mock model was not called."
        )

    messages = captured["messages"]

    if not isinstance(messages, list) or not messages:
        raise AssertionError(
            "SECURITY TEST FAILED: Model messages are missing."
        )

    if not isinstance(messages[0], dict):
        raise AssertionError(
            "SECURITY TEST FAILED: Invalid system message."
        )

    if messages[0].get("role") != "system":
        raise AssertionError(
            "SECURITY TEST FAILED: System message is missing."
        )

    system = messages[0].get("content", "")

    if not isinstance(system, str):
        raise AssertionError(
            "SECURITY TEST FAILED: System content is not text."
        )

    if "DATA GUARD PASS" not in system:
        raise AssertionError(
            "SECURITY TEST FAILED: Data Guard confirmation is missing."
        )

    if "Do not include internal deliberation" not in system:
        raise AssertionError(
            "SECURITY TEST FAILED: Final-report-only instruction is missing."
        )

    if "the workflow runner performs those actions after your response" not in system:
        raise AssertionError(
            "SECURITY TEST FAILED: Agent must not claim post-run memory-save status."
        )

    # Validation agent must receive aggregate metadata only.
    marker = system.rfind('{"guard":')

    if marker < 0:
        raise AssertionError(
            "SECURITY TEST FAILED: Guarded data metadata was not found."
        )

    try:
        data, _ = json.JSONDecoder().raw_decode(system[marker:])
    except json.JSONDecodeError as exc:
        raise AssertionError(
            "SECURITY TEST FAILED: Guarded metadata is invalid JSON."
        ) from exc

    if not isinstance(data, dict):
        raise AssertionError(
            "SECURITY TEST FAILED: Guarded metadata is not an object."
        )

    if data.get("guard") != "PASS":
        raise AssertionError(
            "SECURITY TEST FAILED: Data Guard status is not PASS."
        )

    if data.get("target_date") != target_date:
        raise AssertionError(
            "SECURITY TEST FAILED: Target date mismatch."
        )

    if data.get("target_present") is not True:
        raise AssertionError(
            "SECURITY TEST FAILED: Target presence was not confirmed."
        )

    if data.get("training_count") != len(archive) - 1:
        raise AssertionError(
            "SECURITY TEST FAILED: Training count metadata mismatch."
        )

    if data.get("archive_count") != len(archive):
        raise AssertionError(
            "SECURITY TEST FAILED: Archive count mismatch."
        )

    if data.get("training_records_sent_to_model") != 0:
        raise AssertionError(
            "SECURITY TEST FAILED: Training records should not be sent to validation model."
        )

    if data.get("draw_numbers_sent_to_model") is not False:
        raise AssertionError(
            "SECURITY TEST FAILED: Draw-number transfer flag is not false."
        )

    if data.get("statistical_methods_run") != []:
        raise AssertionError(
            "SECURITY TEST FAILED: Statistical methods must not be claimed as run."
        )

    row_level_fields = {
        "row_level_audit_pass": True,
        "archive_dates_chronological": True,
        "archive_dates_unique": True,
        "target_occurs_once": True,
        "target_is_latest_archive_date": True,
        "training_strictly_before_target": True,
        "training_chronological": True,
        "excluded_dates_match_archive": True,
        "training_dates_match_archive": True,
        "partition_counts_match": True,
    }
    for field, expected in row_level_fields.items():
        if data.get(field) is not expected:
            raise AssertionError(
                "SECURITY TEST FAILED: Row-level audit failed: " + field
            )

    expected_metadata_keys = {
        "guard",
        "target_date",
        "archive_count",
        "training_count",
        "target_present",
        "validation_scope",
        "row_level_audit_pass",
        "archive_dates_chronological",
        "archive_dates_unique",
        "target_occurs_once",
        "target_is_latest_archive_date",
        "training_strictly_before_target",
        "training_chronological",
        "excluded_dates_match_archive",
        "training_dates_match_archive",
        "partition_counts_match",
        "training_records_sent_to_model",
        "draw_numbers_sent_to_model",
        "statistical_methods_run",
    }

    if set(data.keys()) != expected_metadata_keys:
        raise AssertionError(
            "SECURITY TEST FAILED: Unexpected validation metadata fields."
        )

    if "training_data" in data:
        raise AssertionError(
            "SECURITY TEST FAILED: Training rows found in model metadata."
        )

    if data.get("draw_numbers_sent_to_model") is not False:
        raise AssertionError(
            "SECURITY TEST FAILED: Draw-number transfer flag is not false."
        )

    if data.get("training_records_sent_to_model") != 0:
        raise AssertionError(
            "SECURITY TEST FAILED: Training records were sent to validation model."
        )

    # Independently inspect the locally guarded training split.
    local_data = core.prepare_data(target_date)
    training = local_data.get("training_data")

    if not isinstance(training, list) or not training:
        raise AssertionError(
            "SECURITY TEST FAILED: Local training data is missing."
        )

    training_dates = []

    for row in training:
        if not isinstance(row, dict):
            raise AssertionError(
                "SECURITY TEST FAILED: Invalid local training record."
            )
        day = row.get("date")
        if not isinstance(day, str) or day >= target_date:
            raise AssertionError(
                "SECURITY TEST FAILED: Target/future date in local training."
            )
        try:
            date.fromisoformat(day)
        except ValueError as exc:
            raise AssertionError(
                "SECURITY TEST FAILED: Invalid local training date."
            ) from exc
        training_dates.append(day)

    if training_dates != sorted(training_dates):
        raise AssertionError(
            "SECURITY TEST FAILED: Local training data is not chronological."
        )

    if len(training_dates) != len(set(training_dates)):
        raise AssertionError(
            "SECURITY TEST FAILED: Duplicate local training dates found."
        )

    if local_data.get("row_level_audit_pass") is not True:
        raise AssertionError(
            "SECURITY TEST FAILED: Independent row-level audit did not pass."
        )

    if local_data.get("target_is_latest_archive_date") is not True:
        raise AssertionError(
            "SECURITY TEST FAILED: Dynamic target is not latest archive date."
        )

    # Ensure the exact target-number sequence is not present in model messages.
    serialized_messages = json.dumps(messages, ensure_ascii=False)
    target_sequence = json.dumps(target_numbers)

    if target_sequence in serialized_messages:
        raise AssertionError(
            "SECURITY TEST FAILED: Target-number sequence found in messages."
        )

    print("SECURITY TEST: PASS")
    print("Mock model used: YES")
    print("Real NVIDIA API called: NO")
    print("Data Guard status: PASS")
    print("Dynamic target date:", target_date)
    print("Archive records:", len(archive))
    print("Training records:", len(training))
    print("Target/future dates in training: NONE")
    print("Exact target-number sequence in messages: NOT FOUND")

finally:
    core.ask_nvidia = original_ask_nvidia

    if old_save is None:
        os.environ.pop("GENERAL_SAVE_MEMORY", None)
    else:
        os.environ["GENERAL_SAVE_MEMORY"] = old_save

    if old_target is None:
        os.environ.pop("GENERAL_TARGET_DATE", None)
    else:
        os.environ["GENERAL_TARGET_DATE"] = old_target


# ==================================================
# 6. REGRESSION TESTS: FINAL CONTENT, RULES, MEMORY DEDUPLICATION
# ==================================================

def regression_check(condition, message):
    if not condition:
        raise AssertionError("REGRESSION TEST FAILED: " + message)
    print("PASS:", message)


# Binding rules must be consistent in core, memory utility, and stored metadata.
regression_check(
    core.RULES == memory_module.PROJECT_RULES,
    "Core and memory utility share the same binding rules"
)

stored_memory = json.loads(
    Path("general_memory.json").read_text(encoding="utf-8")
)
regression_check(
    stored_memory.get("binding_rules") == core.RULES,
    "Stored memory metadata matches binding rules"
)
regression_check(
    any("reverse" in rule.lower() and "retrospective stress testing" in rule.lower()
        for rule in core.RULES),
    "Two-way chronology rule is preserved"
)


# Repeated identical reports from one agent must be deduplicated.
original_memory_path = core.MEMORY
try:
    with tempfile.TemporaryDirectory() as temporary_directory:
        core.MEMORY = Path(temporary_directory) / "test_memory.json"
        core.add_memory("validation", "unique-memory-deduplication-test")
        core.add_memory("validation", "unique-memory-deduplication-test")
        core.add_memory("research", "unique-memory-deduplication-test")
        test_memory = core.load_memory()
        regression_check(
            len(test_memory["entries"]) == 2,
            "Duplicate report is suppressed for the same agent"
        )
        regression_check(
            [e.get("source") for e in test_memory["entries"]]
            == ["GENERAL/validation", "GENERAL/research"],
            "Memory source labels remain consistent"
        )
finally:
    core.MEMORY = original_memory_path


# API response must never fall back to reasoning_content.
class _FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self.payload


old_api_key = os.environ.get("NVIDIA_API_KEY")
old_confirmation = os.environ.get("GENERAL_CONFIRM_LIVE_CALLS")
old_guard_passed = os.environ.get("GENERAL_NVIDIA_GUARD_PASSED")
try:
    os.environ["NVIDIA_API_KEY"] = "unit-test-key"
    os.environ["GENERAL_CONFIRM_LIVE_CALLS"] = CONFIRMATION_PHRASE
    os.environ["GENERAL_NVIDIA_GUARD_PASSED"] = "1"
    reasoning_only = {
        "choices": [{
            "message": {
                "content": "",
                "reasoning_content": "THIS MUST NOT BE ACCEPTED"
            }
        }]
    }
    with patch(
        "general_core.urllib.request.urlopen",
        return_value=_FakeResponse(reasoning_only)
    ):
        try:
            core.ask_nvidia([{"role": "user", "content": "test"}])
        except RuntimeError as error:
            regression_check(
                ("empty model response" in str(error).lower()
                or "final model content" in str(error).lower()),
                "Reasoning-only response is rejected"
            )
        else:
            raise AssertionError(
                "REGRESSION TEST FAILED: reasoning_content was accepted."
            )

    final_and_reasoning = {
        "choices": [{
            "message": {
                "content": "FINAL_REPORT_ONLY",
                "reasoning_content": "NOT_THE_FINAL_REPORT"
            }
        }]
    }
    captured_request = {}

    def capture_final_request(request, *args, **kwargs):
        captured_request["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse(final_and_reasoning)

    with patch(
        "general_core.urllib.request.urlopen",
        side_effect=capture_final_request
    ):
        answer = core.ask_nvidia([{"role": "user", "content": "test"}])
    regression_check(
        answer == "FINAL_REPORT_ONLY",
        "Only the content field is returned as the final answer"
    )
    request_payload = captured_request["payload"]
    template_kwargs = request_payload.get("chat_template_kwargs", {})
    regression_check(
        request_payload.get("reasoning_effort") == "low",
        "GLM reasoning budget is explicitly reduced to leave room for final content"
    )
    regression_check(
        template_kwargs.get("clear_thinking") is True,
        "GLM clear_thinking is sent as a chat-template argument"
    )
    regression_check(
        "clear_thinking" not in request_payload,
        "GLM chat-template argument is not sent as an unsupported top-level parameter"
    )
    regression_check(
        request_payload.get("stream") is False,
        "GLM request explicitly uses non-streaming output"
    )
finally:
    if old_api_key is None:
        os.environ.pop("NVIDIA_API_KEY", None)
    else:
        os.environ["NVIDIA_API_KEY"] = old_api_key
    if old_confirmation is None:
        os.environ.pop("GENERAL_CONFIRM_LIVE_CALLS", None)
    else:
        os.environ["GENERAL_CONFIRM_LIVE_CALLS"] = old_confirmation
    if old_guard_passed is None:
        os.environ.pop("GENERAL_NVIDIA_GUARD_PASSED", None)
    else:
        os.environ["GENERAL_NVIDIA_GUARD_PASSED"] = old_guard_passed
    for marker_name in ("/tmp/general_nvidia_api_attempted", "/tmp/general_nvidia_api_succeeded"):
        try:
            Path(marker_name).unlink()
        except FileNotFoundError:
            pass

print("RESULT: ALL GENERAL SECURITY AND MEMORY REGRESSION TESTS PASSED")
