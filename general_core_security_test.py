import os
import json
from datetime import date
from pathlib import Path

import general_core as core

def fail(message):
raise AssertionError("SECURITY TEST FAILED: " + message)

def main():
archive_path = Path("super_loto_archive.json")

if not archive_path.is_file():
    fail("Archive file is missing.")

try:
    archive = json.loads(
        archive_path.read_text(encoding="utf-8")
    )
except (OSError, json.JSONDecodeError) as exc:
    fail("Archive could not be read: " + str(exc))

if not isinstance(archive, list) or not archive:
    fail("Archive must be a non-empty list.")

dates = []

for row in archive:
    if not isinstance(row, dict):
        fail("Invalid archive record.")

    day = row.get("date")
    if not isinstance(day, str):
        fail("Archive record has no valid date.")

    try:
        date.fromisoformat(day)
    except ValueError:
        fail("Invalid archive date: " + day)

    dates.append(day)

if dates != sorted(dates):
    fail("Archive is not in chronological order.")

if len(dates) != len(set(dates)):
    fail("Duplicate archive dates found.")

# En son arşiv tarihi dinamik hedef olarak seçilir.
target_date = dates[-1]

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
        fail("Mock model was not called.")

    messages = captured["messages"]

    if not isinstance(messages, list) or not messages:
        fail("Model messages are missing.")

    if not isinstance(messages[0], dict):
        fail("Invalid system message.")

    if messages[0].get("role") != "system":
        fail("System message is missing.")

    system = messages[0].get("content", "")

    if not isinstance(system, str):
        fail("System message content is not text.")

    if "DATA GUARD PASS" not in system:
        fail("Validated target-aware data was not supplied.")

    marker = system.rfind('{"guard":')

    if marker < 0:
        fail("Structured guarded data was not found.")

    try:
        data, _ = json.JSONDecoder().raw_decode(
            system[marker:]
        )
    except json.JSONDecodeError:
        fail("Guarded data could not be parsed.")

    if not isinstance(data, dict):
        fail("Guarded data is not an object.")

    if data.get("guard") != "PASS":
        fail("Data Guard status is not PASS.")

    if data.get("target_date") != target_date:
        fail("Target date mismatch.")

    if data.get("target_present") is not True:
        fail("Target date was not confirmed in the archive.")

    training = data.get("training_data")

    if not isinstance(training, list) or not training:
        fail("Training data is missing or empty.")

    for row in training:
        if not isinstance(row, dict):
            fail("Invalid training record.")

        day = row.get("date")

        if not isinstance(day, str) or day >= target_date:
            fail("Target or future date found in training data.")

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
            fail("Invalid numbers in a training record.")

    allowed_keys = {
        "guard",
        "target_date",
        "archive_count",
        "training_count",
        "target_present",
        "training_data",
    }

    if set(data.keys()) != allowed_keys:
        fail("Unexpected fields in guarded data.")

    if data.get("training_count") != len(training):
        fail("Training record count mismatch.")

    if data.get("archive_count") != len(archive):
        fail("Archive record count mismatch.")

    # Hedef sayıları model mesajlarında bulunmamalı.
    target_rows = [
        row for row in archive
        if row["date"] == target_date
    ]

    if len(target_rows) != 1:
        fail("Latest target does not occur exactly once.")

    target_numbers = target_rows[0].get("numbers")

    if (
        not isinstance(target_numbers, list)
        or len(target_numbers) != 6
        or len(set(target_numbers)) != 6
    ):
        fail("Latest target numbers are invalid.")

    serialized_messages = json.dumps(
        messages, ensure_ascii=False
    )

    # Bu kontrol yalnızca mesaj içindeki tam sayı dizisini
    # arar; farklı biçimde kodlanmış değerlerin yokluğunu
    # tek başına kanıtlamaz.
    target_sequence = json.dumps(target_numbers)

    if target_sequence in serialized_messages:
        fail("Target number sequence found in model messages.")

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

if name == "main":
main()
