import json
import os
import re
import sys
from pathlib import Path
from datetime import date, datetime, timezone
from urllib.request import Request, urlopen
from html.parser import HTMLParser


# ==========================================
# GENERAL ARCHIVE FETCHER
# Version: 3.1
# ==========================================

BASE_URL = "https://lotoistatistik.com/super-loto/"
CROSS_CHECK_BASE_URL = "https://loto.gen.tr/super-loto/arsiv/2025"

CROSS_CHECK_PAGE_URLS = (
    CROSS_CHECK_BASE_URL,
    CROSS_CHECK_BASE_URL + "?sayfa=2",
)

CROSS_CHECK_EXPECTED_2025 = 156
OUTPUT = Path(__file__).resolve().parent / "super_loto_archive.json"

EXPECTED = {
    2020: 97,
    2021: 154,
    2022: 155,
    2023: 150,
    2024: 157,
    2025: 156,
}

TURKISH_MONTHS = {
    "ocak": 1,
    "şubat": 2,
    "subat": 2,
    "mart": 3,
    "nisan": 4,
    "mayıs": 5,
    "mayis": 5,
    "haziran": 6,
    "temmuz": 7,
    "ağustos": 8,
    "agustos": 8,
    "eylül": 9,
    "eylul": 9,
    "ekim": 10,
    "kasım": 11,
    "kasim": 11,
    "aralık": 12,
    "aralik": 12,
}

KNOWN_MISDATED_ROWS = (
    {
        "wrong_date": "2025-07-19",
        "actual_date": "2025-06-19",
        "numbers": (9, 25, 29, 31, 35, 46),
    },
    {
        "wrong_date": "2025-07-17",
        "actual_date": "2025-06-17",
        "numbers": (11, 12, 21, 52, 53, 54),
    },
    {
        "wrong_date": "2025-07-15",
        "actual_date": "2025-06-15",
        "numbers": (7, 22, 31, 39, 48, 56),
    },
    {
        "wrong_date": "2025-07-12",
        "actual_date": "2025-06-12",
        "numbers": (16, 19, 25, 37, 43, 57),
    },
)


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            if self.row is not None:
                # Recover from malformed nested rows safely.
                self.row = None
                self.cell = None

            self.row = []

        elif tag in ("td", "th") and self.row is not None:
            if self.cell is not None:
                # A new cell starts: finalize the previous cell.
                self.row.append(
                    " ".join("".join(self.cell).split())
                )

            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            if self.row is not None:
                self.row.append(
                    " ".join("".join(self.cell).split())
                )

            self.cell = None

        elif tag == "tr" and self.row is not None:
            if self.cell is not None:
                self.row.append(
                    " ".join("".join(self.cell).split())
                )

            self.rows.append(self.row)
            self.row = None
            self.cell = None


def fetch_html(url, source_name):
    request = Request(
        url,
        headers={
            "User-Agent": "GENERAL-Archive-Research/3.1"
        },
    )

    try:
        with urlopen(request, timeout=40) as response:
            html = response.read().decode(
                "utf-8",
                "replace",
            )
    except Exception as exc:
        raise RuntimeError(
            f"{source_name} download failed; "
            f"archive was not saved: {exc}"
        ) from exc

    if not html.strip():
        raise RuntimeError(
            f"{source_name} returned an empty page; "
            "archive was not saved."
        )

    return html


