"""Read-only, multi-year independent-source audit for the Super Loto archive.

The audit prints no draw-number sets. It compares dates and number sets internally,
but only counts and mismatch dates are included in the report. It never overwrites
the working archive and never passes rows to the prediction engine.
"""
from __future__ import annotations

import json
import re
import sys
import time
from html.parser import HTMLParser
from collections import defaultdict
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from typing import Any

from general_archive_fetch import TableParser, get_turkish_date

BASE = Path(__file__).resolve().parent
ARCHIVE_PATH = BASE / "super_loto_archive.json"
LOTO_GEN_BASE = "https://loto.gen.tr/super-loto/arsiv"
LOTObil_URL = "https://www.lotobil.com/super-loto-butun-sonuc-listesi"
R60_START = "2020-08-02"
YEAR_RANGE = range(2020, 2027)
TIMEOUT_SECONDS = 25
LOTObil_ANOMALY_REGISTRY_VERSION = "1.0.0"

# Explicit date-label errors confirmed against the archive's draw index and
# the independent Loto.gen chronology. Corrections are applied only when all
# three fields (year, draw index, observed date) match exactly.
LOTObil_DATE_CORRECTIONS = {
    (2020, 17, "2020-08-08"): ("2020-09-08", "The row sits between 10/09 and 06/09; draw index 17 confirms 08/09."),
    (2020, 51, "2020-11-16"): ("2020-11-26", "Draw index 51 and a separate result page confirm 26/11, not 16/11."),
    (2021, 13, "2021-01-30"): ("2021-01-31", "Draw index 13 is dated 31/01 by Loto.gen and Aydinlik; Lotobil says 30/01."),
    (2021, 108, "2021-09-10"): ("2021-09-09", "Draw index 108 confirms the Thursday 09/09 draw; Lotobil labels it 10/09."),
    (2021, 120, "2021-09-07"): ("2021-10-07", "Draw index 120 sits between 10/10 and 05/10; the correct date is 07/10."),
    (2023, 62, "2023-05-13"): ("2023-05-23", "Draw index 62 sits between indices 63 (25/05) and 61 (21/05)."),
    (2025, 140, "2025-11-25"): ("2025-11-23", "Draw index 140 is 23/11; index 141 is the separate 25/11 draw."),
    (2025, 146, "2025-12-01"): ("2025-12-07", "Draw index 146 is 07/12 between indices 147 (09/12) and 145 (04/12)."),
    (2026, 27, "2026-01-03"): ("2026-03-03", "Draw index 27 sits between 05/03 and 01/03; the source date has a month typo."),
}

# These source rows are omitted only if the observed date, draw index, and
# exact bad values all match. The values remain internal and are never reported.
LOTObil_EXCLUDED_ROWS = {
    (2021, 21, "2021-02-17"): {
        "raw_values": (5, 17, 36, 39, 49, 53),
        "kind": "EXTRA_DUPLICATE_DRAW_ROW",
        "reason": "Lotobil repeats draw index 21 on a non-draw date; the valid draw-index-21 row is dated 18/02 and agrees with the archive.",
        "verified_by": ["loto.gen.tr", "lotoistatistik.com"],
    },
    (2023, 130, "2023-10-29"): {
        "raw_values": (7, 12, 20, 25, 48, 48),
        "kind": "INVALID_DUPLICATE_BALL_ROW",
        "reason": "Lotobil's row has a repeated ball value; the draw-specific Loto.gen page and the primary archive confirm the valid result.",
        "verified_by": ["loto.gen.tr", "lotoistatistik.com"],
    },
}

