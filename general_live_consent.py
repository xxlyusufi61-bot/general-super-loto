"""Single source of truth for explicit live NVIDIA-call approval."""

CONFIRMATION_PHRASE = "I_CONFIRM_FREE_ONLY_NO_BILLING_AND_APPROVE_UP_TO_3_NVIDIA_REQUESTS"


def has_explicit_live_consent(value: object) -> bool:
    """Return True only for the exact phrase; do not normalize or trim input."""
    return isinstance(value, str) and value == CONFIRMATION_PHRASE