def parse_source_year(year):
    url = (
        BASE_URL
        + f"super-loto-cekilis-arsivi-{year}/"
    )

    html = fetch_html(
        url,
        f"Primary source {year}",
    )

    parser = TableParser()
    parser.feed(html)

    results = []

    for cells in parser.rows:
        if len(cells) < 2:
            continue

        match = re.search(
            r"\b(\d{2})\.(\d{2})\.(\d{4})\b",
            cells[0],
        )

        if not match:
            continue

        day, month, row_year = map(
            int,
            match.groups(),
        )

        if row_year != year:
            continue

        try:
            draw_date = date(
                row_year,
                month,
                day,
            ).isoformat()
        except ValueError as exc:
            raise RuntimeError(
                f"Invalid source date: {cells[0]}"
            ) from exc

        numbers = [
            int(value)
            for value in re.findall(
                r"\d+",
                " ".join(cells[1:]),
            )
        ]

        if len(numbers) != 6:
            raise RuntimeError(
                f"{draw_date}: expected exactly 6 numbers; "
                f"found {numbers}."
            )

        if (
            len(set(numbers)) != 6
            or any(
                number < 1 or number > 60
                for number in numbers
            )
        ):
            raise RuntimeError(
                f"{draw_date}: invalid numbers {numbers}."
            )

        results.append({
            "date": draw_date,
            "numbers": sorted(numbers),
        })

    if not results:
        raise RuntimeError(
            f"No results found for {year}; "
            "source or page format may have changed."
        )

    return results


def apply_known_source_corrections(year, rows):
    if year != 2025:
        return rows

    remaining = list(rows)

    for item in KNOWN_MISDATED_ROWS:
        wrong_date = item["wrong_date"]
        actual_date = item["actual_date"]
        expected_numbers = list(item["numbers"])

        bad_rows = [
            row for row in remaining
            if (
                row["date"] == wrong_date
                and row["numbers"] == expected_numbers
            )
        ]

        counterpart_rows = [
            row for row in remaining
            if (
                row["date"] == actual_date
                and row["numbers"] == expected_numbers
            )
        ]

        if (
            len(bad_rows) != 1
            or len(counterpart_rows) != 1
        ):
            raise RuntimeError(
                "SOURCE ANOMALY CHECK FAILED: expected exactly "
                f"one misdated record {wrong_date} and one "
                f"counterpart on {actual_date}; found "
                f"{len(bad_rows)} and "
                f"{len(counterpart_rows)}. "
                "Archive was not saved."
            )

        remaining.remove(bad_rows[0])

        print(
            "EXCLUDED KNOWN SOURCE DUPLICATE:",
            wrong_date,
            "matches the verified record dated",
            actual_date,
        )

    return remaining


def download_year(year):
    rows = parse_source_year(year)
    rows = apply_known_source_corrections(
        year,
        rows,
    )

    by_date = {}

    for row in rows:
        draw_date = row["date"]

        if draw_date in by_date:
            previous = by_date[draw_date]
            conflict = (
                previous["numbers"] != row["numbers"]
            )

            raise RuntimeError(
                "CONFLICTING DUPLICATE AFTER CORRECTIONS: "
                f"{draw_date}; numbers conflict={conflict}. "
                "Archive was not saved."
            )

        by_date[draw_date] = row

    results = sorted(
        by_date.values(),
        key=lambda row: row["date"],
    )

    if (
        year in EXPECTED
        and len(results) != EXPECTED[year]
    ):
        raise RuntimeError(
            f"{year}: expected {EXPECTED[year]} unique dates, "
            f"received {len(results)}. "
            "Archive was not saved."
        )

    print(
        f"{year}: {len(results)} unique dates checked"
    )

    return results


def parse_turkish_date(text):
    match = re.search(
        r"\b(\d{1,2})\s+"
        r"([A-Za-zÇĞİÖŞÜçğıöşü]+)\s+"
        r"(\d{4})\b",
        text,
    )

    if not match:
        return None

    day = int(match.group(1))
    month_name = match.group(2).lower()
    year = int(match.group(3))

    month = TURKISH_MONTHS.get(month_name)

    if month is None:
        raise RuntimeError(
            f"Unrecognized Turkish month '{month_name}'; "
            "source format may have changed."
        )

    try:
        return date(
            year,
            month,
            day,
        ).isoformat()
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid cross-check date: {text}"
        ) from exc


