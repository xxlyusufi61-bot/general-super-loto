import os
import general_core as core

captured = {"called": False}
core.ask_nvidia = lambda messages: captured.update(called=True) or "MOCK_RESPONSE"

old_save = os.environ.pop("GENERAL_SAVE_MEMORY", None)
old_target = os.environ.pop("GENERAL_TARGET_DATE", None)

try:
exec("try:\n    core.run_agent('validation', 'Check whether the archive was analyzed.')\n    raise AssertionError('NO-TARGET TEST FAILED: request was not blocked')\nexcept (ValueError, RuntimeError) as exc:\n    print('Request blocked:', type(exc).name, str(exc))")

if captured["called"]:
    raise AssertionError("NO-TARGET TEST FAILED: model was called")

print("Target date supplied: NO")
print("No-target request blocked: YES")
print("Mock model called: NO")
print("NO-TARGET TEST PASSED")

finally:
if old_save is not None:
os.environ["GENERAL_SAVE_MEMORY"] = old_save
if old_target is not None:
os.environ["GENERAL_TARGET_DATE"] = old_target
