#!/usr/bin/env python3
"""Read-only probe of official Milli Piyango Online Super Loto result pages.

This is an endpoint-discovery probe, not an official verification result.
It never marks a record verified merely because a page URL returns HTTP 200.
"""
from __future__ import annotations

import json
import re
import sys
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from pathlib import Path

BASE = "https://www.millipiyangoonline.com"
OUT = Path(__file__).resolve().parent / "official_source_endpoint_probe.json"
TIMEOUT = 18
MAX_JS_FILES = 20
MAX_JS_BYTES = 2_500_000

# Candidate draw numbers are derived from the per-year draw sequence.
# The probe tests the pages; it does not assume the mapping is verified.
RECORDS = [
    {"date": "2021-01-31", "draw_no_candidate": 13, "year": 2021,
     "numbers": [1, 7, 36, 44, 46, 59]},
    {"date": "2021-11-30", "draw_no_candidate": 143, "year": 2021,
     "numbers": [24, 27, 43, 45, 51, 53]},
    {"date": "2022-01-02", "draw_no_candidate": 1, "year": 2022,
     "numbers": [41, 43, 46, 47, 48, 51]},
    {"date": "2023-08-17", "draw_no_candidate": 99, "year": 2023,
     "numbers": [5, 10, 19, 25, 33, 35]},
    {"date": "2023-08-20", "draw_no_candidate": 100, "year": 2023,
     "numbers": [12, 18, 39, 48, 54, 59]},
    {"date": "2023-10-22", "draw_no_candidate": 127, "year": 2023,
     "numbers": [6, 7, 14, 37, 49, 57]},
    {"date": "2023-10-24", "draw_no_candidate": 128, "year": 2023,
     "numbers": [7, 10, 23, 27, 46, 58]},
    {"date": "2023-10-26", "draw_no_candidate": 129, "year": 2023,
     "numbers": [1, 31, 40, 42, 45, 56]},
    {"date": "2023-10-29", "draw_no_candidate": 130, "year": 2023,
     "numbers": [7, 12, 20, 25, 47, 48]},
    {"date": "2023-11-05", "draw_no_candidate": 133, "year": 2023,
     "numbers": [6, 13, 26, 50, 55, 60]},
]

class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts = []
        self.in_title = False
        self.text_parts = []
        self.script_urls = []
        self.inline_scripts = []
        self.in_script = False
        self.script_parts = []

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if tag.lower() == "title":
            self.in_title = True
        if tag.lower() == "script":
            src = attr.get("src")
            if src:
                self.script_urls.append(src)
            else:
                self.in_script = True
                self.script_parts = []

    def handle_data(self, data):
        self.text_parts.append(data)
        if self.in_title:
            self.title_parts.append(data)
        if self.in_script:
            self.script_parts.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False
        elif tag.lower() == "script" and self.in_script:
            self.inline_scripts.append("".join(self.script_parts))
            self.in_script = False
            self.script_parts = []

def fetch(url, max_bytes=MAX_JS_BYTES):
    req = Request(url, headers={
        "User-Agent": "GENERAL-SuperLoto-OfficialSourceProbe/0.1",
        "Accept": "text/html,application/javascript,*/*;q=0.8",
    })
    try:
        with urlopen(req, timeout=TIMEOUT) as response:
            status = response.status
            final_url = response.geturl()
            data = response.read(max_bytes + 1)
            truncated = len(data) > max_bytes
            if truncated:
                data = data[:max_bytes]
            return {
                "status": status, "final_url": final_url,
                "body": data.decode("utf-8", "replace"),
                "truncated": truncated, "error": None,
            }
    except HTTPError as exc:
        return {"status": exc.code, "final_url": url, "body": "",
                "truncated": False, "error": "HTTP_ERROR"}
    except (URLError, TimeoutError, OSError):
        return {"status": None, "final_url": url, "body": "",
                "truncated": False, "error": "NETWORK_ERROR"}

def number_set_present(text, numbers):
    # Accept normal separators but require each of the six numbers in one short
    # local substring; this is only a probe signal, never proof of a draw date.
    compact = re.sub(r"\s+", " ", text)
    for sep in (r"[\s,;/|:\-]+", r"[\s,;/|]+"):
        sequence = sep.join(map(str, numbers))
        if re.search(r"(?<!\d)" + sequence + r"(?!\d)", compact):
            return True
    return False