LOTObil_WRONG_NUMBER_ROWS = {
    (2023, 5, "2023-01-10"): {
        "raw_values": (15, 24, 29, 30, 44, 49),
        "kind": "CONFIRMED_SOURCE_NUMBER_TYPO",
        "reason": "Lotobil differs from both Loto.gen and lotoistatistik for draw index 5; exclude the Lotobil row from comparison.",
        "verified_by": ["loto.gen.tr", "lotoistatistik.com"],
    },
    (2023, 31, "2023-03-12"): {
        "raw_values": (2, 31, 34, 35, 45, 60),
        "kind": "CONFIRMED_SOURCE_NUMBER_TYPO",
        "reason": "Lotobil differs from both Loto.gen and lotoistatistik for draw index 31; exclude the Lotobil row from comparison.",
        "verified_by": ["loto.gen.tr", "lotoistatistik.com"],
    },
}

# Lotobil omits 2022 draw index 21. Treat this as a coverage exception only
# if indices 20 and 22 are present at the documented dates and the archive has
# the missing draw. Any other internal index gap remains blocking.
LOTObil_MISSING_INDEX_EXCEPTIONS = {
    (2022, 21): {
        "date": "2022-02-17",
        "previous_index": 20,
        "previous_date": "2022-02-15",
        "next_index": 22,
        "next_date": "2022-02-20",
        "reason": "Lotobil jumps from draw index 20 (15/02) to 22 (20/02); the missing 17/02 record is present in both Loto.gen and the archive.",
        "verified_by": ["loto.gen.tr", "lotoistatistik.com"],
    },
}


class AuditSourceError(RuntimeError):
    pass


def fetch_html(url: str, attempts: int = 3) -> str:
    """Fetch independent source pages with bounded retries and safe diagnostics."""
    request = Request(url, headers={"User-Agent": "GENERAL-Archive-Audit/1.1"})
    for attempt in range(attempts):
        try:
            with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                data = response.read().decode("utf-8", "replace")
            if data.strip():
                return data
        except HTTPError as exc:
            # Retry only responses that commonly indicate rate limiting or a
            # temporary server problem. A permanent 4xx response fails closed.
            if exc.code not in (408, 425, 429, 500, 502, 503, 504):
                break
        except (URLError, TimeoutError, OSError):
            # Keep raw host/network details out of public Actions logs.
            pass

        if attempt + 1 < attempts:
            time.sleep(2 ** attempt)

    raise AuditSourceError("Independent source could not be fetched.")

def _raw_number_values(cells: list[dict[str, str]], use_attrs: bool = False) -> list[int]:
    field = "attrs" if use_attrs else "text"
    text = " ".join(cell.get(field, "") for cell in cells)
    return [int(x) for x in re.findall(r"(?<!\d)\d{1,2}(?!\d)", text)]


def _six_numbers(cells: list[dict[str, str]]) -> tuple[int, ...] | None:
    nums = _raw_number_values(cells)
    if len(nums) != 6 or len(set(nums)) != 6 or any(n < 1 or n > 60 for n in nums):
        nums = _raw_number_values(cells, use_attrs=True)
    if len(nums) != 6 or len(set(nums)) != 6 or any(n < 1 or n > 60 for n in nums):
        return None
    return tuple(sorted(nums))


def parse_loto_gen_year_page(html: str, year: int) -> tuple[dict[str, tuple[int, ...]], list[str]]:
    parser = TableParser()
    parser.feed(html)
    parser.close()
    found: dict[str, tuple[int, ...]] = {}
    errors: list[str] = []
    draw_id = re.compile(r"(?<!\d)#?\s*" + str(year) + r"\d{4}(?!\d)")

    for cells in parser.rows:
        all_text = " ".join(cell.get("text", "") for cell in cells)
        all_attrs = " ".join(cell.get("attrs", "") for cell in cells)
        if not draw_id.search(all_text + " " + all_attrs):
            continue

        date_index = None
        draw_date = None
        for index, cell in enumerate(cells):
            candidate = get_turkish_date(cell.get("text", ""))
            if candidate:
                date_index, draw_date = index, candidate
                break
        if not draw_date or not draw_date.startswith(f"{year}-"):
            continue
        if draw_date < R60_START:
            continue

        number_cells = []
        attr_cells = []
        for cell in cells[date_index + 1:]:
            text = cell.get("text", "").strip()
            attrs = cell.get("attrs", "").strip()
            if "₺" in text or re.search(r"\b(?:TL|TRY)\b", text, re.I):
                continue
            if text:
                number_cells.append(cell)
            if attrs:
                attr_cells.append(cell)

        numbers = _six_numbers(number_cells)
        if numbers is None:
            numbers = _six_numbers(attr_cells)
        if numbers is None:
            errors.append(draw_date)
            continue
        if draw_date in found and found[draw_date] != numbers:
            errors.append(draw_date)
            found.pop(draw_date, None)
            continue
        found[draw_date] = numbers
    return found, sorted(set(errors))


