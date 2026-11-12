"""Append-only GENERAL research memory with sealed and offline namespaces.

The sealed directional writer retains its claim/ledger binding and cannot modify
the isolated offline_research namespace. The offline symbolic runner may append
only to that namespace through this module and the existing generation-guarded
research_memory state object. Neither path imports legacy formulas or raw archive
rows into model context, and consumed draw rows are never copied into memory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from general_hypothesis_proposals import (
    PROPOSAL_KEYS,
    _validate_proposal,
    execution_spec_matches_proposal,
    parse_hypothesis_proposals,
)
from general_execution_spec_parser import parse_execution_spec_block
from general_execution_spec_dispatch import validate_execution_spec

VERSION = "0.2.0"
SCHEMA_VERSION = 3
PROJECT = "GENERAL Super Loto"
RESEARCH_EPOCH = "general-open-research-memory-v2"
RESEARCH_SCOPE_START = date(2020, 8, 2)
SOURCE = "GENERAL/open-research"
MEMORY_PATH = Path("general_research_memory.json")
RUN_STATUS_PATH = Path("open_research_run_status.json")
FORWARD_REPORT_PATH = Path("open_research_forward_report.md")
REVERSE_REPORT_PATH = Path("open_research_reverse_report.md")
SEALED_PLAN_PATH = Path("open_research_sealed_plan.json")
PROPOSALS_PATH = Path("open_research_proposals.json")
SPECS_PATH = Path("open_research_execution_specs.json")
CLAIM_PATH = Path("open_research_evaluation_claim.json")
EVALUATION_PATH = Path("open_research_evaluation_report.json")
ARCHIVE_PATH = Path("super_loto_archive.json")
LEDGER_PATH = Path("general_evaluation_ledger.json")

LESSONS_START = "<RESEARCH_LEARNING_NOTES_JSON>"
LESSONS_END = "</RESEARCH_LEARNING_NOTES_JSON>"
LESSON_KEYS = {"observations", "failed_approaches", "next_tests"}
MAX_LESSON_ITEMS = 6
MAX_LESSON_CHARS = 700
MAX_CONTEXT_ENTRIES = 6
# Conservative UTF-8 byte budgets. The active NVIDIA GLM-5.3-Flash endpoint
# documents a 1,048,576-token context window; byte counts are a conservative
# upper bound on tokenizer tokens. The final full-message guard reserves
# additional room for request framing and the 8,192-token completion.
MAX_CONTEXT_BYTES = 850_000
MAX_OFFLINE_CONTEXT_BYTES = 800_000
MAX_REPORT_CHARS = 9000
MAX_ENTRIES = 10000
HEX64 = re.compile(r"^[0-9a-f]{64}$")
REPORT_DATE_PATTERN = re.compile(
    r"(?<![0-9])(?:\d{4}[-/]\d{2}[-/]\d{2}|\d{1,2}[./]\d{1,2}[./]\d{4}|\d{1,2}/\d{1,2}/\d{4})(?![0-9])"
)
ENGLISH_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2,
    "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5,
    "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
TURKISH_MONTHS = {
    "ocak": 1, "şubat": 2, "subat": 2, "mart": 3, "nisan": 4,
    "mayıs": 5, "mayis": 5, "haziran": 6, "temmuz": 7,
    "ağustos": 8, "agustos": 8, "eylül": 9, "eylul": 9,
    "ekim": 10, "kasım": 11, "kasim": 11, "aralık": 12, "aralik": 12,
}
ALL_MONTHS = ENGLISH_MONTHS | TURKISH_MONTHS
MONTH_PATTERN = "|".join(
    re.escape(item) for item in sorted(ALL_MONTHS, key=len, reverse=True)
)
REPORT_TEXT_DATE_PATTERN = re.compile(
    r"(?ix)(?<!\w)(?P<month_before>" + MONTH_PATTERN
    + r")\s+(?P<day_before>\d{1,2})(?:st|nd|rd|th)?[,]?\s+(?P<year_before>\d{4})(?!\d)"
    + r"|(?<!\d)(?P<day_after>\d{1,2})\s+(?P<month_after>" + MONTH_PATTERN
    + r")\s+(?P<year_after>\d{4})(?!\d)"
)
REPORT_SECRET_PATTERN = re.compile(
    r"(?i)(?:\bnvapi-[A-Za-z0-9_-]{12,}\b|"
    r"\bgh[pousr]_[A-Za-z0-9]{20,}\b|"
    r"\b(?:sk|pk)-[A-Za-z0-9_-]{20,}\b|"
    r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}\b|"
    r"\b[0-9]{8,12}:[A-Za-z0-9_-]{25,}\b)"
)
DRAW_SET_TEXT = re.compile(
    r"(?<![0-9.])(?:[1-9]|[1-5][0-9]|60)"
    r"(?:(?:\s*[,;|/]\s*|\s+)(?:[1-9]|[1-5][0-9]|60)){5}(?![0-9]|\.[0-9])"
)
DRAW_SET_QUOTED_TEXT = re.compile(
    r"""(?<![0-9.])["']?(?:[1-9]|[1-5][0-9]|60)["']?"""
    r"""(?:(?:\s*[,;|/]\s*|\s+)["']?(?:[1-9]|[1-5][0-9]|60)["']?){5}(?![0-9]|\.[0-9])"""
)
DRAW_SET_FLEX_TEXT = re.compile(
    r"""(?<![0-9.])["']?0?(?:[1-9]|[1-5][0-9]|60)["']?"""
    r"""(?:(?:\s*[,;|/\-]\s*|\s+)["']?0?(?:[1-9]|[1-5][0-9]|60)["']?){5}(?![0-9]|\.[0-9])"""
)

ENGLISH_NUMBER_WORDS = (
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen", "twenty", "twenty one", "twenty-one",
    "twenty two", "twenty-two", "twenty three", "twenty-three", "twenty four",
    "twenty-four", "twenty five", "twenty-five", "twenty six", "twenty-six",
    "twenty seven", "twenty-seven", "twenty eight", "twenty-eight", "twenty nine",
    "twenty-nine", "thirty", "thirty one", "thirty-one", "thirty two", "thirty-two",
    "thirty three", "thirty-three", "thirty four", "thirty-four", "thirty five",
    "thirty-five", "thirty six", "thirty-six", "thirty seven", "thirty-seven",
    "thirty eight", "thirty-eight", "thirty nine", "thirty-nine", "forty",
    "forty one", "forty-one", "forty two", "forty-two", "forty three", "forty-three",
    "forty four", "forty-four", "forty five", "forty-five", "forty six", "forty-six",
    "forty seven", "forty-seven", "forty eight", "forty-eight", "forty nine",
    "forty-nine", "fifty", "fifty one", "fifty-one", "fifty two", "fifty-two",
    "fifty three", "fifty-three", "fifty four", "fifty-four", "fifty five",
    "fifty-five", "fifty six", "fifty-six", "fifty seven", "fifty-seven",
    "fifty eight", "fifty-eight", "fifty nine", "fifty-nine", "sixty",
)
TURKISH_NUMBER_WORDS = (
    "bir", "iki", "üç", "uc", "dört", "dort", "beş", "bes", "altı", "alti",
    "yedi", "sekiz", "dokuz", "on", "on bir", "on iki", "on üç", "on uc",
    "on dört", "on dort", "on beş", "on bes", "on altı", "on alti",
    "on yedi", "on sekiz", "on dokuz",
    "yirmi", "yirmi bir", "yirmi iki", "yirmi üç", "yirmi uc", "yirmi dört",
    "yirmi dort", "yirmi beş", "yirmi bes", "yirmi altı", "yirmi alti",
    "yirmi yedi", "yirmi sekiz", "yirmi dokuz",
    "otuz", "otuz bir", "otuz iki", "otuz üç", "otuz uc", "otuz dört",
    "otuz dort", "otuz beş", "otuz bes", "otuz altı", "otuz alti",
    "otuz yedi", "otuz sekiz", "otuz dokuz",
    "kırk", "kirk", "kırk bir", "kirk bir", "kırk iki", "kirk iki",
    "kırk üç", "kirk uc", "kırk dört", "kirk dort", "kırk beş", "kirk bes",
    "kırk altı", "kirk alti", "kırk yedi", "kirk yedi", "kırk sekiz",
    "kirk sekiz", "kırk dokuz", "kirk dokuz",
    "elli", "elli bir", "elli iki", "elli üç", "elli uc", "elli dört",
    "elli dort", "elli beş", "elli bes", "elli altı", "elli alti",
    "elli yedi", "elli sekiz", "elli dokuz", "altmış", "altmis",
)
NUMBER_WORD_PATTERN = "|".join(
    re.escape(item) for item in sorted(set(ENGLISH_NUMBER_WORDS + TURKISH_NUMBER_WORDS), key=len, reverse=True)
)
NUMBER_VALUE_TOKEN = r"(?:0?(?:[1-9]|[1-5][0-9]|60)|" + NUMBER_WORD_PATTERN + r")"
DRAW_SET_WORD_TEXT = re.compile(
    r"(?ix)(?<![\w.])" + NUMBER_VALUE_TOKEN
    + r"(?:(?:\s*[,;|/]\s*|\s+(?:and|ve)\s+)" + NUMBER_VALUE_TOKEN + r"){5}(?![\w])"
)
# Generic nouns such as "numbers" and "values" are deliberately not candidate
# cues by themselves. "Each MOD5 class contains 12 numbers" is a class-size
# statement, not a predicted or ranked lottery value.
CANDIDATE_CUE = (
    r"(?:candidate(?:s)?|prediction(?:s)?|predicted|winning|"
    r"rank(?:ed|ing)?|top(?:[- ]ranked)?|highest(?:[- ]ranked)?|best|first|"
    r"winner|selected|chosen|"
    r"aday(?:lar|ı|ın|ları)?(?:\s+(?:sayı(?:lar|ları|sı)?|sayi(?:lar|lari|si)?|"
    r"numara(?:lar|ları|sı)?|değer(?:ler|leri|i)?|deger(?:ler|leri|i)?))?|"
    r"tahmin(?:i|ler)?(?:\s+edilen)?(?:\s+(?:sayı(?:lar|ları|sı)?|sayi(?:lar|lari|si)?|"
    r"numara(?:lar|ları|sı)?|değer(?:ler|leri|i)?|deger(?:ler|leri|i)?))?|"
    r"kazanan(?:ı|lar|ları)?(?:\s+(?:sayı(?:lar|ları|sı)?|sayi(?:lar|lari|si)?|"
    r"numara(?:lar|ları|sı)?|değer(?:ler|leri|i)?|deger(?:ler|leri|i)?))?|"
    r"çıkan\s+(?:sayı|numara)|cikan\s+(?:sayi|numara)|"
    r"sıralama|siralama)"
)
CANDIDATE_CONNECTOR = (
    r"(?:the|number|numbers|value|values|no\.?|is|are|was|were|of|at|"
    r"include|includes|list|set|output|result|pick|picks|first|remains|only|a|an|"
    r"birinci|seçilen|secilen|olarak|edilen|değeri|degeri|numarası|numarasi|"
    r"sayı|sayıları|sayi|sayilari|numara|numaralar|değer|değerleri|deger|degerleri)"
)
CANDIDATE_VALUE_AFTER_CUE = re.compile(
    r"(?ix)\b" + CANDIDATE_CUE
    + r"(?:[\s,:#=]+" + CANDIDATE_CONNECTOR + r"){0,4}"
    + r"[\s:#=]*" + NUMBER_VALUE_TOKEN + r"\b"
)
CANDIDATE_VALUE_BEFORE_CUE = re.compile(
    r"(?ix)\b" + NUMBER_VALUE_TOKEN
    + r"(?:[\s,:#=]+" + CANDIDATE_CONNECTOR + r"){0,3}"
    + r"[\s:#=]+\b" + CANDIDATE_CUE + r"\b"
)


