import json
import re
import sys
import os
from pathlib import Path
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from urllib.request import Request, urlopen

# GENERAL Super Loto archive fetcher 3.4
BASE_URL = "https://lotoistatistik.com/super-loto/"
CROSS_URLS = (
    "https://loto.gen.tr/super-loto/arsiv/2025",
    "https://loto.gen.tr/super-loto/arsiv/2025?sayfa=2",
)
OUT = Path(__file__).resolve().parent / "super_loto_archive.json"
EXPECTED = {2020: 97, 2021: 156, 2022: 156, 2023: 157, 2024: 157, 2025: 156}
CROSS_EXPECTED = 156
MAX_CURRENT_YEAR_PRIMARY_ONLY_TAIL = 6
INDEPENDENT_BASE = "https://loto.gen.tr/super-loto/arsiv/"
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


def fetch_html(url, label):
    req = Request(url, headers={"User-Agent": "GENERAL-Archive-Research/3.4"})
    try:
        with urlopen(req, timeout=40) as response:
            text = response.read().decode("utf-8", "replace")
    except Exception as exc:
        raise RuntimeError(f"{label} download failed: {exc}; archive not saved") from exc
    if not text.strip():
        raise RuntimeError(f"{label} returned an empty page; archive not saved")
    return text


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


def parse_cross_rows(html, year=2025):
    parser = TableParser()
    parser.feed(html)
    parser.close()
    found = {}
    parsed_rows = 0
    for cells in parser.rows:
        all_text = " ".join(cell["text"] for cell in cells)
        all_attrs = " ".join(cell["attrs"] for cell in cells)
        if not re.search(rf"(?<!\d)#?\s*{year}\d{{4}}(?!\d)", all_text + " " + all_attrs):
            continue
        date_index = None
        draw_date = None
        for i, cell in enumerate(cells):
            d = get_turkish_date(cell["text"])
            if d is not None:
                date_index, draw_date = i, d
                break
        if draw_date is None or not draw_date.startswith(f"{year}-"):
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
        raise RuntimeError(f"Independent source yielded no recognizable {year} draws; archive not saved")
    return found, parsed_rows


def independent_urls_for_year(year):
    urls = [f"{INDEPENDENT_BASE}{year}"]
    if year >= 2021:
        urls.append(f"{INDEPENDENT_BASE}{year}?sayfa=2")
    return urls


def fetch_independent_year(year, html_pages=None):
    urls = independent_urls_for_year(year)
    if html_pages is not None and len(html_pages) != len(urls):
        raise RuntimeError(f"Independent {year} source fixture has the wrong page count")
    independent = {}
    for i, url in enumerate(urls):
        html = html_pages[i] if html_pages is not None else fetch_html(
            url, f"Independent {year} source"
        )
        page_rows, parsed = parse_cross_rows(html, year=year)
        print(f"Independent {year} page {i + 1}: {len(page_rows)} unique dates; {parsed} rows")
        for draw_date, numbers in page_rows.items():
            if draw_date in independent and independent[draw_date] != numbers:
                raise RuntimeError(
                    f"Independent source pages disagree for {draw_date}; archive not saved"
                )
            independent[draw_date] = numbers

    # loto.gen.tr covers the 6/60 regime for 2020; keep the 6/54 era from
    # the primary source, and compare the independent source only from R60_START.
    if year == 2020:
        independent = {
            draw_date: numbers
            for draw_date, numbers in independent.items()
            if draw_date >= R60_START
        }
        if len(independent) != 66:
            raise RuntimeError(
                f"Independent 2020 R60 range expected 66 dates, got {len(independent)}; archive not saved"
            )
    elif year in EXPECTED and len(independent) != EXPECTED[year]:
        raise RuntimeError(
            f"Independent {year} source expected {EXPECTED[year]} dates, got {len(independent)}; archive not saved"
        )

    if not independent:
        raise RuntimeError(f"Independent {year} source yielded no draws; archive not saved")
    return independent, urls