def parse_lotobil_archive_detailed(
    html: str,
) -> tuple[
    dict[str, tuple[int, ...]],
    list[str],
    list[dict[str, Any]],
    dict[int, set[int]],
    set[tuple[int, int, str]],
]:
    """Parse Lotobil rows and register only exactly identified source anomalies."""
    parser = TableParser()
    parser.feed(html)
    parser.close()
    found: dict[str, tuple[int, ...]] = {}
    errors: list[str] = []
    anomalies: list[dict[str, Any]] = []
    indices_by_year: dict[int, set[int]] = defaultdict(set)
    index_date_rows: set[tuple[int, int, str]] = set()
    date_pattern = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")

    for cells in parser.rows:
        date_index = None
        observed_date = None
        for index, cell in enumerate(cells):
            match = date_pattern.search(cell.get("text", ""))
            if match:
                day, month, year = map(int, match.groups())
                try:
                    observed_date = date(year, month, day).isoformat()
                except ValueError:
                    observed_date = None
                date_index = index
                break
        if date_index is None or observed_date is None:
            continue
        if observed_date < R60_START:
            continue
        if len(cells) < date_index + 8:
            continue

        draw_index = None
        if date_index + 1 < len(cells):
            index_text = cells[date_index + 1].get("text", "").strip()
            index_match = re.fullmatch(r"(\d{1,3})", index_text)
            if index_match:
                draw_index = int(index_match.group(1))

        number_cells = []
        for cell in cells[date_index + 2:]:
            text = cell.get("text", "").strip()
            if not text or "₺" in text or re.search(r"\b(?:TL|TRY)\b", text, re.I):
                continue
            number_cells.append(cell)

        raw_values = _raw_number_values(number_cells)
        if len(raw_values) != 6 or any(n < 1 or n > 60 for n in raw_values):
            raw_values = _raw_number_values(number_cells, use_attrs=True)
        raw_signature = tuple(sorted(raw_values))
        year = int(observed_date[:4])
        registry_key = (year, draw_index, observed_date) if draw_index is not None else None

        if draw_index is not None and observed_date >= R60_START:
            indices_by_year[year].add(draw_index)
            index_date_rows.add((year, draw_index, observed_date))

        # Recognize a known bad row only when its complete observed signature
        # matches. An updated/corrected source row is parsed normally instead.
        if registry_key in LOTObil_EXCLUDED_ROWS:
            known = LOTObil_EXCLUDED_ROWS[registry_key]
            if raw_signature == known["raw_values"]:
                anomalies.append({
                    "source": "lotobil",
                    "kind": known["kind"],
                    "draw_date": observed_date,
                    "draw_index": draw_index,
                    "reason": known["reason"],
                    "verified_by": known["verified_by"],
                })
                continue

        if registry_key in LOTObil_WRONG_NUMBER_ROWS:
            known = LOTObil_WRONG_NUMBER_ROWS[registry_key]
            if raw_signature == known["raw_values"]:
                anomalies.append({
                    "source": "lotobil",
                    "kind": known["kind"],
                    "draw_date": observed_date,
                    "draw_index": draw_index,
                    "reason": known["reason"],
                    "verified_by": known["verified_by"],
                })
                continue

        draw_date = observed_date
        if registry_key in LOTObil_DATE_CORRECTIONS:
            normalized_date, reason = LOTObil_DATE_CORRECTIONS[registry_key]
            draw_date = normalized_date
            anomalies.append({
                "source": "lotobil",
                "kind": "CONFIRMED_DATE_LABEL_CORRECTION",
                "observed_date": observed_date,
                "draw_date": normalized_date,
                "draw_index": draw_index,
                "reason": reason,
                "verified_by": ["loto.gen.tr", "lotoistatistik.com"],
            })

        numbers = _six_numbers(number_cells)
        if numbers is None:
            errors.append(draw_date)
            continue
        if draw_date in found and found[draw_date] != numbers:
            errors.append(draw_date)
            found.pop(draw_date, None)
            continue
        found[draw_date] = numbers

    return found, sorted(set(errors)), anomalies, dict(indices_by_year), index_date_rows


