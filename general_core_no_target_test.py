import os
import general_core as core

def fail(message):
raise AssertionError("NO-TARGET TEST FAILED: " + message)

def main():
captured = {"called": False}

def fake_nvidia(messages):
    captured["called"] = True
    return "MOCK_RESPONSE"

core.ask_nvidia = fake_nvidia

old_save = os.environ.pop("GENERAL_SAVE_MEMORY", None)
old_target = os.environ.pop("GENERAL_TARGET_DATE", None)

try:
    blocked = False

    try:
        core.run_agent(
            "validation",
            "Check whether the archive was analyzed."
        )
    except (ValueError, RuntimeError) as exc:
        blocked = True
        print("Request blocked:", type(exc).__name__, str(exc))

    if not blocked:
        fail("No-target archive-analysis request was not blocked.")

    if captured["called"]:
        fail("The model was called without a target date.")

    print("Target date supplied: NO")
    print("No-target request blocked: YES")
    print("Mock model called: NO")
    print("Real NVIDIA API called: NO")
    print("NO-TARGET TEST PASSED")

finally:
    if old_save is not None:
        os.environ["GENERAL_SAVE_MEMORY"] = old_save
    if old_target is not None:
        os.environ["GENERAL_TARGET_DATE"] = old_target

if name == "main":
main()