def fetch_independent_2025(html_pages=None):
    independent, _ = fetch_independent_year(2025, html_pages)
    if len(independent) != CROSS_EXPECTED:
        raise RuntimeError(
            f"Independent 2025 source expected {CROSS_EXPECTED} dates, got {len(independent)}; archive not saved"
        )
    return independent


def compare_2025(primary_rows, independent):
    primary = {r["date"]: tuple(sorted(r["numbers"])) for r in primary_rows}
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


def reconcile_year(primary_rows, independent, year, pages_checked):
    primary = {}
    for row in primary_rows:
        draw_date = row["date"]
        numbers = tuple(sorted(row["numbers"]))
        if draw_date in primary:
            raise RuntimeError(
                f"Primary source has duplicate date {draw_date}; archive not saved"
            )
        primary[draw_date] = numbers

    compare_primary = {
        draw_date: numbers
        for draw_date, numbers in primary.items()
        if year != 2020 or draw_date >= R60_START
    }
    common_dates = set(compare_primary) & set(independent)
    mismatches = sorted(
        draw_date
        for draw_date in common_dates
        if compare_primary[draw_date] != independent[draw_date]
    )
    if mismatches:
        raise RuntimeError(
            f"{year} CROSS-SOURCE CHECK FAILED: number sets disagree for "
            + ", ".join(mismatches[:8])
            + "; archive not saved"
        )

    primary_only = sorted(set(compare_primary) - set(independent))
    latest_independent = max(independent)
    primary_only_tail = [
        draw_date for draw_date in primary_only if draw_date > latest_independent
    ]
    if (
        year == datetime.now(timezone.utc).year
        and len(primary_only_tail) > MAX_CURRENT_YEAR_PRIMARY_ONLY_TAIL
    ):
        raise RuntimeError(
            f"{year} CROSS-SOURCE CHECK FAILED: {len(primary_only_tail)} primary-only "
            f"tail draws exceed the maximum allowed {MAX_CURRENT_YEAR_PRIMARY_ONLY_TAIL}; "
            "archive not saved"
        )

    unexplained_primary_only = [
        draw_date for draw_date in primary_only if draw_date <= latest_independent
    ]
    if unexplained_primary_only:
        raise RuntimeError(
            f"{year} CROSS-SOURCE CHECK FAILED: primary dates missing from the independent source "
            + ", ".join(unexplained_primary_only[:8])
            + "; archive not saved"
        )

    independent_only = sorted(set(independent) - set(compare_primary))
    merged = dict(primary)
    for draw_date, numbers in independent.items():
        if draw_date not in merged:
            merged[draw_date] = numbers

    merged_rows = [
        {"date": draw_date, "numbers": list(merged[draw_date])}
        for draw_date in sorted(merged)
    ]
    expected_count = EXPECTED.get(year)
    if expected_count is not None and len(merged_rows) != expected_count:
        raise RuntimeError(
            f"{year}: reconciled archive expected {expected_count} dates, "
            f"got {len(merged_rows)}; archive not saved"
        )

    validation = {
        "year": year,
        "primary_record_count": len(primary_rows),
        "independent_record_count": len(independent),
        "matching_dates_and_number_sets": len(common_dates),
        "supplemented_from_independent_count": len(independent_only),
        "supplemented_dates": independent_only,
        "primary_only_tail_count": len(primary_only_tail),
        "primary_only_tail_dates": primary_only_tail,
        "comparison_scope": "R60-only" if year == 2020 else "full-year",
        "result": "PASS",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "pages_checked": list(pages_checked),
    }
    return merged_rows, validation


