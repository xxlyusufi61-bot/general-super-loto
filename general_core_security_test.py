import os
import json
from datetime import date
from pathlib import Path

import general_core as core

archive_path = Path("super_loto_archive.json")

if not archive_path.is_file():
    raise AssertionError("SECURITY TEST FAILED: Archive file is missing.")

try:
archive = json.loads(
archive_path.read_text(encoding="utf-8")
)
except (OSError, json.JSONDecodeError) as exc:
raise AssertionError(
"SECURITY TEST FAILED: Archive could not be read: " + str(exc)
) from exc

if not isinstance(archive, list) or not archive:
raise AssertionError(
"SECURITY TEST FAILED: Archive must be a non-empty list."
)

dates = []

for row in archive:
if not isinstance(row, dict):
raise AssertionError("SECURITY TEST FAILED: Invalid archive record.")

day = row.get("date")

if not isinstance(day, str):
    raise AssertionError("SECURITY TEST FAILED: Invalid archive date.")

try:
    date.fromisoformat(day)
except ValueError as exc:
    raise AssertionError(
        "SECURITY TEST FAILED: Invalid archive date: " + day
    ) from exc

dates.append(day)

if dates != sorted(dates):
raise AssertionError(
"SECURITY TEST FAILED: Archive is not chronological."
)

if len(dates) != len(set(dates)):
raise AssertionError(
"SECURITY TEST FAILED: Duplicate archive dates found."
)

target_date = dates[-1]
target_rows = [
row for row in archive
if row["date"] == target_date
]

if len(target_rows) != 1:
raise AssertionError(
"SECURITY TEST FAILED: Latest target must occur exactly once."
)

target_numbers = target_rows[0].get("numbers")

if (
not isinstance(target_numbers, list)
or len(target_numbers) != 6
or len(set(target_numbers)) != 6
or any(
not isinstance(n, int)
or isinstance(n, bool)
or not 1 <= n <= 60
for n in target_numbers
)
):
raise AssertionError(
"SECURITY TEST FAILED: Latest target numbers are invalid."
)

captured = {}
original_ask_nvidia = core.ask_nvidia
old_save = os.environ.pop("GENERAL_SAVE_MEMORY", None)
old_target = os.environ.get("GENERAL_TARGET_DATE")

def fake_nvidia(messages):
captured["messages"] = messages
return "MOCK_RESPONSE"

try:
core.ask_nvidia = fake_nvidia
os.environ["GENERAL_TARGET_DATE"] = target_date

core.run_agent(
    "validation",
    "target_date=" + target_date
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
        "SECURITY TEST FAILED: System message content is not text."
    )

if "DATA GUARD PASS" not in system:
    raise AssertionError(
        "SECURITY TEST FAILED: Validated target-aware data missing."
    )

marker = system.rfind('{"guard":')

if marker < 0:
    raise AssertionError(
        "SECURITY TEST FAILED: Structured guarded data not found."
    )

try:
    data, _ = json.JSONDecoder().raw_decode(system[marker:])
except json.JSONDecodeError as exc:
    raise AssertionError(
        "SECURITY TEST FAILED: Guarded data could not be parsed."
    ) from exc

if not isinstance(data, dict):
    raise AssertionError(
        "SECURITY TEST FAILED: Guarded data is not an object."
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
        "SECURITY TEST FAILED: Target presence not confirmed."
    )

training = data.get("training_data")

if not isinstance(training, list) or not training:
    raise AssertionError(
        "SECURITY TEST FAILED: Training data missing or empty."
    )

for row in training:
    if not isinstance(row, dict):
        raise AssertionError(
            "SECURITY TEST FAILED: Invalid training record."
        )

    day = row.get("date")

    if not isinstance(day, str) or day >= target_date:
        raise AssertionError(
            "SECURITY TEST FAILED: Target/future date in training."
        )

    numbers = row.get("numbers")

    if (
        not isinstance(numbers, (list, tuple))
        or len(numbers) != 6
        or len(set(numbers)) != 6
        or any(
            not isinstance(n, int)
            or isinstance(n, bool)
            or not 1 <= n <= 60
            for n in numbers
        )
    ):
        raise AssertionError(
            "SECURITY TEST FAILED: Invalid training numbers."
        )

allowed_keys = {
    "guard",
    "target_date",
    "archive_count",
    "training_count",
    "target_present",
    "training_data",
}

if set(data.keys()) != allowed_keys:
    raise AssertionError(
        "SECURITY TEST FAILED: Unexpected guarded-data fields."
    )

if data.get("training_count") != len(training):
    raise AssertionError(
        "SECURITY TEST FAILED: Training record count mismatch."
    )

if data.get("archive_count") != len(archive):
    raise AssertionError(
        "SECURITY TEST FAILED: Archive record count mismatch."
    )

serialized_messages = json.dumps(
    messages, ensure_ascii=False
)
target_sequence = json.dumps(target_numbers)

if target_sequence in serialized_messages:
    raise AssertionError(
        "SECURITY TEST FAILED: Target number sequence found in messages."
    )

print("SECURITY TEST: PASS")
print("Mock NVIDIA API used: YES")
print("Real NVIDIA API called: NO")
print("Data Guard status: PASS")
print("Dynamic target date:", target_date)
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
