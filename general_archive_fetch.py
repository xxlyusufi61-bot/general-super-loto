import json
import re
import sys
import os
import time
from pathlib import Path
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# GENERAL Super Loto archive fetcher 3.4
BASE_URL = "https://lotoistatistik.com/super-loto/"
CROSS_URLS = (
    "https://loto.gen.tr/super-loto/arsiv/2025",
    "https://loto.gen.tr/super-loto/arsiv/2025?sayfa=2",
)
OUT = Path(__file__).resolve().parent / "super_loto_archive.json"
# Active-scope primary-site counts after dates before 2020-08-02 are discarded.
EXPECTED = {2020: 66, 2021: 154, 2022: 155, 2023: 150, 2024: 157, 2025: 156}
CROSS_EXPECTED = 156
VERIFIED_ARCHIVE_EXPECTED = {
    2020: 66,
    2021: 156,
    2022: 156,
    2023: 157,
    2024: 157,
    2025: 156,
}
LOTO_GEN_BASE = "https://loto.gen.tr/super-loto/arsiv"
R60_START = "2020-08-02"
MONTHS = {
    "ocak": 1, "şubat": 2, "subat": 2, "mart": 3, "nisan": 4,
    "mayıs": 5, "mayis": 5, "haziran": 6, "temmuz": 7,
    "ağustos": 8, "agustos": 8, "eylül": 9, "eylul": 9,
    "ekim": 10, "kasım": 11, "kasim": 11, "aralık": 12, "aralik": 12,
}
DATE_TR = re.compile(r"\b(\d{1,2})\s+([A-Za-zÇĞİÖŞÜçğıöşü]+)\s+(\d{4})\b")
DATE_PRIMARY = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")
ATTR_KEYS = {"alt", "title", "aria-label", "data-number", "data-value", "value"}

KNOWN_MISDATED = (
    ("2025-07-19", "2025-06-19", (9, 25, 29, 31, 35, 46)),
    ("2025-07-17", "2025-06-17", (11, 12, 21, 52, 53, 54)),
    ("2025-07-15", "2025-06-15", (7, 22, 31, 39, 48, 56)),
    ("2025-07-12", "2025-06-12", (16, 19, 25, 37, 43, 57)),
)

