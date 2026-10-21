import json
import os
import re
import sys
from pathlib import Path
from datetime import date, datetime, timezone
from urllib.request import Request, urlopen
from html.parser import HTMLParser

BASE_URL = "https://lotoistatistik.com/super-loto/"
OUTPUT = Path(__file__).resolve().parent / "super_loto_archive.json"

# Historical counts established for this archive. 2025 is 156, not 160:
# the source page contains four extra/misdated June records under July dates.
EXPECTED = {
    2020: 97,
    2021: 154,
    2022: 155,
    2023: 150,
    2024: 157,
    2025: 156,
}

# Exact source defects independently cross-checked against another year archive.
# We only exclude a row if BOTH its wrong-date signature and its correct-date
# counterpart are present exactly once. Any deviation stops the download safely.
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
            # Do not silently overwrite a half-read row if HTML changes.
            if self.row is not None:
                raise RuntimeError(
                    "Unexpected nested table row; source HTML has changed."
                )
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            if self.cell is not None:
                raise RuntimeError(
                    "Unexpected nested table cell; source HTML has changed."
                )
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.cell is not None:
                self.row.append(" ".join("".join(self.cell).split()))
            self.rows.append(self.row)
            self.row = None
            self.cell = None


def parse_source_year(year):
    url = BASE_URL + f"super-loto-cekilis-arsivi-{year}/"
    request = Request(
        url,
        headers={"User-Agent": "GENERAL-Archive-Research/2.0"},
    )
    with urlopen(request, timeout=40) as response:
        html = response.read().decode("utf-8", "replace")

    parser = TableParser()
    parser.feed(html)
    results = []

    for cells in parser.rows:
        if len(cells) < 2:
            continue

        match = re.search(r"\b(\d{2})\.(\d{2})\.(\d{4})\b", cells[0])
        if not match:
            continue

        day, month, row_year = map(int, match.groups())
        if row_year != year:
            continue

        try:
            draw_date = date(row_year, month, day).isoformat()
        except ValueError as exc:
            raise RuntimeError(f"Invalid source date: {cells[0]}") from exc

        numbers = [int(x) for x in re.findall(r"\d+", " ".join(cells[1:]))]
        if len(numbers) != 6:
            raise RuntimeError(
                f"{draw_date}: expected exactly 6 numbers; found {numbers}."
            )
        if len(set(numbers)) != 6 or any(n < 1 or n > 60 for n in numbers):
            raise RuntimeError(f"{draw_date}: invalid numbers {numbers}.")

        results.append({"date": draw_date, "numbers": sorted(numbers)})

    if not results:
        raise RuntimeError(
            f"No results found for {year}; source or page format may have changed."
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
            if row["date"] == wrong_date and row["numbers"] == expected_numbers
        ]
        counterpart_rows = [
            row for row in remaining
            if row["date"] == actual_date and row["numbers"] == expected_numbers
        ]

        if len(bad_rows) != 1 or len(counterpart_rows) != 1:
            raise RuntimeError(
                "SOURCE ANOMALY CHECK FAILED: expected exactly one confirmed "
                f"misdated record {wrong_date} {expected_numbers} and exactly one "
                f"counterpart on {actual_date}; found {len(bad_rows)} and "
                f"{len(counterpart_rows)}. Archive was not saved."
            )

        # Remove only the known wrong-date copy. Keep the correctly dated row.
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
    rows = apply_known_source_corrections(year, rows)

    by_date = {}
    for row in rows:
        draw_date = row["date"]
        if draw_date in by_date:
            previous = by_date[draw_date]
            raise RuntimeError(
                f"CONFLICTING DUPLICATE AFTER CORRECTIONS: {draw_date}; "
                f"first={previous['numbers']}, second={row['numbers']}. "
                "Archive was not saved."
            )
        by_date[draw_date] = row

    results = sorted(by_date.values(), key=lambda row: row["date"])

    if year in EXPECTED and len(results) != EXPECTED[year]:
        raise RuntimeError(
            f"{year}: expected {EXPECTED[year]} verified unique dates, "
            f"received {len(results)}. Archive was not saved."
        )

    print(f"{year}: {len(results)} unique dates checked")
    return results


def main():
    current_year = datetime.now(timezone.utc).year
    all_rows = []

    for year in range(2020, current_year + 1):
        all_rows.extend(download_year(year))

    all_rows.sort(key=lambda row: row["date"])
    dates = [row["date"] for row in all_rows]

    if len(dates) != len(set(dates)):
        raise RuntimeError("Duplicate dates remain after all source corrections.")
    if not all_rows:
        raise RuntimeError("Archive is empty.")

    payload = {
        "source": BASE_URL,
        "cross_check_source": "https://loto.gen.tr/super-loto/arsiv/2025",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "corrections_applied": [
            {
                "removed_source_date": item["wrong_date"],
                "verified_actual_date": item["actual_date"],
                "reason": "Known misdated duplicate in source archive",
            }
            for item in KNOWN_MISDATED_ROWS
        ],
        "draws": all_rows,
    }

    temp = OUTPUT.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")
    os.replace(temp, OUTPUT)

    print("Archive saved:", OUTPUT.name)
    print("Total verified rows:", len(all_rows))
    print("First date:", all_rows[0]["date"])
    print("Last date:", all_rows[-1]["date"])
    print("Archive integrity checks: PASS")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("ARCHIVE FETCH FAILED:", error)
        sys.exit(1)
        
