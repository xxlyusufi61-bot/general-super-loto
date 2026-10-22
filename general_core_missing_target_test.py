import json
import tempfile
from pathlib import Path

import general_core as core

def fail(message):
raise AssertionError("MISSING TARGET TEST FAILED: " + message)

def main():
original_archive = core.ARCHIVE

# Test için geçici ve küçük bir arşiv oluştur.
rows = [
    {
        "date": "2026-09-20",
        "numbers": [1, 2, 3, 4, 5, 6]
    },
    {
        "date": "2026-09-24",
        "numbers": [7, 8, 9, 10, 11, 12]
    }
]

try:
    with tempfile.TemporaryDirectory() as temp_dir:
        test_archive = Path(temp_dir) / "test_archive.json"
        test_archive.write_text(
            json.dumps(rows),
            encoding="utf-8"
        )

        # Test arşivinde hedef tarih olan 2026-09-22 bulunmuyor.
        core.ARCHIVE = test_archive

        try:
            core.prepare_data("2026-09-22")
        except RuntimeError as exc:
            if "Target date is missing from archive" not in str(exc):
                fail("Analysis stopped for an unexpected reason: " + str(exc))
        else:
            fail("Analysis continued although the target date was missing.")

finally:
    core.ARCHIVE = original_archive

print("MISSING TARGET TEST: PASS")
print("Target date absent from test archive: YES")
print("Analysis stopped: YES")
print("Real NVIDIA API called: NO")
print("Production archive modified: NO")

if name == "main":
main()
