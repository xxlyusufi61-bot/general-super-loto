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
EXPECTED = {2020: 97, 2021: 154, 2022: 155, 2023: 150, 2024: 157, 2025: 156}
CROSS_EXPECTED = 156
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
            self.cell["text"].append(data)

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


def build_archive(year_rows, independent_html_pages=None):
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
            raise RuntimeError(f"{year}: unexpected number of unique dates; archive not saved")
        all_rows.extend(rows)
    all_rows.sort(key=lambda r: r["date"])
    dates = [r["date"] for r in all_rows]
    if not all_rows or len(dates) != len(set(dates)):
        raise RuntimeError("Archive empty or contains duplicate dates; archive not saved")
    if check is None:
        raise RuntimeError("Required independent 2025 check did not run; archive not saved")
    payload = {
        "source": BASE_URL,
        "cross_check_source": CROSS_URLS[0],
        "cross_check_validation": check,
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
    <tr><td><a href="/x">#20250156</a></td><td>30 Aralık 2025</td><td><span>6</span> <span>34</span> <span>35</span> <span>36</span> <span>56</span> <span>57</span></td><td>₺732.389.501</td></tr></table>'''
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
        print("ARCHIVE FETCH FAILED:", exc)
        sys.exit(1)
