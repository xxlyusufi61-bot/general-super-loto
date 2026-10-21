import os
import json
from pathlib import Path

import general_core as core


TARGET_DATE = "2026-09-22"


def fail(message):
    raise AssertionError("SECURITY TEST FAILED: " + message)


def main():
    archive = Path("super_loto_archive.json")

    if not archive.is_file():
        fail("Archive file is missing.")

    captured = {}

    # Gerçek NVIDIA API çağrısını engelle.
    def fake_nvidia(messages):
        captured["messages"] = messages
        return "MOCK_RESPONSE"

    core.ask_nvidia = fake_nvidia

    # Test sırasında raporları kalıcı hafızaya kaydetme.
    old_save = os.environ.get("GENERAL_SAVE_MEMORY")
    os.environ.pop("GENERAL_SAVE_MEMORY", None)

    try:
        # Hedef tarihli, kontrollü bir doğrulama görevi çalıştır.
        core.run_agent(
            "validation",
            "target_date=" + TARGET_DATE
            + " Check archive integrity and target isolation."
        )
    finally:
        if old_save is not None:
            os.environ["GENERAL_SAVE_MEMORY"] = old_save

    if "messages" not in captured:
        fail("Mock model was not called.")

    messages = captured["messages"]
    if not messages or messages[0].get("role") != "system":
        fail("System message is missing.")

    system = messages[0].get("content", "")

    if "DATA GUARD PASS" not in system:
        fail("Validated target-aware data was not supplied.")

    marker = system.rfind('{"guard":')
    if marker < 0:
        fail("Structured guarded data was not found.")

    try:
        data, _ = json.JSONDecoder().raw_decode(system[marker:])
    except json.JSONDecodeError:
        fail("Guarded data could not be parsed.")

    if data.get("guard") != "PASS":
        fail("Data Guard status is not PASS.")

    if data.get("target_date") != TARGET_DATE:
        fail("Target date mismatch.")

    if data.get("target_present") is not True:
        fail("Target date was not confirmed in the archive.")

    training = data.get("training_data")
    if not isinstance(training, list) or not training:
        fail("Training data is missing or empty.")

    for row in training:
        day = row.get("date")
        if not isinstance(day, str) or day >= TARGET_DATE:
            fail("Target or future date found in training data.")

    if "Target numbers exposed: NO" not in system:
        fail("Target-number exposure status is missing.")

    # Başarı mesajı dışında çekiliş numaralarını yazdırma.
    print("SECURITY TEST: PASS")
    print("Mock NVIDIA API used: YES")
    print("Real NVIDIA API called: NO")
    print("Target isolation in model input: PASS")
    print("Target/future dates in training: NONE")
    print("Target numbers printed: NO")


if __name__ == "__main__":
    main()
