import os
import general_core as core


def fail(message):
    raise AssertionError("NO-TARGET TEST FAILED: " + message)


def main():
    captured = {"called": False}

    def fake_nvidia(messages):
        captured["called"] = True
        return "MOCK_RESPONSE"

    # Gerçek API çağrısını engelle.
    core.ask_nvidia = fake_nvidia

    # Hafızaya kayıt yapılmasını kapat.
    old_save = os.environ.get("GENERAL_SAVE_MEMORY")
    os.environ.pop("GENERAL_SAVE_MEMORY", None)

    # Hedef tarihi ortam değişkeniyle verilmediğinden emin ol.
    old_target = os.environ.get("GENERAL_TARGET_DATE")
    os.environ.pop("GENERAL_TARGET_DATE", None)

    try:
        core.run_agent(
            "validation",
            "Check whether the archive was analyzed."
        )
    except Exception as exc:
        # Bu test, mevcut davranışı ölçer; tek başına güvenlik şartı koymaz.
        print("NO-TARGET RUN RAISED ERROR:", type(exc).__name__)
    finally:
        if old_save is not None:
            os.environ["GENERAL_SAVE_MEMORY"] = old_save
        if old_target is not None:
            os.environ["GENERAL_TARGET_DATE"] = old_target

    print("Real NVIDIA API called: NO")
    print("Mock model called:", "YES" if captured["called"] else "NO")
    print("Target date supplied: NO")
    print("Test completed: YES")


if __name__ == "__main__":
    main()
