"""Read-only, multi-year independent-source audit for the Super Loto archive.

The audit prints no draw-number sets. It compares dates and number sets internally,
but only counts and mismatch dates are included in the report. It never overwrites
the working archive and never passes rows to the prediction engine.
"""
from __future__ import annotations

import json
import re
import sys
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


class AuditSourceError(RuntimeError):
    pass


def fetch_html(url: str) -> str:
    request = Request(url, headers={"User-Agent": "GENERAL-Archive-Audit/1.0"})
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            data = response.read().decode("utf-8", "replace")
    except (HTTPError, URLError, TimeoutError, OSError):
        raise AuditSourceError("Independent source could not be fetched.") from None
    if not data.strip():
        raise AuditSourceError("Independent source returned an empty page.")
    return data


def _six_numbers(cells: list[dict[str, str]]) -> tuple[int, ...] | None:
    text = " ".join(cell.get("text", "") for cell in cells)
    nums = [int(x) for x in re.findall(r"(?<!\d)\d{1,2}(?!\d)", text)]
    if len(nums) != 6 or len(set(nums)) != 6 or any(n < 1 or n > 60 for n in nums):
        attrs = " ".join(cell.get("attrs", "") for cell in cells)
        nums = [int(x) for x in re.findall(r"(?<!\d)\d{1,2}(?!\d)", attrs)]
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


def parse_lotobil_archive(html: str) -> tuple[dict[str, tuple[int, ...]], list[str]]:
    """Parse date-first Lotobil rows; skip the draw-index column after date."""
    parser = TableParser()
    parser.feed(html)
    parser.close()
    found: dict[str, tuple[int, ...]] = {}
    errors: list[str] = []
    date_pattern = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")

    for cells in parser.rows:
        date_index = None
        draw_date = None
        for index, cell in enumerate(cells):
            match = date_pattern.search(cell.get("text", ""))
            if match:
                day, month, year = map(int, match.groups())
                try:
                    draw_date = date(year, month, day).isoformat()
                except ValueError:
                    draw_date = None
                date_index = index
                break
        if date_index is None or draw_date is None or len(cells) < date_index + 8:
            continue

        number_cells = []
        for cell in cells[date_index + 2:]:
            text = cell.get("text", "").strip()
            if not text or "₺" in text or re.search(r"\b(?:TL|TRY)\b", text, re.I):
                continue
            number_cells.append(cell)
        numbers = _six_numbers(number_cells)
        if numbers is None:
            errors.append(draw_date)
            continue
        if draw_date in found and found[draw_date] != numbers:
            errors.append(draw_date)
            found.pop(draw_date, None)
            continue
        found[draw_date] = numbers
    return found, sorted(set(errors))


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
    for year in YEAR_RANGE:
        combined: dict[str, tuple[int, ...]] = {}
        parse_errors: list[str] = []
        pages = [f"{LOTO_GEN_BASE}/{year}"]
        if year >= 2021:
            pages.append(f"{LOTO_GEN_BASE}/{year}?sayfa=2")
        page_count = 0
        for page_number, url in enumerate(pages, start=1):
            try:
                html = fetch_html(url)
            except AuditSourceError:
                # A missing/failed second page is a coverage warning, not
                # silently interpreted as an empty archive page.
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

        # loto.gen.tr switched to 6/60 in August 2020; it only archives
        # that regime for 2020. Exclude old 6/54 records from this comparison.
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
            "page_fetch_failure_count": sum(x.startswith(f"loto.gen.tr:{year}:") for x in fetch_failures),
        }

    # Independently check the R54-era archive with a source that retains the
    # pre-August-2020 draw list as well as the newer game.
    try:
        lotobil_html = fetch_html(LOTObil_URL)
        lotobil_rows, lotobil_errors = parse_lotobil_archive(lotobil_html)
        r54_archive = [row for row in archive_rows if row["date"] < R60_START]
        r54_source = {
            draw_date: nums for draw_date, nums in lotobil_rows.items()
            if draw_date < R60_START
        }
        r54_report = _compare(r54_archive, r54_source, lotobil_errors)
        r54_fetch_status = "FETCHED"
    except AuditSourceError:
        r54_report = {"status": "FETCH_FAILED", "draw_numbers_in_report": False}
        r54_fetch_status = "FETCH_FAILED"

    # Full-period Lotobil comparison is reported separately; it is not used
    # to overwrite or repair the archive.
    if r54_fetch_status == "FETCHED":
        lotobil_all = {
            draw_date: nums for draw_date, nums in lotobil_rows.items()
            if draw_date >= R60_START
        }
        primary_r60 = [row for row in archive_rows if row["date"] >= R60_START]
        lotobil_r60_report = _compare(primary_r60, lotobil_all, lotobil_errors)
    else:
        lotobil_r60_report = {"status": "FETCH_FAILED", "draw_numbers_in_report": False}

    return {
        "audit_version": "1.0.0",
        "archive_guard_version": summary["guard_version"],
        "archive_records": summary["record_count"],
        "archive_first_date": summary["first_date"],
        "archive_last_date": summary["last_date"],
        "active_target_numbers_exposed": False,
        "archive_modified": False,
        "draw_numbers_in_report": False,
        "primary_source_cross_check_scope_in_existing_fetcher": "2025 only",
        "regime_rule": {"R54_end_date": "2020-07-30", "R60_start_date": R60_START},
        "loto_gen_by_year": year_status,
        "lotobil_R54_comparison": r54_report,
        "lotobil_R60_comparison": lotobil_r60_report,
        "fetch_failures": fetch_failures,
        "interpretation": [
            "MATCH means dates and number sets agree within the compared scope.",
            "SOURCE_LAG_AT_TAIL means archive-only dates are later than that source's latest date and do not by themselves prove an archive error.",
            "ARCHIVE_BEHIND_SOURCE means the independent source contains later dates not present in the working archive.",
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
    print(f"LOTObil R54: {report['lotobil_R54_comparison']['status']} archive={report['lotobil_R54_comparison'].get('archive_count', '?')} source={report['lotobil_R54_comparison'].get('independent_count', '?')}")
    print(f"LOTObil R60: {report['lotobil_R60_comparison']['status']} archive={report['lotobil_R60_comparison'].get('archive_count', '?')} source={report['lotobil_R60_comparison'].get('independent_count', '?')}")
    print("Detailed report path: archive_cross_source_audit_report.json")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Do not echo source rows, cell text, or draw-number sets on failure.
        print("ARCHIVE CROSS-SOURCE AUDIT FAILED:", type(exc).__name__)
        sys.exit(1)
