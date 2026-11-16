import contextlib
import io
import unittest

import general_cycle55_manual_retry as legacy_retry


class RetiredCycle55ManualRetryTests(unittest.TestCase):
    def test_legacy_dispatch_entrypoint_is_disabled(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = legacy_retry.main([
                "prepare",
                "--confirmation",
                "historical-only",
            ])
        self.assertEqual(result, 2)
        self.assertIn("retired", stderr.getvalue())
        self.assertIn("ten-attempt", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