def parse_cross_check_page(url):
    html = fetch_html(
        url,
        "Independent 2025 cross-check source",
    )

    parser = TableParser()
    parser.feed(html)

    results = {}
    parsed_rows = 0

    for cells in parser.rows:
        if len(cells) < 3:
            continue

        # Find the draw identifier wherever it appears.
        draw_id_index = next(
            (
                index
                for index, cell in enumerate(cells)
                if re.search(
                    r"\b#?2025\d{4}\b",
                    cell,
                )
            ),
            None,
        )

        if draw_id_index is None:
            continue

        # Find the date cell independently of column position.
        date_index = None
        draw_date = None

        for index, cell in enumerate(cells):
            parsed_date = parse_turkish_date(cell)

            if parsed_date is not None:
                date_index = index
                draw_date = parsed_date
                break

        if date_index is None or draw_date is None:
            continue

        if not draw_date.startswith("2025-"):
            continue

        if draw_id_index >= date_index:
            raise RuntimeError(
                "Independent source row has an unexpected "
                f"column order for {draw_date}; "
                "archive was not saved."
            )

        # IMPORTANT:
        # The six numbers may occupy one cell OR six cells.
        # Collect numeric values from every cell after the date.
        # Exclude prize/money cells so prize amounts cannot be
        # mistaken for lottery numbers.
        numbers = []

        for cell in cells[date_index + 1:]:
            normalized_cell = cell.strip()

            if not normalized_cell:
                continue

            # Ignore prize cells and monetary values.
            if (
                "₺" in normalized_cell
                or re.search(
                    r"\b(?:TL|TRY)\b",
                    normalized_cell,
                    flags=re.IGNORECASE,
                )
            ):
                continue

            # Some HTML tables contain linked text or icons.
            # Extract number tokens from the remaining cells.
            values = re.findall(
                r"(?<!\d)\d{1,2}(?!\d)",
                normalized_cell,
            )

            numbers.extend(
                int(value) for value in values
            )

        if len(numbers) != 6:
            raise RuntimeError(
                "Independent source row did not contain "
                "exactly six numbers for "
                f"{draw_date}; found {numbers}. "
                "Source format may have changed; "
                "archive was not saved."
            )

        if (
            len(set(numbers)) != 6
            or any(
                number < 1 or number > 60
                for number in numbers
            )
        ):
            raise RuntimeError(
                "Independent source contains invalid numbers "
                f"for {draw_date}: {numbers}. "
                "Archive was not saved."
            )

        normalized = tuple(sorted(numbers))

        if (
            draw_date in results
            and results[draw_date] != normalized
        ):
            raise RuntimeError(
                "Independent source has conflicting records "
                f"for {draw_date}; archive was not saved."
            )

        results[draw_date] = normalized
        parsed_rows += 1

    if not results:
        raise RuntimeError(
            "Independent source page yielded no recognizable "
            "draw rows; archive was not saved."
        )

    page_label = (
        url.split("?")[-1]
        if "?" in url
        else "page 1"
    )

    print(
        "Independent source page checked:",
        page_label,
        "unique dates:",
        len(results),
        "parsed rows:",
        parsed_rows,
    )

    return results


def fetch_independent_2025_archive():
    independent = {}

    for url in CROSS_CHECK_PAGE_URLS:
        page_rows = parse_cross_check_page(url)

        for draw_date, numbers in page_rows.items():
            if (
                draw_date in independent
                and independent[draw_date] != numbers
            ):
                raise RuntimeError(
                    "Independent source pages disagree for "
                    f"{draw_date}; archive was not saved."
                )

            independent[draw_date] = numbers

    if len(independent) != CROSS_CHECK_EXPECTED_2025:
        raise RuntimeError(
            "Independent source completeness check failed: "
            f"expected {CROSS_CHECK_EXPECTED_2025} unique "
            f"2025 dates, received {len(independent)}. "
            "Archive was not saved."
        )

    if any(
        not draw_date.startswith("2025-")
        for draw_date in independent
    ):
        raise RuntimeError(
            "Independent source returned a date outside 2025; "
            "archive was not saved."
        )

    return independent