def endpoint_candidates(text):
    patterns = [
        re.compile(r"""['"\x60]((?:https?:)?//[^'"\x60\s]{1,220})['"\x60]""", re.I),
        re.compile(r"""['"\x60](/[^'"\x60\s]{1,220})['"\x60]"""),
    ]
    keywords = ("api", "draw", "result", "lottery", "superloto",
                "super-loto", "winningnumber", "drawdate", "drawn")
    found = set()
    for pattern in patterns:
        for match in pattern.finditer(text):
            candidate = match.group(1).replace("\\/", "/")
            lower = candidate.lower()
            if any(word in lower for word in keywords):
                if len(candidate) <= 220:
                    found.add(candidate)
    return sorted(found)[:80]

def main():
    page_cache = {}
    result_rows = []
    js_to_fetch = []
    seen_js = set()

    for item in RECORDS:
        url = f"{BASE}/super-loto/cekilis-sonuclari.{item['draw_no_candidate']}.{item['year']}"
        response = fetch(url)
        row = {
            "date": item["date"],
            "draw_no_candidate": item["draw_no_candidate"],
            "year": item["year"],
            "url": url,
            "http_status": response["status"],
            "final_url": response["final_url"],
            "html_bytes": len(response["body"]),
            "html_truncated": response["truncated"],
            "fetch_error": response["error"],
            "date_visible_in_page": item["date"] in response["body"],
            "number_set_visible_in_page": number_set_present(
                response["body"], item["numbers"]),
            "title": "",
            "script_count": 0,
            "status": "UNRESOLVED_DYNAMIC_SOURCE",
        }
        if response["body"]:
            parser = PageParser()
            try:
                parser.feed(response["body"])
                row["title"] = " ".join("".join(parser.title_parts).split())[:180]
                row["script_count"] = len(parser.script_urls)
                for src in parser.script_urls:
                    full = urljoin(response["final_url"], src)
                    parsed = urlparse(full)
                    if parsed.scheme in ("http", "https") and full not in seen_js:
                        seen_js.add(full)
                        if parsed.path.lower().endswith(".js") or ".js?" in full:
                            js_to_fetch.append(full)
                # Keep inline script text in local scan, without writing full page HTML.
                page_cache[url] = response["body"] + "\n" + "\n".join(parser.inline_scripts)
                if row["date_visible_in_page"] and row["number_set_visible_in_page"]:
                    row["status"] = "PAGE_CONTAINS_DATE_AND_NUMBER_SET_NEEDS_CONTEXT_CHECK"
            except Exception:
                row["parser_error"] = "HTML_PARSE_ERROR"
        result_rows.append(row)

    # Search a bounded number of shared JavaScript assets for the public result API.
    scripts_checked = []
    discovered = set()
    for url in js_to_fetch[:MAX_JS_FILES]:
        response = fetch(url)
        scripts_checked.append({
            "url": url, "http_status": response["status"],
            "bytes": len(response["body"]), "truncated": response["truncated"],
            "fetch_error": response["error"],
        })
        if response["body"]:
            discovered.update(endpoint_candidates(response["body"]))
    for body in page_cache.values():
        discovered.update(endpoint_candidates(body))

    report = {
        "schema_version": 1,
        "purpose": "Read-only endpoint discovery; not a verification claim.",
        "official_host": "www.millipiyangoonline.com",
        "official_result_pages_directly_fetched": sum(
            1 for row in result_rows if row["http_status"] == 200
        ),
        "records": result_rows,
        "javascript_assets_considered": min(len(js_to_fetch), MAX_JS_FILES),
        "javascript_assets": scripts_checked,
        "endpoint_candidates": sorted(discovered)[:80],
        "official_numbers_verified": False,
        "notes": [
            "HTTP 200 is not official number verification.",
            "A result page must be tied to the exact draw date and all six expected numbers.",
            "The report deliberately omits the historical number sets and never requests the latest target draw."
        ],
    }
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("OFFICIAL SOURCE ENDPOINT PROBE")
    print("Result pages fetched successfully:", report["official_result_pages_directly_fetched"], "/", len(RECORDS))
    print("JavaScript assets scanned:", report["javascript_assets_considered"])
    for row in result_rows:
        print(
            f"{row['date']}: HTTP={row['http_status']} "
            f"date_visible={row['date_visible_in_page']} "
            f"numbers_visible={row['number_set_visible_in_page']} "
            f"status={row['status']}"
        )
    print("JavaScript asset URLs:")
    for asset in scripts_checked:
        print("JS_ASSET", asset["http_status"], asset["url"])
    print("Endpoint candidates found:", len(report["endpoint_candidates"]))
    for candidate in report["endpoint_candidates"]:
        print("CANDIDATE", candidate)
    print("Official numbers verified: NO")
    print("Report:", OUT.name)

if __name__ == "__main__":
    main()