def parse_lotobil_archive(
    html: str,
) -> tuple[dict[str, tuple[int, ...]], list[str]]:
    """Backward-compatible parser interface used by simple deterministic tests."""
    found, errors, _anomalies, _indices, _index_dates = parse_lotobil_archive_detailed(html)
    return found, errors


class _LotoGenArchiveLinkParser(HTMLParser):
    """Capture text inside result-card links from the paginated root archive."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag == "a" and self.current is None:
            self.current = {"attrs": dict(attrs), "parts": []}

    def handle_data(self, data):
        if self.current is not None and data and data.strip():
            self.current["parts"].append(data.strip())

    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            self.links.append(self.current)
            self.current = None


_ROOT_ARCHIVE_DATE = re.compile(
    r"\b\d{1,2}\s+(?:ocak|şubat|subat|mart|nisan|mayıs|mayis|haziran|"
    r"temmuz|ağustos|agustos|eylül|eylul|ekim|kasım|kasim|aralık|aralik)\s+\d{4}\b",
    re.IGNORECASE,
)
_ROOT_ARCHIVE_DRAW_ID = re.compile(r"(?<!\d)#?\s*(20\d{2})\d{4}(?!\d)")


def parse_loto_gen_root_archive_page(
    html: str,
) -> tuple[dict[tuple[int, str], tuple[int, ...]], list[tuple[int, str]], set[int]]:
    """Parse result-card links on the paginated Loto.gen archive.

    Returns internal rows keyed by (draw year, ISO date), parse-error markers
    keyed by year/date, and every draw year observed on the page. Caller reports
    only aggregate/date metadata; number sets remain internal to comparison.
    """
    parser = _LotoGenArchiveLinkParser()
    parser.feed(html)
    parser.close()

    found: dict[tuple[int, str], tuple[int, ...]] = {}
    errors: list[tuple[int, str]] = []
    years_seen: set[int] = set()

    for link in parser.links:
        text = " ".join(link["parts"])
        attributes = " ".join(
            str(value) for value in link["attrs"].values() if value
        )
        draw_match = _ROOT_ARCHIVE_DRAW_ID.search(text + " " + attributes)
        if not draw_match:
            continue

        draw_year = int(draw_match.group(1))
        years_seen.add(draw_year)
        draw_date = get_turkish_date(text)
        if not draw_date or not draw_date.startswith(f"{draw_year}-"):
            errors.append((draw_year, draw_date or "UNKNOWN_DATE"))
            continue
        if draw_date < R60_START:
            continue

        number_text = _ROOT_ARCHIVE_DRAW_ID.sub(" ", text)
        number_text = _ROOT_ARCHIVE_DATE.sub(" ", number_text, count=1)
        number_tokens = [
            int(value)
            for value in re.findall(r"(?<!\d)\d{1,2}(?!\d)", number_text)
        ]
        if (
            len(number_tokens) != 6
            or len(set(number_tokens)) != 6
            or any(value < 1 or value > 60 for value in number_tokens)
        ):
            errors.append((draw_year, draw_date))
            continue

        key = (draw_year, draw_date)
        numbers = tuple(sorted(number_tokens))
        if key in found and found[key] != numbers:
            errors.append((draw_year, draw_date))
            found.pop(key, None)
            continue
        found[key] = numbers

    return found, sorted(set(errors)), years_seen


def fetch_loto_gen_year_from_root_archive(
    year: int,
    max_pages: int = 12,
) -> tuple[dict[str, tuple[int, ...]], list[str], int, list[str]]:
    """Fetch a full year from root-archive pagination, stopping at older years.

    This is the fallback for current-year pages that are unavailable or expose
    only a selected month. Missing pages or a page with no parseable draw links
    fail closed instead of being treated as an empty valid source.
    """
    combined: dict[str, tuple[int, ...]] = {}
    parse_errors: list[str] = []
    pages_fetched = 0
    failures: list[str] = []

    for page_number in range(1, max_pages + 1):
        url = LOTO_GEN_BASE if page_number == 1 else f"{LOTO_GEN_BASE}?sayfa={page_number}"
        try:
            html = fetch_html(url)
        except AuditSourceError:
            failures.append(f"loto.gen.tr:root-archive:page{page_number}")
            break

        page_rows, page_errors, years_seen = parse_loto_gen_root_archive_page(html)
        pages_fetched += 1

        for error_year, error_date in page_errors:
            if error_year == year:
                parse_errors.append(error_date)

        for (draw_year, draw_date), numbers in page_rows.items():
            if draw_year != year:
                continue
            if draw_date in combined and combined[draw_date] != numbers:
                parse_errors.append(draw_date)
                combined.pop(draw_date, None)
            else:
                combined[draw_date] = numbers

        # Root archive pages are newest-first. Once older-year results appear
        # without any result from the requested year, its current-year range is
        # complete. If the requested year never appears, the source may be lagging.
        if year not in years_seen:
            if not years_seen and not page_rows:
                failures.append(f"loto.gen.tr:root-archive:page{page_number}:no-draw-links")
            break

    else:
        failures.append(f"loto.gen.tr:root-archive:page-limit-{max_pages}")

    return combined, sorted(set(parse_errors)), pages_fetched, failures


def _find_missing_indices(indices_by_year: dict[int, set[int]]) -> dict[int, list[int]]:
    """Return internal gaps in each year's source draw-index sequence."""
    gaps: dict[int, list[int]] = {}
    for year, indices in indices_by_year.items():
        if not indices:
            continue
        missing = sorted(set(range(1, max(indices) + 1)) - indices)
        if missing:
            gaps[year] = missing
    return gaps


