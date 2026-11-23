import copy
import unittest

from general_nvidia_guard import (
    LEGACY_NVIDIA_V2_200_CATEGORY_LIMITS,
    LEGACY_NVIDIA_V2_200_DAILY_BUDGET,
    LEGACY_NVIDIA_V2_72_CATEGORY_LIMITS,
    LEGACY_NVIDIA_V2_72_DAILY_BUDGET,
    NVIDIA_BUDGET_CATEGORY_LIMITS,
    NVIDIA_DAILY_REQUEST_BUDGET,
    initial_rate_limit_state,
)
from general_nvidia_budget_migrate import migrate_ledger

def legacy_v2(daily_budget, categories):
    value = initial_rate_limit_state()
    value["schema_version"] = 2
    value["daily_max_requests"] = daily_budget
    value["category_limits"] = dict(categories)
    value["package_max_requests"] = 4
    value["daily_requests"] = [{
        "reserved_at_utc": "2026-11-20T10:00:00+00:00",
        "source": "test-source",
        "category": "forward_discovery",
        "package_id": "package-001",
        "reservation_id": "reservation-001",
    }]
    value["requests"] = [{"reserved_at_utc": "2026-11-20T10:00:00+00:00", "source": "test-source"}]
    value["diagnostic_run_ids"] = ["123456"]
    return value

class BudgetMigrationTests(unittest.TestCase):
    def test_known_200_v2_migrates_and_preserves_all_histories(self):
        source = legacy_v2(LEGACY_NVIDIA_V2_200_DAILY_BUDGET, LEGACY_NVIDIA_V2_200_CATEGORY_LIMITS)
        before = copy.deepcopy(source)
        result, report = migrate_ledger(source)
        self.assertEqual(result["schema_version"], 3)
        self.assertEqual(result["daily_max_requests"], 1500)
        self.assertEqual(result["category_limits"], NVIDIA_BUDGET_CATEGORY_LIMITS)
        self.assertNotIn("package_max_requests", result)
        for key in ("daily_requests", "requests", "diagnostic_run_ids"):
            self.assertEqual(result[key], before[key])
        self.assertEqual(report["status"], "MIGRATED_V2_TO_V3")
        self.assertEqual(report["reservation_count"], 1)

    def test_known_72_v2_migrates(self):
        result, _ = migrate_ledger(legacy_v2(LEGACY_NVIDIA_V2_72_DAILY_BUDGET, LEGACY_NVIDIA_V2_72_CATEGORY_LIMITS))
        self.assertEqual(result["daily_max_requests"], 1500)

    def test_already_v3_is_idempotent_noop(self):
        source = initial_rate_limit_state()
        result, report = migrate_ledger(source)
        self.assertEqual(result, source)
        self.assertEqual(report["status"], "ALREADY_V3_NOOP")

    def test_unknown_legacy_budget_fails_closed(self):
        source = legacy_v2(200, LEGACY_NVIDIA_V2_200_CATEGORY_LIMITS)
        source["daily_max_requests"] = 201
        with self.assertRaises(ValueError):
            migrate_ledger(source)

    def test_corrupt_reservation_history_fails_closed(self):
        source = legacy_v2(200, LEGACY_NVIDIA_V2_200_CATEGORY_LIMITS)
        source["daily_requests"][0]["reservation_id"] = ""
        with self.assertRaises(ValueError):
            migrate_ledger(source)

if __name__ == "__main__":
    unittest.main()
