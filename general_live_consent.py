"""Single source of truth for explicit live NVIDIA-call approval."""

CONFIRMATION_PHRASE = "I_CONFIRM_FREE_ONLY_NO_BILLING_AND_APPROVE_UP_TO_2_NVIDIA_REQUESTS"
COORDINATOR_CONFIRMATION_PHRASE = "I_CONFIRM_FREE_ONLY_NO_BILLING_AND_APPROVE_UP_TO_3_NVIDIA_REQUESTS"
DIAGNOSTIC_CONFIRMATION_PHRASE = "I_APPROVE_ONE_DIAGNOSTIC_REQUEST"


def has_explicit_live_consent(value: object) -> bool:
    """Return True only for one of the exact operation-specific phrases."""
    return isinstance(value, str) and value in {
        CONFIRMATION_PHRASE,
        COORDINATOR_CONFIRMATION_PHRASE,
    }


def has_explicit_diagnostic_consent(value: object) -> bool:
    """Return True only for the separate one-request diagnostic approval."""
    return isinstance(value, str) and value == DIAGNOSTIC_CONFIRMATION_PHRASE


def has_explicit_coordinator_live_consent(value: object) -> bool:
    """Return True only for the coordinator's separate three-agent approval."""
    return isinstance(value, str) and value == COORDINATOR_CONFIRMATION_PHRASE