def _compare(
    archive_rows: list[dict[str, Any]],
    independent: dict[str, tuple[int, ...]],
    source_parse_errors: list[str] | None = None,
) -> dict[str, Any]:
    archive = {row["date"]: tuple(sorted(row["numbers"])) for row in archive_rows}
    archive_dates, independent_dates = set(archive), set(independent)
    common = archive_dates & independent_dates
    mismatched_numbers = sorted(d for d in common if archive[d] != independent[d])
    archive_only = sorted(archive_dates - independent_dates)
    independent_only = sorted(independent_dates - archive_dates)
    latest_archive = max(archive_dates) if archive_dates else None
    latest_independent = max(independent_dates) if independent_dates else None

    # A source can lag at the current-year tail. Only classify as lag when all
    # archive-only dates are later than the independent source's latest date.
    source_lag = bool(
        archive_only and latest_independent
        and all(d > latest_independent for d in archive_only)
        and not independent_only and not mismatched_numbers
    )
    archive_behind = bool(
        independent_only and latest_archive
        and all(d > latest_archive for d in independent_only)
        and not archive_only and not mismatched_numbers
    )
    if source_parse_errors:
        status = "SOURCE_PARSE_ERRORS"
    elif source_lag:
        status = "SOURCE_LAG_AT_TAIL"
    elif archive_behind:
        status = "ARCHIVE_BEHIND_SOURCE"
    elif not archive_only and not independent_only and not mismatched_numbers:
        status = "MATCH"
    else:
        status = "MISMATCH"

    return {
        "status": status,
        "archive_count": len(archive),
        "independent_count": len(independent),
        "common_date_count": len(common),
        "archive_only_count": len(archive_only),
        "independent_only_count": len(independent_only),
        "number_mismatch_count": len(mismatched_numbers),
        "source_parse_error_count": len(source_parse_errors or []),
        "archive_first_date": min(archive_dates) if archive_dates else None,
        "archive_last_date": latest_archive,
        "independent_first_date": min(independent_dates) if independent_dates else None,
        "independent_last_date": latest_independent,
        "archive_only_dates_sample": archive_only[:20],
        "independent_only_dates_sample": independent_only[:20],
        "number_mismatch_dates_sample": mismatched_numbers[:20],
        "source_parse_error_dates_sample": (source_parse_errors or [])[:20],
        "draw_numbers_in_report": False,
    }


