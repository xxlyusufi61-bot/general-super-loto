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
        self.meta_tags = []
        self.in_script = False
        self.script_parts = []

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if tag.lower() == "meta" and attr.get("property") and attr.get("content"):
            self.meta_tags.append({
                "property": str(attr["property"])[:120],
                "content": str(attr["content"])[:500],
            })
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
            "relevant_meta_tags": [],
            "status": "UNRESOLVED_DYNAMIC_SOURCE",
        }
        if response["body"]:
            parser = PageParser()
            try:
                parser.feed(response["body"])
                row["title"] = " ".join("".join(parser.title_parts).split())[:180]
                row["script_count"] = len(parser.script_urls)
                row["relevant_meta_tags"] = [
                    item for item in parser.meta_tags
                    if re.search(r"gntn|winning|draw|result|api|polaris", item["property"] + " " + item["content"], re.I)
                ]
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

    # Probe the official draw-detail API inferred from the result-page JavaScript.
    api_probe_rows = []
    api_bases = [
        "https://lotteryservice.millipiyangoonline.com/api/",
        "https://digital-info.millipiyangoonline.com/",
    ]

    def collect_six_number_arrays(value, found):
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(child, list) and re.search(r"number|result|winner|table", str(key), re.I):
                    nums = [int(x) for x in child if isinstance(x, (int, str)) and str(x).isdigit()]
                    if len(nums) == 6 and all(1 <= x <= 60 for x in nums):
                        found.append(sorted(nums))
                collect_six_number_arrays(child, found)
        elif isinstance(value, list):
            nums = [int(x) for x in value if isinstance(x, (int, str)) and str(x).isdigit()]
            if len(nums) == 6 and all(1 <= x <= 60 for x in nums):
                found.append(sorted(nums))
            for child in value:
                if isinstance(child, (dict, list)):
                    collect_six_number_arrays(child, found)

    for item in RECORDS:
        for base in api_bases:
            api_url = (
                base + "super-loto/v1/draw-detail/"
                + str(item["draw_no_candidate"]) + "/" + str(item["year"])
            )
            response = fetch(api_url)
            api_row = {
                "date": item["date"],
                "url": api_url,
                "http_status": response["status"],
                "fetch_error": response["error"],
                "date_visible": False,
                "expected_numbers_found": False,
                "matched_number_arrays": [],
                "json_parse_error": False,
                "status": "UNRESOLVED",
            }
            if response["body"]:
                try:
                    payload = json.loads(response["body"])
                    serialized = json.dumps(payload, ensure_ascii=False)
                    api_row["date_visible"] = item["date"] in serialized or item["date"].replace("-", "/") in serialized
                    arrays = []
                    collect_six_number_arrays(payload, arrays)
                    arrays = sorted({tuple(a) for a in arrays})
                    expected = tuple(sorted(item["numbers"]))
                    api_row["matched_number_arrays"] = [list(a) for a in arrays[:20]]
                    api_row["expected_numbers_found"] = expected in arrays
                    if api_row["date_visible"] and api_row["expected_numbers_found"]:
                        api_row["status"] = "DATE_AND_NUMBERS_MATCH"
                    elif api_row["expected_numbers_found"]:
                        api_row["status"] = "NUMBERS_MATCH_DATE_UNCONFIRMED"
                    elif api_row["date_visible"]:
                        api_row["status"] = "DATE_MATCH_NUMBERS_UNCONFIRMED"
                    else:
                        api_row["status"] = "JSON_RECEIVED_CONTEXT_UNRESOLVED"
                except (json.JSONDecodeError, TypeError, ValueError):
                    api_row["json_parse_error"] = True
                    api_row["status"] = "NON_JSON_OR_UNPARSEABLE"
            api_probe_rows.append(api_row)

    # Search a bounded number of shared JavaScript assets for the public result API.
    scripts_checked = []
    discovered = set()
    js_contexts = []
    # Prioritize the bundles that render individual draw results and call APIs.
    def asset_priority(url):
        low = url.lower()
        if "clientlib-singledrawsresult" in low:
            return 0
        if "clientlib-lotterycommon" in low:
            return 1
        if "clientlib-global" in low:
            return 2
        if "clientlib-datalayer" in low:
            return 3
        return 10

    prioritized_js = sorted(js_to_fetch, key=asset_priority)
    context_patterns = [
        re.compile(r"GNTN_ENDPOINT|GNTN_LOTTERY_URL|gntn:endpoint|gntnBaseJsonApi", re.I),
        re.compile(r"winningNumbers|drawId|drawDate|drawNumber|drawNumber", re.I),
        re.compile(r"fetch\(|axios|XMLHttpRequest|\.get\(", re.I),
    ]
    for url in prioritized_js[:MAX_JS_FILES]:
        response = fetch(url)
        scripts_checked.append({
            "url": url, "http_status": response["status"],
            "bytes": len(response["body"]), "truncated": response["truncated"],
            "fetch_error": response["error"],
        })
        if not response["body"]:
            continue
        source = response["body"]
        discovered.update(endpoint_candidates(source))
        # Capture exact source contexts needed to reconstruct the public request.
        exact_terms = [
            ("prepareRequestApiGatewayParams", re.compile(r"prepareRequestApiGatewayParams", re.I), 3),
            ("CLIENTS", re.compile(r"CLIENTS\s*[:=]", re.I), 3),
            ("standard_caller_config", re.compile(r"standard\s*:\s*\{\s*caller", re.I), 4),
            ("epd_caller_config", re.compile(r"epd\s*:\s*\{\s*caller", re.I), 2),
            ("GET_METHOD", re.compile(r"GET_METHOD\s*[:=]", re.I), 4),
            ("GET_METHOD_usage", re.compile(r"GET_METHOD", re.I), 3),
            ("caller", re.compile(r"caller\s*[:=]", re.I), 4),
            ("DrawBlockResultCommand", re.compile(r"DrawBlockResultCommand", re.I), 3),
            ("draw-detail", re.compile(r"draw-detail", re.I), 3),
            ("HeaderHelper", re.compile(r"HeaderHelper\.prepareHeaders", re.I), 2),
            ("getSecretKey", re.compile(r"getSecretKey|getEpdSecretKey", re.I), 2),
        ]        for label, pattern, per_term_limit in exact_terms:
            matches_for_term = 0
            for match in pattern.finditer(source):
                if matches_for_term >= per_term_limit or len(js_contexts) >= 120:
                    break
                left = max(0, match.start() - 900)
                right = min(len(source), match.end() + 1700)
                snippet = re.sub(r"\\s+", " ", source[left:right]).strip()
                js_contexts.append({
                    "asset": url,
                    "term": label,
                    "context": snippet[:2200],
                })
                matches_for_term += 1
            if len(js_contexts) >= 120:
                break
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
        "api_probe_rows": api_probe_rows,
        "api_urls_tested": len(api_probe_rows),
        "api_date_and_number_matches": sum(1 for row in api_probe_rows if row["status"] == "DATE_AND_NUMBERS_MATCH"),
        "relevant_meta_tags": sorted(
            {json.dumps(meta, ensure_ascii=False, sort_keys=True)
             for row in result_rows for meta in row["relevant_meta_tags"]}
        ),
        "javascript_assets_considered": min(len(js_to_fetch), MAX_JS_FILES),
        "javascript_assets": scripts_checked,
        "javascript_endpoint_contexts": js_contexts,
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
    print("Draw-detail API URLs tested:", report["api_urls_tested"])
    for api_row in api_probe_rows:
        print(
            f"API_RESULT date={api_row['date']} http={api_row['http_status']} "
            f"status={api_row['status']} date_match={api_row['date_visible']} "
            f"numbers_match={api_row['expected_numbers_found']} url={api_row['url']}"
        )
    print("Exact date+number API matches:", report["api_date_and_number_matches"])
    print("Relevant official-page meta tags:", len(report["relevant_meta_tags"]))
    for meta_json in report["relevant_meta_tags"]:
        print("META_API", meta_json)
    print("JavaScript asset URLs:")
    for asset in scripts_checked:
        print("JS_ASSET", asset["http_status"], asset["url"])
    print("Targeted JavaScript request contexts:", len(report["javascript_endpoint_contexts"]))
    for entry in report["javascript_endpoint_contexts"]:
        print("JS_CONTEXT_TERM", entry["term"], entry["asset"], entry["context"])
    print("Endpoint candidates found:", len(report["endpoint_candidates"]))
    for candidate in report["endpoint_candidates"]:
        print("CANDIDATE", candidate)
    print("Official numbers verified: NO")
    print("Report:", OUT.name)

if __name__ == "__main__":
    main()
