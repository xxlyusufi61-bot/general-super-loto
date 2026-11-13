import json
import unittest
from unittest.mock import MagicMock, patch

import general_autonomous_research as autonomous
import general_cycle55_manual_retry as manual


def _run_payload(**overrides):
    actor = {
        "login": autonomous.CYCLE55_MANUAL_RETRY_ACTOR_LOGIN,
        "id": autonomous.CYCLE55_MANUAL_RETRY_ACTOR_ID,
    }
    value = {
        "id": "500001",
        "event": "workflow_dispatch",
        "status": "in_progress",
        "run_attempt": 1,
        "head_branch": "main",
        "head_sha": "a" * 40,
        "workflow_id": autonomous.CYCLE55_MANUAL_RETRY_WORKFLOW_ID,
        "path": autonomous.CYCLE55_MANUAL_RETRY_WORKFLOW_PATH,
        "actor": actor,
        "triggering_actor": actor,
        "created_at": "2026-11-13T12:00:00Z",
    }
    value.update(overrides)
    return value


def _env():
    return {
        "GITHUB_REPOSITORY": "xxlyusufi61-bot/general-super-loto",
        "GITHUB_RUN_ID": "500001",
        "GITHUB_SHA": "a" * 40,
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_TOKEN": "test-token",
    }


def _mock_response(payload):
    response = MagicMock()
    response.status = 200
    response.read.return_value = json.dumps(payload).encode("utf-8")
    manager = MagicMock()
    manager.__enter__.return_value = response
    manager.__exit__.return_value = False
    return manager


class Cycle55ManualRetryDispatchTests(unittest.TestCase):
    def test_exact_dispatch_identity_is_verified_from_github_api(self):
        payload = _run_payload()
        with patch.dict("os.environ", _env(), clear=False), patch(
            "general_cycle55_manual_retry.urllib.request.urlopen",
            return_value=_mock_response(payload),
        ) as opened:
            result = manual.verify_dispatch(
                autonomous.CYCLE55_MANUAL_RETRY_CONFIRMATION,
                "cycle55-manual-retry",
            )
        self.assertEqual(result["id"], "500001")
        self.assertEqual(result["actor_login"], autonomous.CYCLE55_MANUAL_RETRY_ACTOR_LOGIN)
        self.assertEqual(result["actor_id"], autonomous.CYCLE55_MANUAL_RETRY_ACTOR_ID)
        self.assertEqual(result["workflow_id"], autonomous.CYCLE55_MANUAL_RETRY_WORKFLOW_ID)
        self.assertEqual(result["run_attempt"], 1)
        self.assertEqual(opened.call_count, 1)

    def test_wrong_phrase_action_repository_or_environment_is_rejected_before_api_call(self):
        with patch.dict("os.environ", _env(), clear=True), patch(
            "general_cycle55_manual_retry.urllib.request.urlopen"
        ) as opened:
            with self.assertRaisesRegex(manual.ManualRetryDispatchError, "confirmation phrase"):
                manual.verify_dispatch("NO", "cycle55-manual-retry")
            opened.assert_not_called()

        with patch.dict("os.environ", {**_env(), "GITHUB_REPOSITORY": "someone/else"}, clear=True), patch(
            "general_cycle55_manual_retry.urllib.request.urlopen"
        ) as opened:
            with self.assertRaisesRegex(manual.ManualRetryDispatchError, "Repository identity"):
                manual.verify_dispatch(
                    autonomous.CYCLE55_MANUAL_RETRY_CONFIRMATION,
                    "cycle55-manual-retry",
                )
            opened.assert_not_called()

    def test_wrong_actor_branch_event_or_rerun_is_rejected(self):
        cases = (
            {"actor": {"login": "untrusted", "id": 1}},
            {"triggering_actor": {"login": "untrusted", "id": 1}},
            {"head_branch": "feature"},
            {"event": "schedule"},
            {"run_attempt": 2},
            {"status": "completed"},
            {"workflow_id": 1},
            {"path": ".github/workflows/another.yml"},
        )
        for change in cases:
            payload = _run_payload(**change)
            with self.subTest(change=change), patch.dict("os.environ", _env(), clear=True), patch(
                "general_cycle55_manual_retry.urllib.request.urlopen",
                return_value=_mock_response(payload),
            ):
                with self.assertRaisesRegex(manual.ManualRetryDispatchError, "identity"):
                    manual.verify_dispatch(
                        autonomous.CYCLE55_MANUAL_RETRY_CONFIRMATION,
                        "cycle55-manual-retry",
                    )

    def test_missing_or_unreachable_github_api_fails_closed(self):
        with patch.dict("os.environ", _env(), clear=True), patch(
            "general_cycle55_manual_retry.urllib.request.urlopen",
            side_effect=OSError("network unavailable"),
        ):
            with self.assertRaisesRegex(manual.ManualRetryDispatchError, "could not be verified"):
                manual.verify_dispatch(
                    autonomous.CYCLE55_MANUAL_RETRY_CONFIRMATION,
                    "cycle55-manual-retry",
                )


if __name__ == "__main__":
    unittest.main()
