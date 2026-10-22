import json
import tempfile
from pathlib import Path

import general_core as core
import general_data_guard as guard


TARGET_DATE = "2026-09-22"


def fail(message):
    raise AssertionError(
        "MISSING TARGET TEST FAILED: " + message
    )


def validate_result(result, label):
    if not isinstance(result, dict) or result.get("valid") is not True:
        fail(label + ": synthetic archive did not pass validation.")


def main():
    original_archive = core.ARCHIVE
    original_isolate_target = guard.isolate_target

    rows = [
        {
            "date": "2026-09-20",
            "numbers": [1, 2, 3, 4, 5, 6],
        },
        {
            "date": "2026-09-24",
            "numbers": [7, 8, 9, 10, 11, 12],
        },
    ]

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            test_archive = Path(temp_dir) / "test_archive.json"
            test_archive.write_text(
                json.dumps(rows),
                encoding="utf-8",
            )
            core.ARCHIVE = test_archive

            # 1. Gerçek Data Guard arşiv doğrulaması.
            checked = guard.validate_archive(rows)
            validate_result(checked, "Real Data Guard")

            # 2. Gerçek Data Guard hedef izolasyonu.
            real_split = guard.isolate_target(rows, TARGET_DATE)

            if not isinstance(real_split, dict):
                fail("Real Data Guard returned an invalid split object.")

            if real_split.get("target_present_in_archive") is not False:
                fail("Real Data Guard did not mark the target as absent.")

            if not isinstance(real_split.get("isolation_pass"), bool):
                fail("Real Data Guard returned invalid isolation status.")

            # Core, iki güvenli ret yolundan birini izlemeli.
            expected_real_error = (
                "Data Guard target isolation failed."
                if real_split.get("isolation_pass") is not True
                else "Target date is missing from archive; analysis stopped."
            )

            try:
                core.prepare_data(TARGET_DATE)
            except RuntimeError as exc:
                if str(exc) != expected_real_error:
                    fail(
                        "Unexpected error on real Data Guard path: "
                        + str(exc)
                    )
            else:
                fail(
                    "Core accepted an archive with no target draw "
                    "when using the real Data Guard."
                )

            # 3. Hedef-eksik kontrolünün kendisini ayrıca sınayalım.
            # Bu ikinci bölüm, yalnızca ilgili kod dalını izole eder.
            def controlled_missing_target(_rows, _target):
                return {
                    "isolation_pass": True,
                    "train": [rows[0]],
                    "excluded_dates": [rows[1]["date"]],
                    "target_present_in_archive": False,
                }

            guard.isolate_target = controlled_missing_target

            try:
                core.prepare_data(TARGET_DATE)
            except RuntimeError as exc:
                expected = (
                    "Target date is missing from archive; analysis stopped."
                )
                if str(exc) != expected:
                    fail(
                        "Explicit missing-target guard returned an "
                        "unexpected error: " + str(exc)
                    )
            else:
                fail("Explicit missing-target guard did not stop analysis.")

    finally:
        core.ARCHIVE = original_archive
        guard.isolate_target = original_isolate_target

    print("MISSING TARGET TEST: PASS")
    print("Real Data Guard checked the synthetic archive: YES")
    print("Real Data Guard marked target absent: YES")
    print("Core rejected the real missing-target case: YES")
    print("Explicit missing-target guard branch: PASS")
    print("Real NVIDIA API called: NO")
    print("Production archive modified: NO")


if __name__ == "__main__":
    main()