def build_archive(year_rows, independent_html_pages=None):
    all_rows = []
    validations = []
    check_2025 = None
    for year in sorted(year_rows):
        rows = year_rows[year]
        if year == 2025:
            rows = apply_known_corrections(year, rows)
        if len({row["date"] for row in rows}) != len(rows):
            raise RuntimeError(
                f"{year}: duplicate primary dates remain after known corrections; archive not saved"
            )

        fixture_pages = independent_html_pages if year == 2025 else None
        independent, pages_checked = fetch_independent_year(year, fixture_pages)
        merged_rows, validation = reconcile_year(
            rows, independent, year, pages_checked
        )
        if year == 2025:
            check_2025 = compare_2025(rows, independent)
        validations.append(validation)
        all_rows.extend(merged_rows)

    all_rows.sort(key=lambda row: row["date"])
    dates = [row["date"] for row in all_rows]
    if not all_rows or len(dates) != len(set(dates)):
        raise RuntimeError("Archive empty or contains duplicate dates; archive not saved")
    if check_2025 is None:
        raise RuntimeError("Required independent 2025 check did not run; archive not saved")

    payload = {
        "source": BASE_URL,
        "cross_check_source": CROSS_URLS[0],
        "cross_check_validation": check_2025,
        "cross_source_validation_by_year": validations,
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

    # Reconciliation supplements missing historical dates but rejects a
    # conflicting number set for any shared date.
    primary_fixture = [
        {"date": "2026-01-01", "numbers": [1, 2, 3, 4, 5, 6]},
        {"date": "2026-01-03", "numbers": [2, 3, 4, 5, 6, 7]},
    ]
    independent_fixture = {
        "2026-01-01": (1, 2, 3, 4, 5, 6),
        "2026-01-02": (10, 11, 12, 13, 14, 15),
    }
    merged_fixture, audit_fixture = reconcile_year(
        primary_fixture, independent_fixture, 2026, ["synthetic-source-fixture"]
    )
    assert [row["date"] for row in merged_fixture] == [
        "2026-01-01", "2026-01-02", "2026-01-03"
    ]
    assert audit_fixture["supplemented_dates"] == ["2026-01-02"]

    # A current-year independent source that is stale by too many draws must
    # fail closed instead of treating a long missing tail as acceptable.
    current_year = datetime.now(timezone.utc).year
    from datetime import timedelta
    first_day = date(current_year, 1, 1)
    current_primary = [
        {"date": first_day.isoformat(), "numbers": [1, 2, 3, 4, 5, 6]}
    ]
    current_primary.extend(
        {
            "date": (first_day + timedelta(days=offset)).isoformat(),
            "numbers": [2, 3, 4, 5, 6, 7],
        }
        for offset in range(1, MAX_CURRENT_YEAR_PRIMARY_ONLY_TAIL + 2)
    )
    current_independent = {
        first_day.isoformat(): (1, 2, 3, 4, 5, 6)
    }
    try:
        reconcile_year(
            current_primary,
            current_independent,
            current_year,
            ["stale-current-year-source-fixture"],
        )
    except RuntimeError as exc:
        assert "primary-only tail" in str(exc)
    else:
        raise AssertionError("Reconciliation accepted an overly stale current-year source")

    try:
        reconcile_year(
            [{"date": "2026-01-01", "numbers": [1, 2, 3, 4, 5, 6]}],
            {"2026-01-01": (7, 8, 9, 10, 11, 12)},
            2026,
            ["conflicting-source-fixture"],
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("Reconciliation accepted conflicting number sets")

    # The general parser also accepts older year IDs.
    html_2023 = '''<table><tr><td>#20230099</td><td>17 Ağustos 2023</td><td>5 10 19 25 33 35</td><td>₺1</td></tr></table>'''
    rows_2023, n_2023 = parse_cross_rows(html_2023, year=2023)
    assert n_2023 == 1
    assert rows_2023["2023-08-17"] == (5, 10, 19, 25, 33, 35)
    print("SELF-TEST PASS: generic parser, reconciliation, source conflict guard, and known corrections")

if __name__ == "__main__":
    try:
        if "--self-test" in sys.argv:
            self_test()
        else:
            main()
    except Exception as exc:
        print("ARCHIVE FETCH FAILED:", exc)
        sys.exit(1)