DRAWN_NUMBER_AFTER_LABEL = re.compile(
    r"(?ix)(?<![\w.])(?:the\s+)?(?:drawn|picked)\s+(?:lottery\s+)?"
    r"(?:number|value)\s*(?::|=|\#)?\s*(?:(?:is|was|equals?)\s+)?"
    + NUMBER_VALUE_TOKEN + r"\b"
)
DRAWN_NUMBER_BEFORE_VERB = re.compile(
    r"(?ix)(?<![\w.])" + NUMBER_VALUE_TOKEN
    + r"(?:\s+(?:was|has\s+been|is))?\s+(?:drawn|picked)\b"
)
DRAWN_NUMBER_TURKISH_AFTER_LABEL = re.compile(
    r"(?ix)(?<![\w.])(?:çekilen|cekilen|çekilmiş|cekilmis|"
    r"çekilişte\s+çıkan|cekiliste\s+cikan)\s+"
    r"(?:sayı|sayi|numara)(?:sı|si)?\s*(?::|=|\#)?\s*"
    + NUMBER_VALUE_TOKEN + r"\b"
)
DRAWN_NUMBER_TURKISH_BEFORE_VERB = re.compile(
    r"(?ix)(?<![\w.])" + NUMBER_VALUE_TOKEN
    + r"(?:\s+(?:numarası|numarasi|sayısı|sayisi))?\s+"
    r"(?:çekildi|cekildi|çekilmiştir|cekilmistir|çekilmiş|cekilmis)\b"
)
EXPLICIT_NUMBER_LIST_LABEL = re.compile(
    r"(?ix)\b(?:numbers?|values?|number|value|"
    r"sayı(?:lar|ları|sı)?|sayi(?:lar|lari|si)?|"
    r"numara(?:lar|ları|sı)?|değer(?:ler|leri|i)?|deger(?:ler|leri|i)?)"
    r"\s*(?::|=|\#|\bare\b|\bis\b|\binclude\b|\bincludes\b)"
    r"\s*[\[(]?\s*" + NUMBER_VALUE_TOKEN + r"\b"
)


ALLOWED_STATUSES = {
    "HYPOTHESES_CAPTURED",
    "NO_VALID_HYPOTHESIS",
    "EVALUATED",
    "CLAIMED_EVALUATION_FAILED",
    "REQUEST_FAILED",
    "RESEARCH_COMPLETE_UNEVALUATED",
}
METRIC_FIELDS = {
    "mean_hits_out_of_6",
    "standard_error_of_mean_hits",
    "uniform_random_expected_hits_out_of_6",
    "test_draw_count",
    "evaluated_draw_count",
    "validation_draw_count",
    "folds",
    "fold_count",
    "zero_hit_count",
    "skipped_draw_count",
}