def compare_2025_sources(
    primary_rows,
    independent_rows,
):
    primary = {
        row["date"]: tuple(row["numbers"])
        for row in primary_rows
    }

    primary_dates = set(primary)
    independent_dates = set(independent_rows)

    missing_from_independent = sorted(
        primary_dates - independent_dates
    )

    missing_from_primary = sorted(
        independent_dates - primary_dates
    )

    mismatched_numbers = sorted(
        draw_date
        for draw_date in primary_dates & independent_dates
        if primary[draw_date] != independent_rows[draw_date]
    )

    if (
        missing_from_independent
        or missing_from_primary
        or mismatched_numbers
    ):
        details = []

        if missing_from_independent:
            details.append(
                "dates absent from independent source: "
                + ", ".join(
                    missing_from_independent[:10]
                )
            )

        if missing_from_primary:
            details.append(
                "dates absent from primary source: "
                + ", ".join(
                    missing_from_primary[:10]
                )
            )

        if mismatched_numbers:
            details.append(
                "number mismatches on dates: "
                + ", ".join(mismatched_numbers[:10])
            )

        raise RuntimeError(
            "CROSS-SOURCE CHECK FAILED for 2025: "
            + "; ".join(details)
            + ". Archive was not saved."
        )

    if len(primary) != CROSS_CHECK_EXPECTED_2025:
        raise RuntimeError(
            "Primary 2025 archive count does not match "
            "the independent check; archive was not saved."
        )

    print("CROSS-SOURCE CHECK: PASS")
    print("Year compared: 2025")
    print(
        "Matching dates and number sets:",
        len(primary),
    )
    print("Draw numbers exposed in summary: NO")

    return {
        "year": 2025,
        "primary_record_count": len(primary),
        "independent_record_count": len(
            independent_rows
        ),
        "matching_dates_and_number_sets": len(primary),
        "result": "PASS",
        "checked_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "pages_checked": list(
            CROSS_CHECK_PAGE_URLS
        ),
    }


def main():
    current_year = datetime.now(
        timezone.utc
    ).year

    all_rows = []
    cross_check_validation = None

    for year in range(2020, current_year + 1):
        year_rows = download_year(year)

        if year == 2025:
            independent_2025 = (
                fetch_independent_2025_archive()
            )

            cross_check_validation = (
                compare_2025_sources(
                    year_rows,
                    independent_2025,
                )
            )

        all_rows.extend(year_rows)

    all_rows.sort(
        key=lambda row: row["date"]
    )

    dates = [
        row["date"]
        for row in all_rows
    ]

    if len(dates) != len(set(dates)):
        raise RuntimeError(
            "Duplicate dates remain after all source corrections."
        )

    if not all_rows:
        raise RuntimeError("Archive is empty.")

    if cross_check_validation is None:
        raise RuntimeError(
            "Required 2025 independent cross-check did not run; "
            "archive was not saved."
        )

    payload = {
        "source": BASE_URL,
        "cross_check_source": CROSS_CHECK_BASE_URL,
        "cross_check_validation": cross_check_validation,
        "fetched_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "corrections_applied": [
            {
                "removed_source_date": item["wrong_date"],
                "verified_actual_date": item["actual_date"],
                "reason": (
                    "Known misdated duplicate in source archive"
                ),
            }
            for item in KNOWN_MISDATED_ROWS
        ],
        "draws": all_rows,
    }

    # Save only after all checks pass.
    # The existing archive remains untouched on validation failure.
    temp = OUTPUT.with_suffix(".tmp")

    try:
        with temp.open(
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                payload,
                file,
                ensure_ascii=False,
                indent=2,
            )
            file.write("\n")

        os.replace(temp, OUTPUT)

    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass

    print("Archive saved:", OUTPUT.name)
    print("Total verified rows:", len(all_rows))
    print("First date:", all_rows[0]["date"])
    print("Last date:", all_rows[-1]["date"])
    print("Archive integrity checks: PASS")
    print("Independent 2025 cross-check: PASS")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("ARCHIVE FETCH FAILED:", error)
        sys.exit(1)