def _within_date_scope(
    draw_date: str,
    start_inclusive: str,
    end_exclusive: str | None = None,
) -> bool:
    """Return whether an ISO date falls within a half-open date interval."""
    return (
        draw_date >= start_inclusive
        and (end_exclusive is None or draw_date < end_exclusive)
    )


def run_audit(archive_path: Path = ARCHIVE_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(archive_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise AuditSourceError("Working archive could not be read.") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("draws"), list):
        raise AuditSourceError("Working archive structure is invalid.")
    archive_rows = payload["draws"]

    # Data Guard is structural only; do not alter or print the archive.
    import general_data_guard as guard
    summary = guard.validate_archive(archive_rows)
    if summary.get("valid") is not True:
        raise AuditSourceError("Data Guard rejected the working archive.")

    all_loto_gen: dict[str, tuple[int, ...]] = {}
    year_status: dict[str, Any] = {}
    fetch_failures: list[str] = []
    current_audit_year = max(YEAR_RANGE)
    for year in YEAR_RANGE:
        parse_errors: list[str] = []
        page_count = 0
        if year == current_audit_year:
            # Year pages can fail or expose only one selected month. For the
            # current year, use the chronological root archive pages instead.
            eligible, parse_errors, page_count, year_failures = (
                fetch_loto_gen_year_from_root_archive(year)
            )
            fetch_failures.extend(year_failures)
        else:
            combined: dict[str, tuple[int, ...]] = {}
            pages = [f"{LOTO_GEN_BASE}/{year}"]
            if year >= 2021:
                pages.append(f"{LOTO_GEN_BASE}/{year}?sayfa=2")
            for page_number, url in enumerate(pages, start=1):
                try:
                    html = fetch_html(url)
                except AuditSourceError:
                    # A missing page is a coverage warning, never an empty page.
                    fetch_failures.append(f"loto.gen.tr:{year}:page{page_number}")
                    continue
                page_rows, errors = parse_loto_gen_year_page(html, year)
                page_count += 1
                parse_errors.extend(errors)
                for draw_date, numbers in page_rows.items():
                    if draw_date in combined and combined[draw_date] != numbers:
                        parse_errors.append(draw_date)
                        combined.pop(draw_date, None)
                    else:
                        combined[draw_date] = numbers

            # Loto.gen's 2020 page is filtered to the active research scope only.
            eligible = {
                draw_date: numbers for draw_date, numbers in combined.items()
                if year != 2020 or draw_date >= R60_START
            }

        all_loto_gen.update(eligible)
        primary_year = [
            row for row in archive_rows
            if row["date"].startswith(str(year))
            and (year != 2020 or row["date"] >= R60_START)
        ]
        year_status[str(year)] = {
            "loto_gen": _compare(primary_year, eligible, parse_errors),
            "pages_fetched": page_count,
            "retrieval_mode": "root_archive_pagination" if year == current_audit_year else "year_pages",
            "page_fetch_failure_count": sum(
                x.startswith(f"loto.gen.tr:{year}:")
                or x.startswith("loto.gen.tr:root-archive:")
                for x in fetch_failures
            ) if year == current_audit_year else sum(
                x.startswith(f"loto.gen.tr:{year}:") for x in fetch_failures
            ),
        }

    # Independently check Lotobil, but distinguish its documented historical
    # data-entry errors from any new discrepancy. Every correction is keyed by
    # observed date and draw index; values are still compared against the
    # archive after normalization, and unregistered errors remain blocking.
    source_anomalies: list[dict[str, Any]] = []
    lotobil_indices_by_year: dict[int, set[int]] = {}
    lotobil_index_date_rows: set[tuple[int, int, str]] = set()
    lotobil_rows: dict[str, tuple[int, ...]] = {}
    lotobil_errors: list[str] = []
    try:
        lotobil_html = fetch_html(LOTObil_URL)
        (
            lotobil_rows,
            lotobil_errors,
            source_anomalies,
            lotobil_indices_by_year,
            lotobil_index_date_rows,
        ) = parse_lotobil_archive_detailed(lotobil_html)
        r60_source = {
            draw_date: nums for draw_date, nums in lotobil_rows.items()
            if draw_date >= R60_START
        }
        r60_archive = [row for row in archive_rows if row["date"] >= R60_START]
        r60_errors = [
            draw_date for draw_date in lotobil_errors
            if _within_date_scope(draw_date, R60_START)
        ]

        # Draw index 21 is missing from the 2022 Lotobil sequence. This is
        # accepted only when neighbouring index/date pairs and the archive
        # agree with the documented source-coverage gap.
        missing_source_dates: set[str] = set()
        expected_missing = LOTObil_MISSING_INDEX_EXCEPTIONS[(2022, 21)]
        has_gap = 21 not in lotobil_indices_by_year.get(2022, set())
        has_neighbours = (
            (2022, 20, expected_missing["previous_date"]) in lotobil_index_date_rows
            and (2022, 22, expected_missing["next_date"]) in lotobil_index_date_rows
        )
        archive_has_gap_date = any(
            row["date"] == expected_missing["date"] for row in r60_archive
        )
        if has_gap and has_neighbours and archive_has_gap_date:
            missing_source_dates.add(expected_missing["date"])
            source_anomalies.append({
                "source": "lotobil",
                "kind": "CONFIRMED_MISSING_SOURCE_ROW",
                "draw_date": expected_missing["date"],
                "draw_index": 21,
                "reason": expected_missing["reason"],
                "verified_by": expected_missing["verified_by"],
            })

        missing_indices = _find_missing_indices(lotobil_indices_by_year)
        unresolved_index_gaps: dict[str, list[int]] = {}
        for gap_year, gaps in missing_indices.items():
            unrecognized = [
                index for index in gaps
                if not (
                    gap_year == 2022
                    and index == 21
                    and expected_missing["date"] in missing_source_dates
                )
            ]
            if unrecognized:
                unresolved_index_gaps[str(gap_year)] = unrecognized

        # Rows whose exact source error is independently verified are excluded
        # from both sides of the Lotobil-only comparison, never from the
        # canonical archive or the year-by-year Loto.gen check.
        excluded_comparison_dates = {
            anomaly.get("draw_date")
            for anomaly in source_anomalies
            if anomaly.get("kind") in {
                "CONFIRMED_SOURCE_NUMBER_TYPO",
                "INVALID_DUPLICATE_BALL_ROW",
            }
        }
        excluded_comparison_dates.update(missing_source_dates)

        comparable_archive = [
            row for row in r60_archive
            if row["date"] not in excluded_comparison_dates
        ]
        comparable_source = {
            draw_date: nums for draw_date, nums in r60_source.items()
            if draw_date not in excluded_comparison_dates
        }
        comparable_errors = [
            draw_date for draw_date in r60_errors
            if draw_date not in excluded_comparison_dates
        ]

        lotobil_r60_report = _compare(
            comparable_archive,
            comparable_source,
            comparable_errors,
        )
        lotobil_r60_report["raw_source_parse_error_count"] = len(r60_errors)
        lotobil_r60_report["documented_source_anomaly_count"] = len(source_anomalies)
        lotobil_r60_report["source_date_correction_count"] = sum(
            1 for item in source_anomalies
            if item.get("kind") == "CONFIRMED_DATE_LABEL_CORRECTION"
        )
        lotobil_r60_report["excluded_confirmed_bad_row_count"] = sum(
            1 for item in source_anomalies
            if item.get("kind") in {
                "EXTRA_DUPLICATE_DRAW_ROW",
                "INVALID_DUPLICATE_BALL_ROW",
                "CONFIRMED_SOURCE_NUMBER_TYPO",
            }
        )
        lotobil_r60_report["excluded_confirmed_missing_row_count"] = len(missing_source_dates)
        lotobil_r60_report["unresolved_index_gap_count"] = sum(
            len(values) for values in unresolved_index_gaps.values()
        )
        lotobil_r60_report["unresolved_index_gaps_by_year"] = unresolved_index_gaps
        lotobil_r60_report["documented_source_anomalies"] = source_anomalies
        lotobil_r60_report["anomaly_registry_version"] = LOTObil_ANOMALY_REGISTRY_VERSION

        # The normalized status is allowed only when all remaining dates and
        # number sets match and no new parse/index gaps remain.
        if (
            lotobil_r60_report["status"] == "MATCH"
            and not unresolved_index_gaps
        ):
            if source_anomalies:
                lotobil_r60_report["status"] = "MATCH_AFTER_DOCUMENTED_SOURCE_NORMALIZATION"
            lotobil_r60_report["normalized_match"] = True
        else:
            lotobil_r60_report["normalized_match"] = False
    except AuditSourceError:
        lotobil_r60_report = {"status": "FETCH_FAILED", "draw_numbers_in_report": False}



    return {
        "audit_version": "1.2.0",
        "archive_guard_version": summary["guard_version"],
        "archive_records": summary["record_count"],
        "archive_first_date": summary["first_date"],
        "archive_last_date": summary["last_date"],
        "active_target_numbers_exposed": False,
        "archive_modified": False,
        "draw_numbers_in_report": False,
        "primary_source_cross_check_scope_in_existing_fetcher": "2025 only",
        "research_scope_start_date": R60_START,
        "loto_gen_by_year": year_status,
        "lotobil_R60_comparison": lotobil_r60_report,
        "fetch_failures": fetch_failures,
        "lotobil_anomaly_registry_version": LOTObil_ANOMALY_REGISTRY_VERSION,
        "interpretation": [
            "MATCH means dates and number sets agree within the compared scope.",
            "MATCH_AFTER_DOCUMENTED_SOURCE_NORMALIZATION means the compared rows match after only exact, independently verified Lotobil date-label corrections and explicitly registered bad/missing source rows are excluded.",
            "Unregistered parse errors, date gaps, and number-set mismatches remain blocking.",
            "The Lotobil-only exclusions never alter the canonical archive or relax the per-year Loto.gen comparison.",
            "This audit does not modify the archive or run statistical discovery.",
            "No draw-number sets are included in this report."
        ]
    }


def main() -> None:
    report = run_audit()
    output = BASE / "archive_cross_source_audit_report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("ARCHIVE CROSS-SOURCE AUDIT COMPLETED")
    print("Archive records:", report["archive_records"])
    print("Archive modified: NO")
    print("Target numbers exposed: NO")
    for year, details in report["loto_gen_by_year"].items():
        r = details["loto_gen"]
        print(f"LOTO_GEN {year}: {r['status']} archive={r['archive_count']} source={r['independent_count']} date_mismatch={r['archive_only_count'] + r['independent_only_count']} number_mismatch={r['number_mismatch_count']} parse_errors={r['source_parse_error_count']}")
    print(f"LOTObil R60: {report['lotobil_R60_comparison']['status']} archive={report['lotobil_R60_comparison'].get('archive_count', '?')} source={report['lotobil_R60_comparison'].get('independent_count', '?')}")
    print("Detailed report path: archive_cross_source_audit_report.json")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Do not echo source rows, cell text, or draw-number sets on failure.
        print("ARCHIVE CROSS-SOURCE AUDIT FAILED:", type(exc).__name__)
        sys.exit(1)