class ResearchMemoryError(ValueError):
    """Raised when research-memory content fails provenance or safety checks."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _contains_six_number_set(value: Any) -> bool:
    if isinstance(value, str):
        return (
            DRAW_SET_TEXT.search(value) is not None
            or DRAW_SET_QUOTED_TEXT.search(value) is not None
            or DRAW_SET_FLEX_TEXT.search(value) is not None
            or DRAW_SET_WORD_TEXT.search(value) is not None
        )
    if isinstance(value, list):
        if (
            len(value) == 6
            and all(type(item) is int and 1 <= item <= 60 for item in value)
            and len(set(value)) == 6
        ):
            return True
        return any(_contains_six_number_set(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_six_number_set(item) for item in value.values())
    return False

def _reject_number_values(value: Any, label: str) -> None:
    if _contains_six_number_set(value):
        raise ResearchMemoryError(label + " contains a six-number value set; memory write refused.")


def _contains_candidate_value_reference(value: Any) -> bool:
    """Detect explicit candidate values and partial rankings in structured prose."""
    if isinstance(value, str):
        return (
            CANDIDATE_VALUE_AFTER_CUE.search(value) is not None
            or CANDIDATE_VALUE_BEFORE_CUE.search(value) is not None
            or EXPLICIT_NUMBER_LIST_LABEL.search(value) is not None
            or DRAWN_NUMBER_AFTER_LABEL.search(value) is not None
            or DRAWN_NUMBER_BEFORE_VERB.search(value) is not None
            or DRAWN_NUMBER_TURKISH_AFTER_LABEL.search(value) is not None
            or DRAWN_NUMBER_TURKISH_BEFORE_VERB.search(value) is not None
        )
    if isinstance(value, list):
        return any(_contains_candidate_value_reference(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_candidate_value_reference(item) for item in value.values())
    return False


def _reject_candidate_value_references(value: Any, label: str) -> None:
    if _contains_candidate_value_reference(value):
        raise ResearchMemoryError(
            label + " contains a candidate/predicted number value or partial ranking; memory write refused."
        )


def _report_dates_in_text(value: str) -> set[str]:
    """Normalize numeric, English-month, and Turkish-month date spellings."""
    normalized: set[str] = set()
    for token in REPORT_DATE_PATTERN.findall(value):
        try:
            if len(token) == 10 and token[4] in "-/":
                year, month, day = (int(part) for part in token.split(token[4]))
                normalized.add(date(year, month, day).isoformat())
            elif "." in token:
                day, month, year = (int(part) for part in token.split("."))
                normalized.add(date(year, month, day).isoformat())
            else:
                first, second, year = (int(part) for part in token.split("/"))
                # Slash dates are ambiguous across locales. Test both readings
                # and reject if either maps to a consumed validation date.
                for day, month in ((first, second), (second, first)):
                    try:
                        normalized.add(date(year, month, day).isoformat())
                    except ValueError:
                        continue
        except (TypeError, ValueError):
            continue
    for match in REPORT_TEXT_DATE_PATTERN.finditer(value):
        month_name = match.group("month_before") or match.group("month_after")
        day_value = match.group("day_before") or match.group("day_after")
        year_value = match.group("year_before") or match.group("year_after")
        try:
            parsed = date(int(year_value), ALL_MONTHS[month_name.casefold()], int(day_value))
        except (KeyError, TypeError, ValueError):
            continue
        normalized.add(parsed.isoformat())
    return normalized

def _redact_forbidden_validation_dates(
    value: Any,
    forbidden_dates: set[str] | None,
) -> Any:
    """Replace exact reserved/consumed date tokens in learning notes with a date-free marker.

    This is deliberately limited to learning-note text. Proposals and execution
    specifications continue to fail closed when they mention a forbidden date.
    Any date form the normalizer cannot match remains subject to the strict
    rejection check immediately after redaction.
    """
    forbidden = set(forbidden_dates or ())
    if not forbidden:
        return value

    if isinstance(value, str):
        matches = list(REPORT_DATE_PATTERN.finditer(value))
        matches.extend(REPORT_TEXT_DATE_PATTERN.finditer(value))
        redacted = value
        for match in sorted(matches, key=lambda item: item.start(), reverse=True):
            if _report_dates_in_text(match.group(0)).intersection(forbidden):
                redacted = (
                    redacted[:match.start()]
                    + "[reserved validation date omitted]"
                    + redacted[match.end():]
                )
        return redacted
    if isinstance(value, list):
        return [_redact_forbidden_validation_dates(item, forbidden) for item in value]
    if isinstance(value, dict):
        return {
            key: _redact_forbidden_validation_dates(item, forbidden)
            for key, item in value.items()
        }
    return value


def _reject_consumed_validation_dates(
    value: Any,
    forbidden_dates: set[str] | None,
    label: str,
) -> None:
    if not forbidden_dates:
        return
    serialized = _canonical(value).decode("utf-8")
    if _report_dates_in_text(serialized).intersection(forbidden_dates):
        raise ResearchMemoryError(label + " contains a consumed validation date or a reserved validation date; memory write refused.")


def _validate_sealed_plan_binding(
    plan: dict[str, Any],
    archive: dict[str, Any],
    ledger: dict[str, Any],
    claim: dict[str, Any] | None,
) -> None:
    """Prove that the redaction plan belongs to this exact archive/target."""
    if not isinstance(plan, dict) or not isinstance(archive, dict) or not isinstance(ledger, dict):
        raise ResearchMemoryError("Sealed-plan binding metadata is invalid.")
    if (
        plan.get("schema_version") != 1
        or plan.get("target_date") != archive.get("last_date")
        or plan.get("archive_sha256") != archive.get("archive_sha256")
        or plan.get("archive_record_count") != archive.get("record_count")
        or plan.get("target_numbers_exposed") is not False
        or plan.get("target_in_discovery") is not False
        or plan.get("target_in_validation") is not False
    ):
        raise ResearchMemoryError(
            "Sealed plan does not match the current archive/target isolation proof."
        )
    from general_sealed_evaluation import (
        get_used_validation_dates,
        public_plan,
        split_archive,
    )

    prior_used_dates = get_used_validation_dates(ledger)
    if isinstance(claim, dict) and claim.get("claim_created") is True:
        claim_id = claim.get("claim_id")
        if not isinstance(claim_id, str) or not HEX64.fullmatch(claim_id):
            raise ResearchMemoryError("Current sealed claim ID is missing or invalid.")
        matches = [
            item for item in ledger.get("entries", [])
            if isinstance(item, dict) and item.get("claim_id") == claim_id
        ]
        if len(matches) != 1:
            raise ResearchMemoryError("Current sealed claim is not uniquely present in the evaluation ledger.")
        current_claim = matches[0]
        if (
            current_claim.get("archive_sha256") != archive.get("archive_sha256")
            or current_claim.get("target_date") != archive.get("last_date")
        ):
            raise ResearchMemoryError("Current sealed claim does not belong to this archive/target.")
        prior_snapshot = current_claim.get("previously_consumed_validation_dates")
        if (
            not isinstance(prior_snapshot, list)
            or any(not isinstance(item, str) for item in prior_snapshot)
            or prior_snapshot != sorted(set(prior_snapshot))
        ):
            raise ResearchMemoryError("Current claim lacks a valid prior-consumption snapshot.")
        prior_used_dates = set(prior_snapshot)
    elif claim is not None and not isinstance(claim, dict):
        raise ResearchMemoryError("Current claim metadata is malformed.")

    expected = public_plan(
        split_archive(
            archive.get("rows"),
            archive_sha256=archive.get("archive_sha256", ""),
            used_validation_dates=prior_used_dates,
        )
    )
    if plan != expected:
        raise ResearchMemoryError(
            "Sealed plan differs from the archive-derived chronological partition."
        )


def _reserved_validation_dates(plan: dict[str, Any]) -> set[str]:
    """Expand the current sealed holdout windows so guessed dates cannot enter memory."""
    if not isinstance(plan, dict):
        raise ResearchMemoryError("Sealed-plan metadata is invalid for memory redaction.")
    regimes = plan.get("regimes")
    if not isinstance(regimes, dict) or not regimes:
        raise ResearchMemoryError("Sealed-plan regimes are missing for memory redaction.")
    reserved: set[str] = set()
    pairs = (
        ("validation_start_date", "validation_end_date"),
        ("reverse_validation_start_date", "reverse_validation_end_date"),
    )
    for regime_name, regime in regimes.items():
        if not isinstance(regime, dict):
            raise ResearchMemoryError("Sealed-plan regime metadata is invalid.")
        for start_key, end_key in pairs:
            start = regime.get(start_key)
            end = regime.get(end_key)
            if start is None and end is None:
                raise ResearchMemoryError("A sealed validation window is missing its bounds.")
            if not isinstance(start, str) or not isinstance(end, str):
                raise ResearchMemoryError("Sealed validation-window bounds are incomplete.")
            try:
                first = date.fromisoformat(start)
                last = date.fromisoformat(end)
            except ValueError:
                raise ResearchMemoryError("Sealed validation-window bounds are invalid.") from None
            if first.isoformat() != start or last.isoformat() != end or last < first:
                raise ResearchMemoryError("Sealed validation-window chronology is invalid.")
            span = (last - first).days
            if span > 3660:
                raise ResearchMemoryError("Sealed validation-window span exceeds memory redaction limits.")
            reserved.update(
                (first + timedelta(days=offset)).isoformat()
                for offset in range(span + 1)
            )
    return reserved


def empty_memory() -> dict[str, Any]:
    # Offline symbolic learning is a separate append-only namespace within the
    # existing research_memory state object. It is not a fifth state-store object.
    from general_offline_hypothesis_discovery import empty_memory as empty_offline_memory
    return {
        "schema_version": SCHEMA_VERSION,
        "project": PROJECT,
        "research_epoch": RESEARCH_EPOCH,
        "purpose": (
            "Append-only GENERAL-origin directional research memory. It may retain validated "
            "hypotheses, safe execution specifications, sanitized same-direction report narratives, "
            "and safe aggregate feedback. No legacy user/Pydroid experiments, raw archive rows, "
            "consumed holdout rows/dates, target values, or predicted number values are permitted."
        ),
        "entries": [],
        "offline_research": empty_offline_memory(),
    }


def _validate_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ResearchMemoryError("Memory timestamp is missing.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ResearchMemoryError("Memory timestamp is invalid.") from None
    if parsed.tzinfo is None:
        raise ResearchMemoryError("Memory timestamp must include a timezone.")
    return parsed.astimezone(timezone.utc)


def _entry_hash(entry: dict[str, Any]) -> str:
    body = {key: value for key, value in entry.items() if key != "content_sha256"}
    return _sha(_canonical(body))


def _validate_entry(entry: Any, expected_id: int) -> dict[str, Any]:
    required = {
        "entry_id", "cycle_id", "source", "research_epoch", "direction",
        "created_at_utc", "target_date", "archive_sha256", "runner_version",
        "status", "research_report_sha256", "research_report_content",
        "research_report_content_sha256", "research_report_content_status",
        "proposal_status", "proposals", "execution_spec_status", "execution_spec",
        "execution_spec_sha256",
        "learning_notes", "evaluation_feedback", "failure", "discovery_window",
        "content_sha256",
    }
    if not isinstance(entry, dict) or set(entry) != required:
        raise ResearchMemoryError("Research-memory entry keys do not match the schema.")
    if type(entry["entry_id"]) is not int or entry["entry_id"] != expected_id:
        raise ResearchMemoryError("Research-memory IDs must be continuous, unique, and increasing.")
    if not isinstance(entry["cycle_id"], str) or not HEX64.fullmatch(entry["cycle_id"]):
        raise ResearchMemoryError("Research-memory cycle ID is invalid.")
    if entry["source"] != SOURCE or entry["research_epoch"] != RESEARCH_EPOCH:
        raise ResearchMemoryError("Only this GENERAL research engine's own epoch may enter memory.")
    if entry["direction"] not in {"forward", "reverse"}:
        raise ResearchMemoryError("Research-memory direction must be forward or reverse.")
    _validate_timestamp(entry["created_at_utc"])
    try:
        day = date.fromisoformat(entry["target_date"])
    except (TypeError, ValueError):
        raise ResearchMemoryError("Research-memory target date is invalid.") from None
    if day.isoformat() != entry["target_date"]:
        raise ResearchMemoryError("Research-memory target date is not canonical.")
    if day < RESEARCH_SCOPE_START:
        raise ResearchMemoryError("Research-memory target is before the active 2020-08-02 scope.")
    if entry["archive_sha256"] is None:
        raise ResearchMemoryError("Research-memory archive provenance is required.")
    if entry["research_report_sha256"] is None and entry["status"] != "REQUEST_FAILED":
        raise ResearchMemoryError("A report-backed research entry must include its report hash.")
    for name in (
        "archive_sha256", "research_report_sha256",
        "research_report_content_sha256", "content_sha256",
    ):
        value = entry[name]
        if value is not None and (not isinstance(value, str) or not HEX64.fullmatch(value)):
            raise ResearchMemoryError("Research-memory " + name + " is invalid.")
    report_content_status = entry["research_report_content_status"]
    if report_content_status not in {
        "STORED", "OMITTED_SENSITIVE_CONTENT", "OMITTED_NUMERIC_CONTENT",
        "OMITTED_UNSUPPORTED_FORMAT", "OMITTED_SIZE_LIMIT", "NOT_AVAILABLE",
    }:
        raise ResearchMemoryError("Research-report content status is invalid.")
    report_content = entry["research_report_content"]
    if entry["research_report_sha256"] is None:
        if report_content_status != "NOT_AVAILABLE" or report_content is not None or entry["research_report_content_sha256"] is not None:
            raise ResearchMemoryError("A missing source report cannot have retained report content.")
    elif report_content_status == "NOT_AVAILABLE":
        raise ResearchMemoryError("A present source report cannot be marked unavailable.")
    if report_content_status == "STORED":
        if (
            not isinstance(report_content, str)
            or not 1 <= len(report_content) <= MAX_REPORT_CHARS
            or entry["research_report_content_sha256"] != _sha(report_content.encode("utf-8"))
        ):
            raise ResearchMemoryError("Stored research-report content or its hash is invalid.")
        _reject_number_values(report_content, "Stored research-report content")
        if re.search(r"[0-9]", report_content):
            raise ResearchMemoryError("Stored research-report content contains numeric content.")
        if REPORT_SECRET_PATTERN.search(report_content):
            raise ResearchMemoryError("Stored research-report content contains a secret-like value.")
    elif report_content is not None or entry["research_report_content_sha256"] is not None:
        raise ResearchMemoryError("Omitted research-report content must not retain text or a content hash.")
    if entry["status"] not in ALLOWED_STATUSES:
        raise ResearchMemoryError("Research-memory status is invalid.")
    if not isinstance(entry["runner_version"], str) or not entry["runner_version"]:
        raise ResearchMemoryError("Research runner version is missing.")
    if entry["proposal_status"] not in {
        "VALID_PROPOSALS", "VALID_EMPTY", "MISSING_BLOCK", "REJECTED",
        "REJECTED_DIRECTION_MISMATCH", "NOT_RUN",
    }:
        raise ResearchMemoryError("Proposal status is invalid.")
    if not isinstance(entry["proposals"], list) or len(entry["proposals"]) > 1:
        raise ResearchMemoryError("Stored proposal list must contain at most one atomic directional proposal.")
    seen_proposals = set()
    for proposal in entry["proposals"]:
        if not isinstance(proposal, dict) or set(proposal) != PROPOSAL_KEYS:
            raise ResearchMemoryError("Stored proposal does not match the validated proposal schema.")
        proposal_id = proposal.get("hypothesis_id")
        if not isinstance(proposal_id, str) or not re.fullmatch(r"HYP-[0-9]{3}", proposal_id):
            raise ResearchMemoryError("Stored proposal ID is invalid.")
        numeric_id = int(proposal_id[-3:])
        if (
            proposal_id in seen_proposals
            or (entry["direction"] == "forward" and not 1 <= numeric_id <= 5)
            or (entry["direction"] == "reverse" and not 501 <= numeric_id <= 505)
        ):
            raise ResearchMemoryError("Stored proposal ID does not preserve its directional namespace.")
        seen_proposals.add(proposal_id)
        if proposal.get("discovery_direction") != entry["direction"]:
            raise ResearchMemoryError("A directional memory entry contains another direction's proposal.")
        _reject_number_values(proposal, "Stored proposal")
        _reject_candidate_value_references(proposal, "Stored proposal")
        try:
            checked_proposal = _validate_proposal(proposal, canonical=True)
        except (TypeError, ValueError):
            raise ResearchMemoryError("Stored proposal failed the canonical proposal validator.") from None
        if {key: checked_proposal[key] for key in PROPOSAL_KEYS} != proposal:
            raise ResearchMemoryError("Stored proposal is not in canonical validated form.")
    if entry["execution_spec_status"] not in {
        "VALIDATED_SPECS", "VALID_EMPTY", "MISSING_BLOCK", "REJECTED", "NOT_RUN",
    }:
        raise ResearchMemoryError("Execution-spec status is invalid.")
    if entry["execution_spec"] is not None and not isinstance(entry["execution_spec"], dict):
        raise ResearchMemoryError("Stored execution specification is invalid.")
    if entry["execution_spec_sha256"] is not None and not HEX64.fullmatch(str(entry["execution_spec_sha256"])):
        raise ResearchMemoryError("Stored execution-spec hash is invalid.")
    if entry["execution_spec"] is not None:
        try:
            checked_spec = validate_execution_spec(entry["execution_spec"])
        except Exception:
            raise ResearchMemoryError("Stored execution specification failed the canonical DSL validator.") from None
        if checked_spec != entry["execution_spec"]:
            raise ResearchMemoryError("Stored execution specification is not canonical.")
        if (
            not entry["proposals"]
            or not execution_spec_matches_proposal(
                entry["proposals"][0], checked_spec
            )
        ):
            raise ResearchMemoryError(
                "Stored execution specification does not match the proposal's formal mathematical definition."
            )
        if entry["execution_spec_sha256"] != _sha(_canonical(entry["execution_spec"])):
            raise ResearchMemoryError("Stored execution-spec hash does not match its definition.")
    elif entry["execution_spec_sha256"] is not None:
        raise ResearchMemoryError("An absent execution specification cannot have a specification hash.")
    if (entry["execution_spec_status"] == "VALIDATED_SPECS") != (entry["execution_spec"] is not None):
        raise ResearchMemoryError("Execution-spec status and stored specification disagree.")
    if entry["proposal_status"] == "VALID_PROPOSALS" and not entry["proposals"]:
        raise ResearchMemoryError("A valid proposal status must contain proposals.")
    if entry["proposal_status"] == "VALID_EMPTY" and entry["proposals"]:
        raise ResearchMemoryError("A valid-empty proposal status cannot contain proposals.")
    if entry["proposal_status"] not in {"VALID_PROPOSALS", "VALID_EMPTY"} and entry["proposals"]:
        raise ResearchMemoryError("Rejected or missing proposal status cannot contain proposals.")
    if not isinstance(entry["learning_notes"], dict) or set(entry["learning_notes"]) != LESSON_KEYS:
        raise ResearchMemoryError("Stored learning notes are invalid.")
    for note_list in entry["learning_notes"].values():
        if not isinstance(note_list, list) or len(note_list) > MAX_LESSON_ITEMS:
            raise ResearchMemoryError("Stored learning-note lists are invalid.")
        if any(not isinstance(note, str) or not 5 <= len(note.strip()) <= MAX_LESSON_CHARS for note in note_list):
            raise ResearchMemoryError("Stored learning-note items are invalid.")
    _reject_number_values(entry["learning_notes"], "Stored learning notes")
    _reject_candidate_value_references(entry["learning_notes"], "Stored learning notes")
    if entry["evaluation_feedback"] is not None:
        feedback = entry["evaluation_feedback"]
        feedback_keys = {
            "status", "claim_id", "candidate_id", "spec_sha256", "holdout",
            "cross_validation", "holdout_consumed", "retrospective_only",
            "raw_holdout_dates_stored", "per_draw_results_stored",
            "draw_number_values_stored", "interpretation",
        }
        if not isinstance(feedback, dict) or set(feedback) != feedback_keys:
            raise ResearchMemoryError("Evaluation feedback does not match its safe summary schema.")
        if feedback.get("status") != "EVALUATED_EXPLORATORY_FIXED_SPEC":
            raise ResearchMemoryError("Evaluation feedback status is invalid.")
        if (
            not isinstance(feedback.get("claim_id"), str)
            or not HEX64.fullmatch(feedback["claim_id"])
            or not entry["proposals"]
            or feedback.get("candidate_id") != entry["proposals"][0]["hypothesis_id"]
            or feedback.get("spec_sha256") != entry["execution_spec_sha256"]
            or feedback.get("spec_sha256") is None
        ):
            raise ResearchMemoryError("Evaluation feedback is not bound to this direction's frozen candidate.")
        if not isinstance(feedback.get("interpretation"), str) or len(feedback["interpretation"]) > 300:
            raise ResearchMemoryError("Evaluation feedback interpretation is invalid.")
        for metric_group in ("holdout", "cross_validation"):
            group = feedback.get(metric_group)
            if not isinstance(group, dict) or set(group) - (METRIC_FIELDS | {"hit_distribution"}):
                raise ResearchMemoryError("Evaluation feedback contains non-aggregate fields.")
            for metric, metric_value in group.items():
                if metric == "hit_distribution":
                    if (
                        not isinstance(metric_value, dict)
                        or len(metric_value) > 8
                        or any(
                            not isinstance(key, str)
                            or len(key) > 12
                            or type(count) is not int
                            or not 0 <= count <= 10000
                            for key, count in metric_value.items()
                        )
                    ):
                        raise ResearchMemoryError("Evaluation hit distribution is invalid.")
                elif type(metric_value) not in {int, float} or not math.isfinite(float(metric_value)):
                    raise ResearchMemoryError("Evaluation aggregate metric is invalid.")
        if feedback.get("holdout_consumed") is not True:
            raise ResearchMemoryError("Evaluation feedback must mark the sealed holdout as permanently consumed.")
        expected_retrospective = entry["direction"] == "reverse"
        if feedback.get("retrospective_only") is not expected_retrospective:
            raise ResearchMemoryError("Evaluation feedback direction classification is invalid.")
        if any(feedback.get(flag) is not False for flag in (
            "raw_holdout_dates_stored", "per_draw_results_stored", "draw_number_values_stored"
        )):
            raise ResearchMemoryError("Evaluation feedback must not contain raw holdout details or values.")
        _reject_number_values(feedback, "Evaluation feedback")
    if entry["failure"] is not None:
        failure = entry["failure"]
        basic_failure_keys = {"stage", "error_type", "failure_metadata_only"}
        rejection_failure_keys = basic_failure_keys | {"proposal_reasons", "execution_spec_reasons"}
        if not isinstance(failure, dict) or frozenset(failure) not in {frozenset(basic_failure_keys), frozenset(rejection_failure_keys)}:
            raise ResearchMemoryError("Failure metadata does not match its safe schema.")
        if (
            not isinstance(failure.get("stage"), str)
            or not 3 <= len(failure["stage"]) <= 80
            or not isinstance(failure.get("error_type"), str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", failure["error_type"])
            or failure.get("failure_metadata_only") is not True
        ):
            raise ResearchMemoryError("Failure metadata contains an invalid stage/type.")
        for field in ("proposal_reasons", "execution_spec_reasons"):
            if field in failure:
                values = failure[field]
                if (
                    not isinstance(values, list)
                    or len(values) > 4
                    or any(not isinstance(item, str) or len(item) > 240 for item in values)
                ):
                    raise ResearchMemoryError("Structured rejection metadata is invalid.")
        _reject_number_values(failure, "Failure metadata")
    window = entry["discovery_window"]
    if not isinstance(window, dict) or set(window) != {
        "start_date", "end_date", "draw_count", "holdout_dates_copied", "archive_rows_copied"
    }:
        raise ResearchMemoryError("Discovery-window metadata is invalid.")
    if window.get("holdout_dates_copied") is not False or window.get("archive_rows_copied") is not False:
        raise ResearchMemoryError("Raw holdout/archive rows cannot be copied into research memory.")
    count = window.get("draw_count")
    if count is not None and (type(count) is not int or count < 0):
        raise ResearchMemoryError("Discovery-window draw count is invalid.")
    start = window.get("start_date")
    end = window.get("end_date")
    if (start is None) != (end is None):
        raise ResearchMemoryError("Discovery-window date bounds must both be present or both be null.")
    if start is None:
        if count not in {None, 0}:
            raise ResearchMemoryError("A non-empty discovery window must have date bounds.")
    else:
        try:
            start_day = date.fromisoformat(start)
            end_day = date.fromisoformat(end)
        except (TypeError, ValueError):
            raise ResearchMemoryError("Discovery-window dates are invalid.") from None
        if (
            start_day.isoformat() != start
            or end_day.isoformat() != end
            or start_day < RESEARCH_SCOPE_START
            or end_day < start_day
            or end_day >= day
            or count is None
            or count < 1
        ):
            raise ResearchMemoryError("Discovery-window chronology or scope is invalid.")
    _reject_number_values(window, "Discovery-window metadata")
    if REPORT_SECRET_PATTERN.search(_canonical(entry).decode("utf-8")):
        raise ResearchMemoryError("Research-memory entry contains a secret-like value.")
    if entry["content_sha256"] != _entry_hash(entry):
        raise ResearchMemoryError("Research-memory entry hash mismatch.")
    return entry


def load_memory(path: str | Path = MEMORY_PATH) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return empty_memory()
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ResearchMemoryError("Research-memory JSON contains a duplicate object key.")
            result[key] = value
        return result

    try:
        payload = json.loads(
            target.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ResearchMemoryError("Research-memory JSON contains a non-finite number.")
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ResearchMemoryError("Research-memory file could not be read as valid UTF-8 JSON.") from None
    legacy_keys = {"schema_version", "project", "research_epoch", "purpose", "entries"}
    current_keys = legacy_keys | {"offline_research"}
    if not isinstance(payload, dict):
        raise ResearchMemoryError("Research-memory file has an invalid schema; failing closed.")
    # Existing schema-v2 state is upgraded in memory only. The next legitimate
    # append through the established research_memory state object persists v3.
    if payload.get("schema_version") == 2 and set(payload) == legacy_keys:
        payload = dict(payload)
        payload["schema_version"] = SCHEMA_VERSION
        from general_offline_hypothesis_discovery import empty_memory as empty_offline_memory
        payload["offline_research"] = empty_offline_memory()
    if (
        set(payload) != current_keys
        or type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("project") != PROJECT
        or payload.get("research_epoch") != RESEARCH_EPOCH
        or not isinstance(payload.get("purpose"), str)
        or not isinstance(payload.get("entries"), list)
        or len(payload["entries"]) > MAX_ENTRIES
    ):
        raise ResearchMemoryError("Research-memory file has an invalid schema; failing closed.")
    from general_offline_hypothesis_discovery import validate_memory_document
    try:
        validate_memory_document(payload["offline_research"])
    except Exception as exc:
        raise ResearchMemoryError(
            "Offline research-memory namespace failed its independent validator: "
            + type(exc).__name__
        ) from None
    seen_cycles: set[str] = set()
    previous_target: date | None = None
    for index, entry in enumerate(payload["entries"], start=1):
        checked = _validate_entry(entry, index)
        if checked["cycle_id"] in seen_cycles:
            raise ResearchMemoryError("Research-memory cycle IDs must be unique.")
        seen_cycles.add(checked["cycle_id"])
        current_target = date.fromisoformat(checked["target_date"])
        if previous_target is not None and current_target < previous_target:
            raise ResearchMemoryError("Research-memory entries must remain chronological.")
        previous_target = current_target
    return payload


def append_offline_research_memory(
    updated_offline: dict[str, Any],
    *,
    path: str | Path = MEMORY_PATH,
) -> dict[str, Any]:
    """Append one validated offline cycle inside the existing research_memory state object."""
    from general_offline_hypothesis_discovery import validate_memory_document
    current = load_memory(path)
    old_offline = current["offline_research"]
    checked_offline = validate_memory_document(updated_offline)
    old_entries = old_offline["entries"]
    old_windows = old_offline["consumed_development_windows"]
    if (
        checked_offline["entries"][:len(old_entries)] != old_entries
        or checked_offline["consumed_development_windows"][:len(old_windows)] != old_windows
        or len(checked_offline["entries"]) != len(old_entries) + 1
        or len(checked_offline["consumed_development_windows"]) != len(old_windows) + 1
    ):
        raise ResearchMemoryError("Offline learning must append exactly one new cycle without changing its prefix.")
    updated = dict(current)
    updated["offline_research"] = checked_offline
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(
        json.dumps(updated, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    verified = load_memory(temporary)
    if verified != updated:
        raise ResearchMemoryError("Combined research-memory append failed read-back validation.")
    temporary.replace(target)
    return {
        "status": "APPENDED",
        "offline_entries": len(checked_offline["entries"]),
        "sealed_entries_unchanged": True,
    }


def _parse_learning_notes(report: str) -> dict[str, list[str]]:
    empty = {key: [] for key in LESSON_KEYS}
    if report.count(LESSONS_START) == 0 and report.count(LESSONS_END) == 0:
        return empty
    if report.count(LESSONS_START) != 1 or report.count(LESSONS_END) != 1:
        raise ResearchMemoryError("Learning-notes block markers are duplicated or incomplete.")
    start = report.index(LESSONS_START) + len(LESSONS_START)
    end = report.index(LESSONS_END)
    if end <= start:
        raise ResearchMemoryError("Learning-notes block markers are out of order.")
    try:
        value = json.loads(report[start:end].strip())
    except json.JSONDecodeError:
        raise ResearchMemoryError("Learning-notes block is not valid JSON.") from None
    if not isinstance(value, dict) or set(value) != {"schema_version", *LESSON_KEYS}:
        raise ResearchMemoryError("Learning-notes block keys are invalid.")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ResearchMemoryError("Learning-notes schema version is unsupported.")
    result: dict[str, list[str]] = {}
    for key in LESSON_KEYS:
        items = value[key]
        if not isinstance(items, list) or len(items) > MAX_LESSON_ITEMS:
            raise ResearchMemoryError("Learning-notes " + key + " list is invalid.")
        checked = []
        for item in items:
            if not isinstance(item, str) or not 5 <= len(item.strip()) <= MAX_LESSON_CHARS:
                raise ResearchMemoryError("Learning-notes items must be bounded text.")
            checked.append(item.strip())
        result[key] = checked
    _reject_number_values(result, "Learning-notes block")
    _reject_candidate_value_references(result, "Learning-notes block")
    return result


def _sanitize_research_report(
    report: str,
    *,
    forbidden_validation_dates: set[str] | None = None,
) -> tuple[str | None, str]:
    """Retain complete safe report prose after separating validated machine blocks.

    Structured proposals, execution specs, learning notes, and rule-change requests
    are not copied as raw text. Their permitted fields are handled by their own
    validators or their separate approval path.
    """
    if not isinstance(report, str):
        return None, "OMITTED_UNSUPPORTED_FORMAT"
    normalized = report.replace("\r\n", "\n").replace("\r", "\n")
    if len(normalized) > MAX_REPORT_CHARS:
        return None, "OMITTED_SIZE_LIMIT"

    blocks = (
        ("<RULE_CHANGE_REQUEST_JSON>", "</RULE_CHANGE_REQUEST_JSON>",
         "[Structured rule-change request omitted from research memory; separate approval path applies.]"),
        ("<HYPOTHESIS_PROPOSALS_JSON>", "</HYPOTHESIS_PROPOSALS_JSON>",
         "[Validated hypothesis proposals are stored in their structured directional fields.]"),
        ("<HYPOTHESIS_EXECUTION_SPECS_JSON>", "</HYPOTHESIS_EXECUTION_SPECS_JSON>",
         "[Validated execution specification is stored in its structured directional field.]"),
        (LESSONS_START, LESSONS_END,
         "[Validated learning notes are stored in their structured directional field.]"),
    )
    for opening, closing, placeholder in blocks:
        open_count = normalized.count(opening)
        close_count = normalized.count(closing)
        if open_count == 0 and close_count == 0:
            continue
        if open_count != 1 or close_count != 1:
            return None, "OMITTED_UNSUPPORTED_FORMAT"
        start = normalized.index(opening)
        end = normalized.index(closing)
        if end < start + len(opening):
            return None, "OMITTED_UNSUPPORTED_FORMAT"
        normalized = (
            normalized[:start]
            + "\n"
            + placeholder
            + "\n"
            + normalized[end + len(closing):]
        )

    normalized = normalized.strip()
    if not normalized:
        return None, "OMITTED_UNSUPPORTED_FORMAT"

    # Reports are public repository content. Fail closed rather than retaining
    # number sets, already-consumed holdout dates, or secret-like strings.
    if _contains_six_number_set(normalized):
        return None, "OMITTED_SENSITIVE_CONTENT"
    forbidden = forbidden_validation_dates or set()
    report_dates = _report_dates_in_text(normalized)
    if report_dates.intersection(forbidden):
        return None, "OMITTED_SENSITIVE_CONTENT"
    if REPORT_SECRET_PATTERN.search(normalized):
        return None, "OMITTED_SENSITIVE_CONTENT"
    # Free-form narrative is retained only when it has no Arabic numerals.
    # Aggregate metrics and validated hypothesis/spec fields live separately;
    # this prevents numeric candidate rankings and unstructured value lists
    # from entering persistent prompt context.
    if re.search(r"[0-9]", normalized):
        return None, "OMITTED_NUMERIC_CONTENT"
    return normalized, "STORED"


def _safe_metric_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    summary = {}
    for key in METRIC_FIELDS:
        item = value.get(key)
        if type(item) in {int, float} and math.isfinite(float(item)):
            summary[key] = item
    distribution = value.get("hit_distribution")
    if isinstance(distribution, dict) and len(distribution) <= 8:
        safe_distribution = {}
        for key, count in distribution.items():
            if (
                isinstance(key, str)
                and len(key) <= 12
                and type(count) is int
                and 0 <= count <= 10000
            ):
                safe_distribution[key] = count
        if safe_distribution:
            summary["hit_distribution"] = safe_distribution
    return summary


def _extract_evaluation_feedback(
    report: dict[str, Any],
    direction: str,
) -> dict[str, Any]:
    if report.get("status") != "EVALUATED_EXPLORATORY_FIXED_SPEC":
        raise ResearchMemoryError("Evaluation report status is not a completed exploratory evaluation.")
    if (
        report.get("current_isolated_target_included") is not False
        or report.get("current_target_numbers_exposed") is not False
        or report.get("target_or_future_rows_used") is not False
        or report.get("per_draw_target_numbers_reported") is not False
        or report.get("candidate_rankings_reported") is not False
    ):
        raise ResearchMemoryError("Evaluation report failed target/number-value safety flags.")
    selection = report.get("candidate_selection")
    if not isinstance(selection, dict) or selection.get("selection_used_validation_scores") is not False:
        raise ResearchMemoryError("Evaluation report candidate-freeze metadata is invalid.")
    if direction == "forward":
        candidate_id = selection.get("forward_hypothesis_id")
        spec_hash = selection.get("forward_spec_sha256")
        holdout = _safe_metric_summary(report.get("forward_walk_forward"))
        cv = _safe_metric_summary(report.get("time_ordered_cross_validation"))
        retrospective_only = False
    else:
        candidate_id = selection.get("reverse_hypothesis_id")
        spec_hash = selection.get("reverse_spec_sha256")
        holdout = _safe_metric_summary(report.get("reverse_discovered_candidate_holdout"))
        cv = _safe_metric_summary(report.get("reverse_time_ordered_cross_validation"))
        retrospective_only = True
    if not isinstance(candidate_id, str) or not isinstance(spec_hash, str) or not HEX64.fullmatch(spec_hash):
        raise ResearchMemoryError("Directional evaluation candidate provenance is invalid.")
    return {
        "status": report["status"],
        "claim_id": report.get("claim_id"),
        "candidate_id": candidate_id,
        "spec_sha256": spec_hash,
        "holdout": holdout,
        "cross_validation": cv,
        "holdout_consumed": True,
        "retrospective_only": retrospective_only,
        "raw_holdout_dates_stored": False,
        "per_draw_results_stored": False,
        "draw_number_values_stored": False,
        "interpretation": (
            "Forward chronological evaluation feedback; its sealed interval is permanently consumed."
            if direction == "forward"
            else "Reverse result is retrospective-only feedback; its sealed interval is permanently consumed."
        ),
    }


def _load_optional_json(path: str | Path) -> dict[str, Any] | None:
    file_path = Path(path)
    if not file_path.is_file():
        return None
    try:
        value = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ResearchMemoryError(file_path.name + " is not valid JSON.") from None
    return value if isinstance(value, dict) else None


def _direction_artifacts(
    direction: str,
    report_path: str | Path,
    *,
    forbidden_validation_dates: set[str] | None = None,
) -> tuple[str | None, str | None, str, dict[str, Any], dict[str, Any], dict[str, list[str]]]:
    path = Path(report_path)
    if not path.is_file():
        return None, None, "NOT_AVAILABLE", {
            "status": "NOT_RUN", "proposals": [],
        }, {
            "status": "NOT_RUN", "specifications": [],
        }, {key: [] for key in LESSON_KEYS}
    try:
        report = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise ResearchMemoryError("Directional model report could not be read.") from None
    proposals = parse_hypothesis_proposals(report)
    if proposals["status"] in {"VALID_PROPOSALS", "VALID_EMPTY"} and any(
        item.get("discovery_direction") != direction for item in proposals["proposals"]
    ):
        proposals["status"] = "REJECTED_DIRECTION_MISMATCH"
        proposals["proposals"] = []
        proposals["rejection_reasons"] = ["Proposal direction did not match the isolated request."]
    allowed_ids = {item["hypothesis_id"] for item in proposals["proposals"]}
    specs = parse_execution_spec_block(report, allowed_hypothesis_ids=allowed_ids)
    if len(specs["specifications"]) > 1:
        specs["status"] = "REJECTED"
        specs["specifications"] = []
        specs["rejection_reasons"] = ["Only one specification is permitted per directional request."]
    if any(item.get("hypothesis_id") not in allowed_ids for item in specs["specifications"]):
        specs["status"] = "REJECTED"
        specs["specifications"] = []
        specs["rejection_reasons"] = ["Execution specification is not attached to this direction's proposal."]
    if specs["specifications"]:
        proposals_by_id = {
            item["hypothesis_id"]: item for item in proposals["proposals"]
        }
        if any(
            not execution_spec_matches_proposal(
                proposals_by_id.get(item["hypothesis_id"], {}),
                item["execution_spec"],
            )
            for item in specs["specifications"]
        ):
            specs["status"] = "REJECTED"
            specs["specifications"] = []
            specs["rejection_reasons"] = [
                "Execution specification does not exactly match the proposal's formal mathematical definition."
            ]
    if specs["status"] == "VALIDATED_SPECS" and (
        not proposals["proposals"]
        or len(specs["specifications"]) != 1
        or specs["specifications"][0].get("hypothesis_id") != proposals["proposals"][0]["hypothesis_id"]
    ):
        specs["status"] = "REJECTED"
        specs["specifications"] = []
        specs["rejection_reasons"] = ["Only a specification for this direction's first proposal can be retained."]
    notes = _parse_learning_notes(report)
    # Learning notes may describe the active research boundary, which can also
    # be a real reserved reverse-holdout date. Remove only exact forbidden date
    # tokens here; proposal/specification date references remain hard failures.
    notes = _redact_forbidden_validation_dates(notes, forbidden_validation_dates)
    _reject_number_values(proposals["proposals"], "Directional proposals")
    _reject_candidate_value_references(proposals["proposals"], "Directional proposals")
    _reject_number_values(specs["specifications"], "Directional specifications")
    _reject_number_values(notes, "Directional learning notes")
    _reject_candidate_value_references(notes, "Directional learning notes")
    _reject_consumed_validation_dates(
        proposals["proposals"], forbidden_validation_dates, "Directional proposals"
    )
    _reject_consumed_validation_dates(
        specs["specifications"], forbidden_validation_dates, "Directional specifications"
    )
    _reject_consumed_validation_dates(
        notes, forbidden_validation_dates, "Directional learning notes"
    )
    safe_report, safe_report_status = _sanitize_research_report(
        report,
        forbidden_validation_dates=forbidden_validation_dates,
    )
    return report, safe_report, safe_report_status, proposals, specs, notes

def _build_entry(
    *,
    entry_id: int,
    run_id: str,
    run_attempt: str,
    target_date: str,
    archive_sha256: str,
    direction: str,
    report_path: str | Path,
    discovery_window: dict[str, Any],
    outcome: str,
    failed_direction: str | None,
    failed_stage: str | None,
    error_type: str | None,
    shared_failure: bool,
    evaluation: dict[str, Any] | None,
    forbidden_validation_dates: set[str] | None = None,
) -> dict[str, Any]:
    (
        report, safe_report, safe_report_status, proposal_doc, specs_doc, notes
    ) = _direction_artifacts(
        direction,
        report_path,
        forbidden_validation_dates=forbidden_validation_dates,
    )
    has_report = report is not None
    source_proposals = proposal_doc.get("proposals", [])
    offset = 0 if direction == "forward" else 500
    proposals = []
    for index, item in enumerate(source_proposals):
        mapped = {key: item[key] for key in PROPOSAL_KEYS}
        mapped["hypothesis_id"] = "HYP-" + str(offset + index + 1).zfill(3)
        proposals.append(mapped)
    source_first_id = source_proposals[0]["hypothesis_id"] if source_proposals else None
    first_id = proposals[0]["hypothesis_id"] if proposals else None
    spec_items = specs_doc.get("specifications", [])
    matched_spec = next(
        (item for item in spec_items if item.get("hypothesis_id") == source_first_id),
        None,
    )
    spec = matched_spec.get("execution_spec") if matched_spec else None
    spec_hash = _sha(_canonical(spec)) if isinstance(spec, dict) else None
    feedback = _extract_evaluation_feedback(evaluation, direction) if evaluation is not None else None
    if feedback is not None:
        if feedback["candidate_id"] != first_id or feedback["spec_sha256"] != spec_hash:
            raise ResearchMemoryError("Evaluation feedback does not match this direction's stored frozen candidate.")
        status = "EVALUATED"
    elif not has_report and failed_direction == direction:
        status = "REQUEST_FAILED"
    elif outcome == "CLAIMED_EVALUATION_FAILED" and has_report:
        status = "CLAIMED_EVALUATION_FAILED"
    elif proposals:
        status = "HYPOTHESES_CAPTURED"
    elif has_report:
        status = "NO_VALID_HYPOTHESIS"
    else:
        status = "REQUEST_FAILED"
    failure = None
    if failed_direction == direction or status == "CLAIMED_EVALUATION_FAILED" or shared_failure:
        failure = {
            "stage": failed_stage or ("sealed_evaluation" if status == "CLAIMED_EVALUATION_FAILED" else "model_request"),
            "error_type": error_type or ("EvaluationIncomplete" if status == "CLAIMED_EVALUATION_FAILED" else "UnknownRequestFailure"),
            "failure_metadata_only": True,
        }
    elif proposal_doc.get("rejection_reasons") or specs_doc.get("rejection_reasons"):
        failure = {
            "stage": "response_contract_validation",
            "error_type": "StructuredOutputRejected",
            "proposal_reasons": proposal_doc.get("rejection_reasons", [])[:4],
            "execution_spec_reasons": specs_doc.get("rejection_reasons", [])[:4],
            "failure_metadata_only": True,
        }
    entry = {
        "entry_id": entry_id,
        "cycle_id": _sha((run_id + "|" + run_attempt + "|" + target_date + "|" + archive_sha256 + "|" + direction).encode()),
        "source": SOURCE,
        "research_epoch": RESEARCH_EPOCH,
        "direction": direction,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_date": target_date,
        "archive_sha256": archive_sha256,
        "runner_version": "0.9.0",
        "status": status,
        "research_report_sha256": _sha(report.encode("utf-8")) if report is not None else None,
        "research_report_content": safe_report,
        "research_report_content_sha256": _sha(safe_report.encode("utf-8")) if safe_report is not None else None,
        "research_report_content_status": safe_report_status,
        "proposal_status": proposal_doc.get("status", "NOT_RUN"),
        "proposals": proposals,
        "execution_spec_status": specs_doc.get("status", "NOT_RUN"),
        "execution_spec": spec,
        "execution_spec_sha256": spec_hash,
        "learning_notes": notes,
        "evaluation_feedback": feedback,
        "failure": failure,
        "discovery_window": discovery_window,
    }
    _reject_number_values(entry, "Research-memory entry")
    entry["content_sha256"] = _entry_hash(entry)
    _validate_entry(entry, entry_id)
    return entry


def record_cycle(
    *,
    memory_path: str | Path = MEMORY_PATH,
    archive_path: str | Path = ARCHIVE_PATH,
    ledger_path: str | Path = LEDGER_PATH,
    run_status_path: str | Path = RUN_STATUS_PATH,
    forward_report_path: str | Path = FORWARD_REPORT_PATH,
    reverse_report_path: str | Path = REVERSE_REPORT_PATH,
    sealed_plan_path: str | Path = SEALED_PLAN_PATH,
    claim_path: str | Path = CLAIM_PATH,
    evaluation_path: str | Path = EVALUATION_PATH,
    run_id: str,
    run_attempt: str = "1",
    failure_stage_override: str | None = None,
    error_type_override: str | None = None,
) -> dict[str, Any]:
    """Append one entry per completed/failed direction; raw draws never enter this function."""
    if not run_id or not run_attempt:
        raise ResearchMemoryError("GitHub run ID and attempt are required for idempotent memory records.")
    from general_sealed_evaluation import get_used_validation_dates, load_archive, load_ledger

    archive = load_archive(archive_path)
    target = archive["last_date"]
    archive_hash = archive["archive_sha256"]
    ledger = load_ledger(ledger_path)
    used_validation_dates = get_used_validation_dates(ledger)
    run_status = _load_optional_json(run_status_path) or {}
    claim = _load_optional_json(claim_path)
    evaluation_report = _load_optional_json(evaluation_path)
    if evaluation_report is not None and (
        not isinstance(claim, dict) or claim.get("claim_created") is not True
    ):
        raise ResearchMemoryError("An evaluation report without its successful current-cycle claim cannot enter memory.")
    plan = _load_optional_json(sealed_plan_path) or {}
    has_any_directional_report = (
        Path(forward_report_path).is_file() or Path(reverse_report_path).is_file()
    )
    needs_directional_record = (
        has_any_directional_report
        or run_status.get("failed_direction") in {"forward", "reverse"}
    )
    if needs_directional_record:
        _validate_sealed_plan_binding(plan, archive, ledger, claim)
    reserved_report_dates = _reserved_validation_dates(plan) if needs_directional_record else set()
    forbidden_report_dates = used_validation_dates | reserved_report_dates
    r60 = plan.get("regimes", {}).get("R60", {}) if isinstance(plan.get("regimes"), dict) else {}
    discovery_window = {
        "start_date": r60.get("discovery_start_date"),
        "end_date": r60.get("discovery_end_date"),
        "draw_count": r60.get("discovery_count"),
        "holdout_dates_copied": False,
        "archive_rows_copied": False,
    }
    if needs_directional_record:
        if (
            not isinstance(discovery_window["start_date"], str)
            or not isinstance(discovery_window["end_date"], str)
            or type(discovery_window["draw_count"]) is not int
            or discovery_window["draw_count"] < 1
        ):
            raise ResearchMemoryError("A directional result requires its verified sealed discovery-window metadata.")
    outcome = "RESEARCH_COMPLETE_UNEVALUATED"
    claim_id = None
    if isinstance(claim, dict) and claim.get("claim_created") is True:
        claim_id = claim.get("claim_id")
    elif evaluation_report is not None:
        claim_id = evaluation_report.get("claim_id")
    ledger_entry = next(
        (item for item in ledger["entries"] if item.get("claim_id") == claim_id),
        None,
    ) if claim_id else None

    if evaluation_report is not None:
        if (
            ledger_entry is None
            or ledger_entry.get("archive_sha256") != archive_hash
            or ledger_entry.get("target_date") != target
        ):
            raise ResearchMemoryError("Evaluation report claim is not backed by the current append-only ledger.")
        if ledger_entry.get("state") == "EVALUATED":
            if (
                evaluation_report.get("archive_sha256") != archive_hash
                or evaluation_report.get("target_date") != target
            ):
                raise ResearchMemoryError("Evaluation report archive provenance does not match the ledger.")
            # The workflow stages any pending ledger/snapshot changes together
            # with this memory append, so an evaluated state and its feedback
            # become persistent in the same repository push.
            outcome = "EVALUATED"
        elif ledger_entry.get("state") == "CLAIMED":
            # The report may have been written immediately before a crash, but
            # the append-only ledger is authoritative. Do not retain its scores.
            evaluation_report = None
            outcome = "CLAIMED_EVALUATION_FAILED"
        else:
            raise ResearchMemoryError("Evaluation ledger state is not eligible for memory publication.")
    elif isinstance(claim, dict) and claim.get("claim_created") is True:
        if ledger_entry is None or ledger_entry.get("state") not in {"CLAIMED", "EVALUATED"}:
            raise ResearchMemoryError("Claimed holdout is not present in the append-only ledger.")
        if (
            ledger_entry.get("archive_sha256") != archive_hash
            or ledger_entry.get("target_date") != target
        ):
            raise ResearchMemoryError("Claimed holdout provenance does not match the current archive.")
        if ledger_entry.get("state") == "EVALUATED":
            raise ResearchMemoryError("An evaluated ledger claim is missing its evaluation report.")
        outcome = "CLAIMED_EVALUATION_FAILED"
    elif isinstance(claim, dict) and claim.get("claim_created") is False:
        outcome = "NO_ELIGIBLE_CANDIDATE"

    failed_direction = run_status.get("failed_direction")
    failed_stage = failure_stage_override or run_status.get("stage")
    error_type = error_type_override or run_status.get("error_type")
    if failed_direction not in {None, "forward", "reverse"}:
        failed_direction = None
    if failure_stage_override:
        if not isinstance(failure_stage_override, str) or not 3 <= len(failure_stage_override) <= 80:
            raise ResearchMemoryError("Workflow failure-stage override is invalid.")
        run_status = dict(run_status)
        run_status["status"] = "FAILED"
    if run_status:
        if run_status.get("target_date") not in {None, target}:
            raise ResearchMemoryError("Run-status target does not match the validated archive.")
        if run_status.get("archive_sha256") not in {None, archive_hash}:
            raise ResearchMemoryError("Run-status archive hash does not match the validated archive.")
    if run_status.get("status") == "FAILED" and not failed_direction and not has_any_directional_report:
        # Failures before a directional request are not research observations.
        return {"status": "SKIPPED_NO_DIRECTIONAL_FAILURE", "entries_added": 0}
    shared_failure = run_status.get("status") == "FAILED" and failed_direction is None

    memory = load_memory(memory_path)
    next_id = len(memory["entries"]) + 1
    additions = []
    for direction, report_path in (
        ("forward", forward_report_path),
        ("reverse", reverse_report_path),
    ):
        report_exists = Path(report_path).is_file()
        if not report_exists and failed_direction != direction:
            continue
        direction_eval = evaluation_report if evaluation_report is not None else None
        entry = _build_entry(
            entry_id=next_id + len(additions),
            run_id=run_id,
            run_attempt=run_attempt,
            target_date=target,
            archive_sha256=archive_hash,
            direction=direction,
            report_path=report_path,
            discovery_window=discovery_window,
            outcome=outcome,
            failed_direction=failed_direction,
            failed_stage=failed_stage,
            error_type=error_type,
            shared_failure=shared_failure,
            evaluation=direction_eval,
            forbidden_validation_dates=forbidden_report_dates,
        )
        if any(old["cycle_id"] == entry["cycle_id"] for old in memory["entries"]):
            raise ResearchMemoryError("This run/direction already exists in memory; refusing a duplicate append.")
        additions.append(entry)
    if not additions:
        return {"status": "NO_DIRECTIONAL_RECORDS", "entries_added": 0}
    if len(memory["entries"]) + len(additions) > MAX_ENTRIES:
        raise ResearchMemoryError("Research memory reached its safety limit; no entries were written.")
    memory["entries"].extend(additions)
    _write_memory(memory, memory_path)
    return {
        "status": "APPENDED",
        "entries_added": len(additions),
        "forward_status": next((item["status"] for item in additions if item["direction"] == "forward"), None),
        "reverse_status": next((item["status"] for item in additions if item["direction"] == "reverse"), None),
        "raw_archive_rows_stored": False,
        "target_or_predicted_numbers_stored": False,
        "consumed_holdout_rows_stored": False,
    }


def _write_memory(memory: dict[str, Any], path: str | Path) -> None:
    target = Path(path)
    checked = load_memory(target)
    if (
        memory.keys() != checked.keys()
        or memory["entries"][:len(checked["entries"])] != checked["entries"]
        or memory["offline_research"] != checked["offline_research"]
    ):
        raise ResearchMemoryError("Memory update is not append-only; sealed entries or the offline namespace changed.")
    if len(memory["entries"]) < len(checked["entries"]):
        raise ResearchMemoryError("Memory entries cannot be deleted.")
    # The persisted prefix was already validated by load_memory above and is
    # immutable. Start chronology checks at the first genuinely new entry;
    # rechecking entry 1 against the final persisted target would reject every
    # valid append once the store contains multiple target dates.
    existing_count = len(checked["entries"])
    previous_target = (
        date.fromisoformat(checked["entries"][-1]["target_date"])
        if existing_count else None
    )
    for offset, entry in enumerate(memory["entries"][existing_count:], start=existing_count + 1):
        _validate_entry(entry, offset)
        current_target = date.fromisoformat(entry["target_date"])
        if previous_target is not None and current_target < previous_target:
            raise ResearchMemoryError("Research-memory append would move chronology backwards.")
        previous_target = current_target
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(json.dumps(memory, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(target)


def _compact_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    """Keep the useful mathematical core while bounding prompt size."""
    limits = {
        "proposed_family": 180,
        "hypothesis": 420,
        "exact_definition": 850,
        "inputs": 280,
        "output_rule": 420,
        "parameter_policy": 280,
        "selection_policy": 280,
        "falsification_criterion": 280,
    }
    compact = {
        "hypothesis_id": proposal["hypothesis_id"],
        "discovery_direction": proposal["discovery_direction"],
        "truncated_fields": [],
    }
    for key, limit in limits.items():
        value = proposal.get(key)
        if isinstance(value, str) and len(value) > limit:
            compact[key] = value[:limit - 16] + " [TRUNCATED]"
            compact["truncated_fields"].append(key)
        else:
            compact[key] = value
    risks = proposal.get("known_risks", [])
    compact["known_risks"] = []
    for item in risks[:2]:
        if not isinstance(item, str):
            continue
        if len(item) > 160:
            compact["known_risks"].append(item[:144] + " [TRUNCATED]")
            compact["truncated_fields"].append("known_risks")
        else:
            compact["known_risks"].append(item)
    compact["evaluation_protocols"] = list(proposal.get("evaluation_protocols", []))
    return compact





def direction_context(
    direction: str,
    target_date: str,
    *,
    memory_path: str | Path = MEMORY_PATH,
    limit: int = MAX_CONTEXT_ENTRIES,
) -> list[dict[str, Any]]:
    """Return bounded same-direction GENERAL history and forward-only offline learning."""
    if direction not in {"forward", "reverse"}:
        raise ResearchMemoryError("Direction must be forward or reverse.")
    try:
        current_target = date.fromisoformat(target_date)
    except (TypeError, ValueError):
        raise ResearchMemoryError("Current target date is invalid.") from None
    if current_target.isoformat() != target_date:
        raise ResearchMemoryError("Current target date is not canonical.")
    if type(limit) is not int or not 1 <= limit <= MAX_CONTEXT_ENTRIES:
        raise ResearchMemoryError("Research-memory context limit is invalid.")
    memory = load_memory(memory_path)
    now = datetime.now(timezone.utc)
    context = []

    # The offline symbolic namespace is forward-only. It is deliberately kept
    # out of reverse prompts to preserve directional separation. Only the most
    # recent eligible offline cycle is projected into the forward prompt.
    if direction == "forward":
        for offline_entry in reversed(memory["offline_research"]["entries"]):
            if date.fromisoformat(offline_entry["target_date"]) > current_target:
                continue
            if _validate_timestamp(offline_entry["created_at_utc"]) >= now:
                continue

            # Transfer complete validated candidate records in original generation
            # order. Preserve proposals, full DSL ASTs, hashes, and all test metrics.
            # If the complete payload exceeds the byte budget below, fail closed;
            # never project, rank-select, or silently omit candidates.
            candidate_records = offline_entry["candidates"]
            offline_context = {
                "context_schema_version": 3,
                "prior_target_date": offline_entry["target_date"],
                "prior_recorded_at_utc": offline_entry["created_at_utc"],
                "source": "GENERAL/offline-symbolic-discovery",
                "direction": "forward",
                "status": "EXPLORATORY_ONLY",
                "candidate_selection": "NONE",
                "automatic_candidate_selection": False,
                "candidate_transfer_mode": "FULL_VALIDATED_RECORDS",
                "candidates_in_memory": len(candidate_records),
                "candidates_in_context": len(candidate_records),
                "candidate_context_truncated": False,
                "candidates": candidate_records,
                "development_window": {
                    "draw_count": offline_entry["development_window"]["draw_count"],
                    "window_sha256": offline_entry["development_window"]["window_sha256"],
                    "dates_stored": False,
                    "consumed_for_exploratory_feedback": True,
                },
                "interpretation": (
                    "Every candidates item is one complete validated record in original generation "
                    "order; this order is not a ranking. Each record includes its complete proposal, "
                    "formal definition, full execution_spec AST, specification hash, training signal, "
                    "synthetic-test metadata, forward walk-forward metrics, time-ordered CV metrics, "
                    "and reverse-retrospective metrics. These are exploratory history, not approved "
                    "formulas or independent confirmation. Reuse of the development interval is forbidden."
                ),
                "holdout_reuse": (
                    "FORBIDDEN; this consumed offline development interval is historical feedback only."
                ),
            }
            _reject_number_values(offline_context, "Offline directional context")
            offline_size = len(_canonical([offline_context]))
            if offline_size > MAX_OFFLINE_CONTEXT_BYTES:
                raise ResearchMemoryError(
                    "Complete offline candidate context exceeds its dedicated byte budget; "
                    "all candidates were retained and no prompt context was emitted."
                )
            if offline_size > MAX_CONTEXT_BYTES:
                raise ResearchMemoryError(
                    "Offline development provenance exceeds the bounded forward context."
                )
            context.append(offline_context)
            break

    entries = [
        item for item in memory["entries"]
        if item["direction"] == direction
        and date.fromisoformat(item["target_date"]) <= current_target
        and _validate_timestamp(item["created_at_utc"]) < now
    ]
    for entry in reversed(entries):
        compact = {
            "prior_target_date": entry["target_date"],
            "prior_recorded_at_utc": entry["created_at_utc"],
            "archive_sha256": entry["archive_sha256"],
            "runner_version": entry["runner_version"],
            "research_report_sha256": entry["research_report_sha256"],
            "research_report_content_status": entry["research_report_content_status"],
            "research_report_content": entry["research_report_content"],
            "research_report_content_is_unverified": True,
            "status": entry["status"],
            "proposal_status": entry["proposal_status"],
            "hypotheses": [_compact_proposal(item) for item in entry["proposals"][:2]],
            "proposals_in_memory": len(entry["proposals"]),
            "execution_spec_status": entry["execution_spec_status"],
            "execution_spec": entry["execution_spec"],
            "execution_spec_sha256": entry["execution_spec_sha256"],
            "learning_notes": entry["learning_notes"],
            "evaluation_feedback": entry["evaluation_feedback"],
            "failure": entry["failure"],
            "holdout_reuse": "FORBIDDEN; any reported holdout is consumed development feedback only.",
            "source": SOURCE,
            "direction": direction,
        }
        _reject_number_values(compact, "Directional context")
        candidate_context = context + [compact]
        if len(candidate_context) > limit or len(_canonical(candidate_context)) > MAX_CONTEXT_BYTES:
            continue
        context = candidate_context
    return context


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    record = sub.add_parser("record-cycle", help="Append sanitized directional results from a GENERAL run.")
    record.add_argument("--run-id", required=True)
    record.add_argument("--run-attempt", default="1")
    record.add_argument("--memory", type=Path, default=MEMORY_PATH)
    record.add_argument("--archive", type=Path, default=ARCHIVE_PATH)
    record.add_argument("--ledger", type=Path, default=LEDGER_PATH)
    record.add_argument("--run-status", type=Path, default=RUN_STATUS_PATH)
    record.add_argument("--forward-report", type=Path, default=FORWARD_REPORT_PATH)
    record.add_argument("--reverse-report", type=Path, default=REVERSE_REPORT_PATH)
    record.add_argument("--sealed-plan", type=Path, default=SEALED_PLAN_PATH)
    record.add_argument("--claim", type=Path, default=CLAIM_PATH)
    record.add_argument("--evaluation", type=Path, default=EVALUATION_PATH)
    record.add_argument("--failure-stage", default="")
    record.add_argument("--error-type", default="WorkflowStepFailure")
    args = parser.parse_args(argv)
    try:
        result = record_cycle(
            memory_path=args.memory,
            archive_path=args.archive,
            ledger_path=args.ledger,
            run_status_path=args.run_status,
            forward_report_path=args.forward_report,
            reverse_report_path=args.reverse_report,
            sealed_plan_path=args.sealed_plan,
            claim_path=args.claim,
            evaluation_path=args.evaluation,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            failure_stage_override=args.failure_stage or None,
            error_type_override=args.error_type if args.failure_stage else None,
        )
    except (ResearchMemoryError, OSError, ValueError) as exc:
        print("RESEARCH MEMORY FAILED:", type(exc).__name__, str(exc))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