class TableParser(HTMLParser):
    """Collect row/cell text plus useful accessible/data attributes."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = None
        self.cell = None

    def _finish_cell(self):
        if self.row is not None and self.cell is not None:
            text = " ".join("".join(self.cell["text"]).split())
            attrs = " ".join(" ".join(self.cell["attrs"]).split())
            self.row.append({"text": text, "attrs": attrs})
        self.cell = None

    def _finish_row(self):
        if self.row is not None:
            self._finish_cell()
            if self.row:
                self.rows.append(self.row)
        self.row = None

    def handle_starttag(self, tag, attrs):
        attr_map = dict(attrs)
        if tag == "tr":
            if self.row is not None:
                self._finish_row()
            self.row = []
            self.cell = None
            return
        if tag in ("td", "th") and self.row is not None:
            self._finish_cell()
            self.cell = {"text": [], "attrs": []}
            # IMPORTANT: capture attributes on the cell itself too.
            # Some result sites render winning balls in td data-number
            # or aria-label attributes instead of visible text.
            for key, value in attrs:
                if key.lower() in ATTR_KEYS and value:
                    self.cell["attrs"].append(str(value))
            return
        if self.cell is not None:
            for key, value in attrs:
                if key.lower() in ATTR_KEYS and value:
                    self.cell["attrs"].append(str(value))

    def handle_data(self, data):
        if self.cell is not None:
            # Preserve boundaries between adjacent nested elements (e.g. six balls in spans).
            self.cell["text"].append(" " + data + " ")

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self._finish_cell()
        elif tag == "tr":
            self._finish_row()

    def close(self):
        super().close()
        if self.row is not None:
            self._finish_row()


def fetch_html(url, label, attempts=4):
    """Fetch a source page with bounded retries for transient network failures."""
    req = Request(url, headers={"User-Agent": "GENERAL-Archive-Research/3.5"})
    last_error = None
    for attempt in range(attempts):
        try:
            with urlopen(req, timeout=40) as response:
                page_text = response.read().decode("utf-8", "replace")
            if page_text.strip():
                return page_text
            last_error = RuntimeError("empty source page")
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
            retryable = not isinstance(exc, HTTPError) or exc.code in (408, 425, 429, 500, 502, 503, 504)
            if not retryable:
                break
        if attempt + 1 < attempts:
            time.sleep(2 ** attempt)
    raise RuntimeError(
        f"{label} download failed after {attempt + 1} attempt(s); archive not saved"
    ) from last_error


def safe_failure_category(exc):
    """Return a number-free diagnostic category suitable for public CI logs."""
    message = str(exc).lower()
    if "download failed after" in message:
        if "primary source" in message:
            prefix = "PRIMARY_SOURCE"
        elif "independent 2025 source" in message:
            prefix = "INDEPENDENT_SOURCE"
        else:
            prefix = "SOURCE"

        cause = exc.__cause__
        if isinstance(cause, HTTPError):
            retryable_codes = (408, 425, 429, 500, 502, 503, 504)
            suffix = (
                "HTTP_RETRY_EXHAUSTED"
                if cause.code in retryable_codes
                else "HTTP_REJECTED"
            )
        elif isinstance(cause, (URLError, TimeoutError, OSError)):
            suffix = "NETWORK_RETRY_EXHAUSTED"
        elif isinstance(cause, RuntimeError) and "empty source page" in str(cause).lower():
            suffix = "EMPTY_RESPONSE"
        else:
            suffix = "FETCH_FAILED"
        return f"{prefix}_{suffix}"

    if "returned an empty page" in message:
        return "SOURCE_EMPTY_RESPONSE"
    if "current-year independent-source coverage failed" in message:
        return "CURRENT_YEAR_COVERAGE_FAILED"
    if "independent archive contains" in message or "unparsed row" in message:
        return "SOURCE_PARSE_FAILED"
    if "independent-source verification failed" in message or "number-set mismatch" in message:
        return "CROSS_SOURCE_MISMATCH"
    if "verified archive count is not" in message or "unexpected number of unique primary-source dates" in message:
        return "ARCHIVE_COUNT_MISMATCH"
    if "did not yield six distinct numbers" in message or "no primary results found" in message:
        return "PRIMARY_SOURCE_PARSE_FAILED"
    return "ARCHIVE_VALIDATION_FAILED"

def parse_primary_year(year, html=None):
    if html is None:
        url = BASE_URL + f"super-loto-cekilis-arsivi-{year}/"
        html = fetch_html(url, f"Primary source {year}")
    parser = TableParser()
    parser.feed(html)
    parser.close()
    results = []
    for cells in parser.rows:
        if len(cells) < 2:
            continue
        first = cells[0]["text"]
        m = DATE_PRIMARY.search(first)
        if not m:
            continue
        day, month, row_year = map(int, m.groups())
        if row_year != year:
            continue
        try:
            draw_date = date(row_year, month, day).isoformat()
        except ValueError as exc:
            raise RuntimeError(f"Invalid primary date: {first}") from exc
        if draw_date < R60_START:
            continue
        text_parts = [cell["text"] for cell in cells[1:]]
        nums = [int(x) for x in re.findall(r"(?<!\d)\d{1,2}(?!\d)", " ".join(text_parts))]
        if len(nums) != 6 or len(set(nums)) != 6 or any(n < 1 or n > 60 for n in nums):
            # Attribute fallback for sites that render numbers as accessible labels.
            attrs = [cell["attrs"] for cell in cells[1:]]
            nums = [int(x) for x in re.findall(r"(?<!\d)\d{1,2}(?!\d)", " ".join(attrs))]
        if len(nums) != 6 or len(set(nums)) != 6 or any(n < 1 or n > 60 for n in nums):
            raise RuntimeError(f"{draw_date}: primary row did not yield six distinct numbers; archive not saved")
        results.append({"date": draw_date, "numbers": sorted(nums)})
    if not results:
        raise RuntimeError(f"No primary results found for {year}; archive not saved")
    return results


def apply_known_corrections(year, rows):
    if year != 2025:
        return rows
    remaining = list(rows)
    for wrong_date, actual_date, expected_nums in KNOWN_MISDATED:
        bad = [r for r in remaining if r["date"] == wrong_date and r["numbers"] == list(expected_nums)]
        counterpart = [r for r in remaining if r["date"] == actual_date and r["numbers"] == list(expected_nums)]
        if len(bad) != 1 or len(counterpart) != 1:
            raise RuntimeError(f"Known-source anomaly validation failed for {wrong_date}/{actual_date}; archive not saved")
        remaining.remove(bad[0])
        print(f"EXCLUDED VERIFIED MISDATED DUPLICATE: {wrong_date} -> {actual_date}")
    return remaining


def get_turkish_date(text):
    m = DATE_TR.search(text)
    if not m:
        return None
    day, month_name, year = int(m.group(1)), m.group(2).lower(), int(m.group(3))
    month = MONTHS.get(month_name)
    if month is None:
        raise RuntimeError(f"Unrecognized month '{month_name}' in cross-check; archive not saved")
    try:
        return date(year, month, day).isoformat()
    except ValueError as exc:
        raise RuntimeError(f"Invalid cross-check date: {m.group(0)}") from exc


def _valid_six(nums):
    return len(nums) == 6 and len(set(nums)) == 6 and all(1 <= n <= 60 for n in nums)


def parse_cross_rows(html):
    parser = TableParser()
    parser.feed(html)
    parser.close()
    found = {}
    parsed_rows = 0
    for cells in parser.rows:
        all_text = " ".join(cell["text"] for cell in cells)
        all_attrs = " ".join(cell["attrs"] for cell in cells)
        if not re.search(r"(?<!\d)#?\s*2025\d{4}(?!\d)", all_text + " " + all_attrs):
            continue
        date_index = None
        draw_date = None
        for i, cell in enumerate(cells):
            d = get_turkish_date(cell["text"])
            if d is not None:
                date_index, draw_date = i, d
                break
        if draw_date is None or not draw_date.startswith("2025-"):
            continue
        # Use cell text after the date, ignoring prize/money cells.
        number_cells = []
        attr_cells = []
        for cell in cells[date_index + 1:]:
            t = cell["text"].strip()
            a = cell["attrs"].strip()

            # Read attributes even when the cell has no visible text.
            # Skip prize cells before collecting text or attributes.
            if "₺" in t or re.search(r"\b(?:TL|TRY)\b", t, re.I):
                continue

            if t:
                number_cells.append(t)
            if a:
                attr_cells.append(a)
        nums = [int(x) for x in re.findall(r"(?<!\d)\d{1,2}(?!\d)", " ".join(number_cells))]
        if not _valid_six(nums):
            # If the source represents balls with attributes rather than text,
            # try those attributes; do not combine both paths and double-count.
            nums = [int(x) for x in re.findall(r"(?<!\d)\d{1,2}(?!\d)", " ".join(attr_cells))]
        if not _valid_six(nums):
            raise RuntimeError(f"Cross-check row {draw_date}: expected six distinct numbers but parsed {nums}; cells={json.dumps(cells, ensure_ascii=False)[:2000]}; archive not saved")
        values = tuple(sorted(nums))
        if draw_date in found and found[draw_date] != values:
            raise RuntimeError(f"Independent source has conflicting records for {draw_date}; archive not saved")
        found[draw_date] = values
        parsed_rows += 1
    if not found:
        raise RuntimeError("Independent source yielded no recognizable 2025 draws; archive not saved")
    return found, parsed_rows


def fetch_independent_2025(html_pages=None):
    independent = {}
    for i, url in enumerate(CROSS_URLS):
        html = html_pages[i] if html_pages is not None else fetch_html(url, "Independent 2025 source")
        page_rows, parsed = parse_cross_rows(html)
        print(f"Cross-check page {i + 1}: {len(page_rows)} unique dates; {parsed} rows")
        for d, nums in page_rows.items():
            if d in independent and independent[d] != nums:
                raise RuntimeError(f"Independent source pages disagree for {d}; archive not saved")
            independent[d] = nums
    if len(independent) != CROSS_EXPECTED:
        raise RuntimeError(f"Independent source expected {CROSS_EXPECTED} dates, got {len(independent)}; archive not saved")
    return independent


def compare_2025(primary_rows, independent):
    primary = {r["date"]: tuple(r["numbers"]) for r in primary_rows}
    pd, idates = set(primary), set(independent)
    missing_other = sorted(pd - idates)
    missing_primary = sorted(idates - pd)
    mismatches = sorted(d for d in pd & idates if primary[d] != independent[d])
    if missing_other or missing_primary or mismatches:
        bits = []
        if missing_other:
            bits.append("missing from independent source: " + ", ".join(missing_other[:8]))
        if missing_primary:
            bits.append("missing from primary source: " + ", ".join(missing_primary[:8]))
        if mismatches:
            bits.append("number mismatches: " + ", ".join(mismatches[:8]))
        raise RuntimeError("2025 CROSS-SOURCE CHECK FAILED: " + "; ".join(bits) + "; archive not saved")
    if len(primary) != CROSS_EXPECTED:
        raise RuntimeError(f"Primary 2025 count expected {CROSS_EXPECTED}, got {len(primary)}; archive not saved")
    print("CROSS-SOURCE CHECK: PASS (156 dates and number sets)")
    return {"year": 2025, "primary_record_count": len(primary), "independent_record_count": len(independent), "matching_dates_and_number_sets": len(primary), "result": "PASS", "checked_at": datetime.now(timezone.utc).isoformat(), "pages_checked": list(CROSS_URLS)}


def reconcile_year_with_loto_gen(year, primary_rows, independent_rows, source_parse_errors=None):
    """Merge only independently verified missing rows; reject conflicts."""
    source_parse_errors = list(source_parse_errors or [])
    if year == 2020:
        source_parse_errors = [
            day for day in source_parse_errors if day >= R60_START
        ]
        primary_scope = [
            row for row in primary_rows if row["date"] >= R60_START
        ]
        independent_scope = {
            day: values for day, values in independent_rows.items()
            if day >= R60_START
        }
    else:
        primary_scope = list(primary_rows)
        independent_scope = dict(independent_rows)

    if source_parse_errors:
        raise RuntimeError(
            f"{year}: independent archive contains "
            f"{len(set(source_parse_errors))} unparsed row(s); archive not saved"
        )

    primary_map = {}
    for row in primary_scope:
        if row["date"] in primary_map:
            raise RuntimeError(
                f"{year}: primary archive has duplicate dates; archive not saved"
            )
        primary_map[row["date"]] = tuple(sorted(row["numbers"]))

    shared = set(primary_map) & set(independent_scope)
    number_mismatches = sorted(
        day for day in shared
        if primary_map[day] != tuple(sorted(independent_scope[day]))
    )
    primary_only = sorted(set(primary_map) - set(independent_scope))
    if number_mismatches or primary_only:
        raise RuntimeError(
            f"{year}: independent-source verification failed "
            f"({len(primary_only)} archive-only date(s), "
            f"{len(number_mismatches)} number-set mismatch date(s)); "
            "archive not saved"
        )

    independent_only = sorted(set(independent_scope) - set(primary_map))
    additions = [
        {"date": day, "numbers": list(sorted(independent_scope[day]))}
        for day in independent_only
    ]
    merged = list(primary_scope) + additions
    final_dates = {
        row["date"]: tuple(sorted(row["numbers"]))
        for row in merged
    }
    if (
        set(final_dates) != set(independent_scope)
        or any(final_dates[day] != tuple(sorted(independent_scope[day]))
               for day in independent_scope)
    ):
        raise RuntimeError(
            f"{year}: post-merge verification failed; archive not saved"
        )

    return merged, {
        "result": "PASS",
        "archive_compared_count_before_merge": len(primary_map),
        "independent_source_count": len(independent_scope),
        "records_added_from_independent_source": len(additions),
        "matching_dates_and_number_sets_after_merge": len(independent_scope),
        "draw_numbers_in_report": False,
    }


def reconcile_archive_with_loto_gen(
    rows,
    html_pages_by_year=None,
    start_year=2020,
    through_year=None,
):
    """Verify every year against Loto.gen before an archive can be saved."""
    from general_archive_cross_source_audit import (
        fetch_html as fetch_audit_html,
        fetch_loto_gen_year_from_root_archive,
        parse_loto_gen_year_page,
    )

    through_year = through_year or datetime.now(timezone.utc).year
    html_pages_by_year = html_pages_by_year or {}
    reconciled = [row for row in rows if row.get("date", "") >= R60_START]
    reports = {}

    for year in range(start_year, through_year + 1):
        supplied_pages = html_pages_by_year.get(year)
        independent = {}
        parse_errors = []
        urls = []
        if year == through_year and supplied_pages is None:
            # Reuse the current-year root-pagination fallback used by the audit.
            independent, parse_errors, root_pages_fetched, coverage_failures = (
                fetch_loto_gen_year_from_root_archive(year)
            )
            urls = [
                f"{LOTO_GEN_BASE} (root archive page {page_number})"
                for page_number in range(1, root_pages_fetched + 1)
            ]
            if coverage_failures:
                raise RuntimeError(
                    f"{year}: current-year independent-source coverage failed; "
                    "archive not saved"
                )
        else:
            urls = [f"{LOTO_GEN_BASE}/{year}"]
            if year >= 2021:
                urls.append(f"{LOTO_GEN_BASE}/{year}?sayfa=2")
            if supplied_pages is not None and len(supplied_pages) != len(urls):
                raise RuntimeError(
                    f"{year}: wrong number of independent-source fixture pages; "
                    "archive not saved"
                )
            for index, url in enumerate(urls):
                html = (
                    supplied_pages[index]
                    if supplied_pages is not None
                    else fetch_audit_html(url)
                )
                page_rows, page_errors = parse_loto_gen_year_page(html, year)
                parse_errors.extend(page_errors)
                for day, numbers in page_rows.items():
                    if day in independent and independent[day] != numbers:
                        parse_errors.append(day)
                        independent.pop(day, None)
                    elif day not in independent:
                        independent[day] = numbers

        verified_rows = [
            row for row in reconciled
            if row["date"].startswith(str(year))
        ]
        merged_year, verification = reconcile_year_with_loto_gen(
            year,
            verified_rows,
            independent,
            parse_errors,
        )
        other_years = [
            row for row in reconciled
            if not row["date"].startswith(str(year))
        ]
        reconciled = other_years + merged_year
        verification["source"] = "loto.gen.tr"
        verification["pages_checked"] = urls
        verification["checked_at"] = datetime.now(timezone.utc).isoformat()
        reports[str(year)] = verification

    reconciled.sort(key=lambda row: row["date"])
    final_counts = {}
    for row in reconciled:
        year = int(row["date"][:4])
        final_counts[year] = final_counts.get(year, 0) + 1
    for year, expected in VERIFIED_ARCHIVE_EXPECTED.items():
        if start_year <= year <= through_year and final_counts.get(year, 0) != expected:
            raise RuntimeError(
                f"{year}: verified archive count is not {expected}; "
                "archive not saved"
            )
    return reconciled, reports


def build_archive(
    year_rows,
    independent_html_pages=None,
    independent_year_html_pages=None,
    through_year=None,
):
    all_rows = []
    check = None
    for year in sorted(year_rows):
        rows = year_rows[year]
        if year == 2025:
            rows = apply_known_corrections(year, rows)
            if len({r["date"] for r in rows}) != len(rows):
                raise RuntimeError("Duplicate dates remain in primary 2025 archive; archive not saved")
            independent = fetch_independent_2025(independent_html_pages)
            check = compare_2025(rows, independent)
        if year in EXPECTED and len({r["date"] for r in rows}) != EXPECTED[year]:
            raise RuntimeError(f"{year}: unexpected number of unique primary-source dates; archive not saved")
        all_rows.extend(
            row for row in rows if row["date"] >= R60_START
        )

    if check is None:
        raise RuntimeError("Required independent 2025 check did not run; archive not saved")

    all_rows, multi_year_check = reconcile_archive_with_loto_gen(
        all_rows,
        html_pages_by_year=independent_year_html_pages,
        through_year=through_year,
    )
    dates = [r["date"] for r in all_rows]
    if any(day < R60_START for day in dates):
        raise RuntimeError("Archive contains a record before the active research scope; archive not saved")
    if not all_rows or len(dates) != len(set(dates)):
        raise RuntimeError("Archive empty or contains duplicate dates; archive not saved")

    payload = {
        "source": BASE_URL,
        "cross_check_source": CROSS_URLS[0],
        "cross_check_validation": check,
        "multi_year_cross_source_validation": multi_year_check,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "corrections_applied": [
            {"removed_source_date": wrong, "verified_actual_date": actual, "reason": "Misdated duplicate; verified against matching counterpart and independent source"}
            for wrong, actual, _ in KNOWN_MISDATED
        ],
        "draws": all_rows,
    }
    return payload


def main():
    now_year = datetime.now(timezone.utc).year
    year_rows = {}
    for year in range(2020, now_year + 1):
        rows = parse_primary_year(year)
        print(f"{year}: {len(rows)} raw rows read")
        year_rows[year] = rows
    payload = build_archive(year_rows)
    tmp = OUT.with_suffix(".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, OUT)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    print("Archive saved:", OUT.name)
    print("Total verified rows:", len(payload["draws"]))
    print("First date:", payload["draws"][0]["date"])
    print("Last date:", payload["draws"][-1]["date"])
    print("Archive integrity checks: PASS")


def self_test():
    # Representative fixture matching the visible second-source table:
    # draw ID, Turkish date, six numbers, prize.
    html = '''<table><tr><th>#</th><th>Tarih</th><th>Numaralar</th><th>İkramiye</th></tr>
    <tr><td><a href="/x">#20250156</a></td><td>30 Aralık 2025</td><td><span>6</span><span>34</span><span>35</span><span>36</span><span>56</span><span>57</span></td><td>₺732.389.501</td></tr></table>'''
    rows, n = parse_cross_rows(html)
    assert n == 1
    assert rows["2025-12-30"] == (6, 34, 35, 36, 56, 57)

    # Numbers split over separate cells must also work.
    html2 = '''<table><tr><td>#20250155</td><td>28 Aralık 2025</td><td>6</td><td>31</td><td>35</td><td>41</td><td>44</td><td>46</td><td>₺732.389.501</td></tr></table>'''
    rows2, n2 = parse_cross_rows(html2)
    assert n2 == 1
    assert rows2["2025-12-28"] == (6, 31, 35, 41, 44, 46)

    # Some sites expose each number on a cell attribute instead of text.
    html3 = '''<table><tr><td>#20250156</td><td>30 Aralık 2025</td>
    <td data-number="6"></td><td data-number="34"></td>
    <td data-number="35"></td><td data-number="36"></td>
    <td data-number="56"></td><td data-number="57"></td>
    <td>₺732.389.501</td></tr></table>'''
    rows3, n3 = parse_cross_rows(html3)
    assert n3 == 1
    assert rows3["2025-12-30"] == (6, 34, 35, 36, 56, 57)

    bad_html = '''<table><tr><td>#20250154</td><td>25 Aralık 2025</td><td>2 7 19 21 23</td><td>₺732.389.501</td></tr></table>'''
    try:
        parse_cross_rows(bad_html)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Parser accepted a row with only five numbers")

    primary_html = '''<table><tr><td>30.12.2025</td><td>6 34 35 36 56 57</td></tr></table>'''
    primary = parse_primary_year(2025, html=primary_html)
    assert primary == [{"date": "2025-12-30", "numbers": [6, 34, 35, 36, 56, 57]}]

    # Misdated rows are removable only when the matching counterpart exists.
    sample = [
        {"date": "2025-06-19", "numbers": [9, 25, 29, 31, 35, 46]},
        {"date": "2025-07-19", "numbers": [9, 25, 29, 31, 35, 46]},
    ]
    corrected = apply_known_corrections(2025, sample + [
        {"date": "2025-06-17", "numbers": [11, 12, 21, 52, 53, 54]},
        {"date": "2025-07-17", "numbers": [11, 12, 21, 52, 53, 54]},
        {"date": "2025-06-15", "numbers": [7, 22, 31, 39, 48, 56]},
        {"date": "2025-07-15", "numbers": [7, 22, 31, 39, 48, 56]},
        {"date": "2025-06-12", "numbers": [16, 19, 25, 37, 43, 57]},
        {"date": "2025-07-12", "numbers": [16, 19, 25, 37, 43, 57]},
    ])
    assert len(corrected) == 4
    print("SELF-TEST PASS: syntax, primary parser, cross-source parser, and correction checks")

if __name__ == "__main__":
    try:
        if "--self-test" in sys.argv:
            self_test()
        else:
            main()
    except Exception as exc:
        print(f"ARCHIVE FETCH FAILED [{safe_failure_category(exc)}]; details suppressed.")
        sys.exit(1)
