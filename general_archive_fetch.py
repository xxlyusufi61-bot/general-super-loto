import json
import re
import os
import sys
from pathlib import Path
from datetime import date, datetime, timezone
from urllib.request import Request, urlopen
from html.parser import HTMLParser

BASE_URL = "https://lotoistatistik.com/super-loto/"
OUTPUT = Path(__file__).resolve().parent / "super_loto_archive.json"

EXPECTED = {
    2020: 97,
    2021: 154,
    2022: 155,
    2023: 150,
    2024: 157,
    2025: 160,
}


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
            self.cell = None


def download_year(year):
    url = BASE_URL + f"super-loto-cekilis-arsivi-{year}/"
    request = Request(
        url,
        headers={"User-Agent": "GENERAL-Archive-Research/1.0"},
    )
    with urlopen(request, timeout=40) as response:
        html = response.read().decode("utf-8", "replace")

    parser = TableParser()
    parser.feed(html)
    by_date = {}

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
            raise RuntimeError(f"Invalid date in {year} archive: {cells[0]}") from exc

        numbers = [int(x) for x in re.findall(r"\d+", " ".join(cells[1:]))]
        if len(numbers) != 6:
            raise RuntimeError(
                f"{draw_date}: expected 6 numbers, received {numbers}"
            )
        if len(set(numbers)) != 6 or any(n < 1 or n > 60 for n in numbers):
            raise RuntimeError(f"{draw_date}: invalid draw numbers {numbers}")

        record = {"date": draw_date, "numbers": sorted(numbers)}
        if draw_date in by_date:
            previous = by_date[draw_date]
            if previous["numbers"] != record["numbers"]:
                raise RuntimeError(
                    f"CONFLICTING DUPLICATE: {draw_date}; "
                    f"first={previous['numbers']}, second={record['numbers']}"
                )
            print(f"IDENTICAL DUPLICATE ROW: {draw_date}")
            continue
        by_date[draw_date] = record

    results = sorted(by_date.values(), key=lambda row: row["date"])
    if not results:
        raise RuntimeError(
            f"No results found for {year}; source page format may have changed."
        )
    if year in EXPECTED and len(results) != EXPECTED[year]:
        raise RuntimeError(
            f"{year}: expected {EXPECTED[year]} UNIQUE dates, "
            f"received {len(results)}. Archive not saved."
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
        duplicates = sorted({d for d in dates if dates.count(d) > 1})
        raise RuntimeError(f"Duplicate dates across yearly pages: {duplicates}")
    if not all_rows:
        raise RuntimeError("Archive is empty.")

    payload = {
        "source": BASE_URL,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "draws": all_rows,
    }
    temp = OUTPUT.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")
    os.replace(temp, OUTPUT)

    print("Archive saved:", OUTPUT.name)
    print("Total unique rows:", len(all_rows))
    print("First date:", all_rows[0]["date"])
    print("Last date:", all_rows[-1]["date"])
    print("Duplicate and date checks: PASS")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("ARCHIVE FETCH FAILED:", error)
        sys.exit(1)
            
