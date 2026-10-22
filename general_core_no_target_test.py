import os
import general_core as core

captured = {"called": False}

original_ask_nvidia = core.ask_nvidia
old_save = os.environ.pop("GENERAL_SAVE_MEMORY", None)
old_target = os.environ.pop("GENERAL_TARGET_DATE", None)


def mock_ask_nvidia(messages):
    captured["called"] = True
    return "MOCK_RESPONSE"


try:
    core.ask_nvidia = mock_ask_nvidia

    try:
        core.run_agent(
            "validation",
            "Check whether the archive was analyzed."
        )
    except (ValueError, RuntimeError) as exc:
        message = str(exc)
        normalized = message.lower()

        target_related = any(
            word in normalized
            for word in (
                "target",
                "date",
                "isolation",
                "hedef",
                "tarih",
            )
        )

        if not target_related:
            raise AssertionError(
                f"NO-TARGET TEST FAILED: unrelated error: "
                f"{type(exc).__name__}: {message}"
            ) from exc

        print("Request blocked:", type(exc).__name__, message)

    else:
        raise AssertionError(
            "NO-TARGET TEST FAILED: request was not blocked "
            "without GENERAL_TARGET_DATE"
        )

    if captured["called"]:
        raise AssertionError(
            "NO-TARGET TEST FAILED: model was called "
            "without a target date"
        )

    print("Target date supplied: NO")
    print("No-target request blocked: YES")
    print("Mock model called: NO")
    print("NO-TARGET TEST PASSED")

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
