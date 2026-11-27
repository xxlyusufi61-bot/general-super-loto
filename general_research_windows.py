"""Leakage-free planner for genuinely unseen chronological research packages."""
from __future__ import annotations
import hashlib, json
from datetime import date
from typing import Any

PARTITION_VERSION = 3
RESEARCH_SCOPE_START = "2020-08-02"
MIN_DISCOVERY_DRAWS = 40
MIN_VALIDATION_DRAWS = 2
MAX_VALIDATION_DRAWS = 30
MIN_PACKAGE_DRAWS = 44

class ResearchWindowError(ValueError):
    pass

def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")

def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()

def day(value: Any) -> str:
    if not isinstance(value, str):
        raise ResearchWindowError("Date is not text.")
    try: parsed = date.fromisoformat(value)
    except ValueError: raise ResearchWindowError("Date is not ISO canonical.") from None
    if parsed.isoformat() != value: raise ResearchWindowError("Date is not ISO canonical.")
    return value

def archive_dates(rows):
    if not isinstance(rows, list) or len(rows) < 2:
        raise ResearchWindowError("Archive must contain historical rows and one isolated target.")
    values = [day(row.get("date")) for row in rows]
    if any(value < RESEARCH_SCOPE_START for value in values):
        raise ResearchWindowError("Archive contains a pre-2020-08-02 record outside active scope.")
    if values != sorted(values) or len(values) != len(set(values)):
        raise ResearchWindowError("Archive chronology is not strictly increasing and unique.")
    return values

def legacy_discovery_dates(rows, memory):
    dates = archive_dates(rows)
    exposed = set()
    if memory is None: return exposed
    if not isinstance(memory, dict) or not isinstance(memory.get("entries"), list):
        raise ResearchWindowError("GENERAL memory schema is invalid.")
    for index, entry in enumerate(memory["entries"]):
        window = entry.get("discovery_window") if isinstance(entry, dict) else None
        if not isinstance(window, dict):
            raise ResearchWindowError(f"Legacy memory entry {index + 1} lacks exposure provenance.")
        start, end = day(window.get("start_date")), day(window.get("end_date"))
        count = window.get("draw_count")
        if start > end or type(count) is not int or count < 0:
            raise ResearchWindowError("Legacy discovery-window metadata is malformed.")
        matched = {d for d in dates[:-1] if start <= d <= end}
        if len(matched) < count:
            raise ResearchWindowError("A legacy discovery window cannot be reconstructed from the archive.")
        exposed.update(matched)
    return exposed

def offline_dates(memory):
    if memory is None: return set()
    offline = memory.get("offline_research", {})
    if not isinstance(offline, dict) or not isinstance(offline.get("consumed_development_windows", []), list):
        raise ResearchWindowError("Offline development ledger is malformed.")
    return {day(d) for item in offline.get("consumed_development_windows", [])
            for d in item.get("dates", [])}

def package_status(ledger, exposure):
    states = [e["state"] for e in ledger.get("discovery_exposures", [])
              if e.get("kind") == "RESEARCH_PACKAGE_STATUS" and e.get("exposure_id") == exposure.get("exposure_id")]
    return states[-1] if states else exposure.get("state", "RESERVED")

def exposure_dates(ledger):
    result = set()
    exposures = ledger.get("discovery_exposures", [])
    if not isinstance(exposures, list): raise ResearchWindowError("Exposure ledger malformed.")
    for item in exposures:
        if not isinstance(item, dict): raise ResearchWindowError("Exposure item malformed.")
        kind = item.get("kind")
        if kind == "RESEARCH_PACKAGE_STATUS": continue
        dates = {day(d) for d in item.get("dates", [])}
        if kind == "LEGACY_DISCOVERY_RANGES":
            result.update(dates); continue
        if kind != "RESEARCH_PACKAGE": raise ResearchWindowError("Unknown exposure type.")
        status = package_status(ledger, item)
        if status == "RELEASED":
            continue
        result.update(day(d) for d in item.get("discovery_dates", []))
        if status in {"RESERVED", "CLAIMED", "EVALUATED"}:
            result.update(day(d) for d in item.get("validation_dates", []) + item.get("reverse_validation_dates", []))
    return result

def holdout_dates(ledger):
    return {day(d) for e in ledger.get("entries", [])
            for field in ("validation_dates", "reverse_validation_dates")
            for d in e.get(field, [])}

def unavailable_dates(rows, *, ledger, memory, attempts):
    archive = set(archive_dates(rows)[:-1])
    prior = legacy_discovery_dates(rows, memory) | offline_dates(memory) | exposure_dates(ledger) | holdout_dates(ledger)
    if not prior.issubset(archive):
        raise ResearchWindowError("A previously exposed or consumed date is absent from the canonical archive.")
    for attempt in attempts:
        pkg = attempt.get("research_package") if isinstance(attempt, dict) else None
        if isinstance(pkg, dict):
            for field in ("discovery_dates", "validation_dates", "reverse_validation_dates"):
                prior.update(day(d) for d in pkg.get(field, []))
    if not prior.issubset(archive):
        raise ResearchWindowError("A scheduler package refers to a missing or target date.")
    return prior

def _blocks(rows, unavailable):
    blocks, current = [], []
    for row in rows:
        if row["date"] in unavailable:
            if current: blocks.append(current); current = []
        else: current.append(row)
    if current: blocks.append(current)
    return blocks

def _package(segment, *, target, archive_sha256, archive_count, sequence, prior, offline_sha):
    n = min(MAX_VALIDATION_DRAWS, (len(segment) - MIN_DISCOVERY_DRAWS) // 2)
    if len(segment) < MIN_PACKAGE_DRAWS or n < MIN_VALIDATION_DRAWS:
        raise ResearchWindowError("Package does not meet fixed validation minima.")
    reverse, discovery, forward = segment[:n], segment[n:-n], segment[-n:]
    if len(discovery) < MIN_DISCOVERY_DRAWS: raise ResearchWindowError("Discovery minimum not met.")
    dates = {
        "discovery_dates": [r["date"] for r in discovery],
        "validation_dates": [r["date"] for r in forward],
        "reverse_validation_dates": [r["date"] for r in reverse],
    }
    all_dates = dates["discovery_dates"] + dates["validation_dates"] + dates["reverse_validation_dates"]
    if len(all_dates) != len(set(all_dates)) or set(all_dates) & prior:
        raise ResearchWindowError("Package roles overlap or reuse previously unavailable dates.")
    item = {
        "schema_version": 1, "partition_version": 3, "target_date": target,
        "archive_sha256": archive_sha256, "archive_record_count": archive_count,
        "package_sequence": sequence, "package_start_date": segment[0]["date"],
        "package_end_date": segment[-1]["date"], "discovery_start_date": discovery[0]["date"],
        "discovery_end_date": discovery[-1]["date"], "discovery_count": len(discovery),
        "validation_start_date": forward[0]["date"], "validation_end_date": forward[-1]["date"],
        "validation_count": len(forward), "reverse_validation_start_date": reverse[0]["date"],
        "reverse_validation_end_date": reverse[-1]["date"], "reverse_validation_count": len(reverse),
        **dates, "dates": sorted(all_dates), "previously_unavailable_dates": sorted(prior),
        "previously_unavailable_date_count": len(prior),
        "previously_unavailable_dates_sha256": digest(sorted(prior)),
        "offline_development_memory_sha256": offline_sha,
        "target_numbers_exposed": False, "target_in_discovery": False,
        "target_in_validation": False, "target_in_reverse_validation": False,
        "directional_validation_windows_disjoint": True,
    }
    item["package_id"] = digest(item)
    return item

def build_research_packages(rows, *, archive_sha256, unavailable, sequence_start=1, offline_memory_sha256=None):
    dates = archive_dates(rows)
    if not isinstance(archive_sha256, str) or len(archive_sha256) != 64:
        raise ResearchWindowError("Archive hash is invalid.")
    if not set(unavailable).issubset(set(dates[:-1])):
        raise ResearchWindowError("Unavailable dates must be pre-target archive dates.")
    if type(sequence_start) is not int or sequence_start < 1:
        raise ResearchWindowError("Package sequence invalid.")
    offline_memory_sha256 = offline_memory_sha256 or digest([])
    if (not isinstance(offline_memory_sha256, str) or len(offline_memory_sha256) != 64
        or any(ch not in "0123456789abcdef" for ch in offline_memory_sha256)):
        raise ResearchWindowError("Offline memory hash invalid.")
    result, sequence, prior = [], sequence_start, set(unavailable)
    for block in _blocks(rows[:-1], prior):
        count = len(block) // MIN_PACKAGE_DRAWS
        if not count: continue
        size, extra = divmod(len(block), count)
        cursor = 0
        for i in range(count):
            width = size + (1 if i < extra else 0)
            segment = block[cursor:cursor + width]; cursor += width
            if len(segment) > 100: raise ResearchWindowError("Package exceeds 100-row bound.")
            item = _package(segment, target=dates[-1], archive_sha256=archive_sha256,
                            archive_count=len(rows), sequence=sequence, prior=prior,
                            offline_sha=offline_memory_sha256)
            result.append(item); prior.update(item["dates"]); sequence += 1
    return result

def validate_package(package, rows, *, archive_sha256, unavailable):
    dates = archive_dates(rows)
    if package.get("partition_version") != 3 or package.get("target_date") != dates[-1]:
        raise ResearchWindowError("V3 package target/version mismatch.")
    if package.get("archive_sha256") != archive_sha256 or package.get("archive_record_count") != len(rows):
        raise ResearchWindowError("V3 package archive binding mismatch.")
    if any(package.get(flag) is not False for flag in (
        "target_numbers_exposed", "target_in_discovery", "target_in_validation", "target_in_reverse_validation"
    )): raise ResearchWindowError("V3 target isolation failed.")
    start, end = day(package.get("package_start_date")), day(package.get("package_end_date"))
    starts = [i for i, row in enumerate(rows[:-1]) if row["date"] == start]
    if len(starts) != 1: raise ResearchWindowError("Package start not canonical.")
    segment = [r for r in rows[starts[0]:-1] if r["date"] <= end]
    if not segment or segment[-1]["date"] != end or [r["date"] for r in segment] != package.get("dates"):
        raise ResearchWindowError("Package is not one exact contiguous interval.")
    if set(package["dates"]) & unavailable: raise ResearchWindowError("Package reuses a prior date.")
    expected = _package(segment, target=dates[-1], archive_sha256=archive_sha256,
                        archive_count=len(rows), sequence=package["package_sequence"],
                        prior=set(unavailable), offline_sha=package["offline_development_memory_sha256"])
    if expected != package: raise ResearchWindowError("Package metadata/hash mismatch.")
    return expected

def make_package_exposure(package, *, attempt_id, created_at_utc):
    if (not isinstance(attempt_id, str) or len(attempt_id) != 64
        or any(ch not in "0123456789abcdef" for ch in attempt_id)):
        raise ResearchWindowError("Attempt ID invalid.")
    if not isinstance(created_at_utc, str) or not created_at_utc.endswith("+00:00"):
        raise ResearchWindowError("Package reservation timestamp must be explicit UTC.")
    try: timestamp_day = date.fromisoformat(created_at_utc[:10])
    except ValueError: raise ResearchWindowError("Package reservation timestamp date invalid.") from None
    item = {
        "kind": "RESEARCH_PACKAGE", "schema_version": 1, "exposure_id": "",
        "attempt_id": attempt_id, "package_id": package["package_id"], "package": package,
        "target_date": package["target_date"], "archive_sha256": package["archive_sha256"],
        "archive_record_count": package["archive_record_count"], "partition_version": 3,
        "package_sequence": package["package_sequence"], "package_start_date": package["package_start_date"],
        "package_end_date": package["package_end_date"], "discovery_dates": package["discovery_dates"],
        "validation_dates": package["validation_dates"], "reverse_validation_dates": package["reverse_validation_dates"],
        "dates": package["dates"], "previously_unavailable_dates_sha256": package["previously_unavailable_dates_sha256"],
        "created_at_utc": created_at_utc, "target_numbers_exposed": False, "state": "RESERVED",
    }
    item["exposure_id"] = digest(item)
    return item

def legacy_exposure_record(dates, memory_sha256, created_at_utc):
    if not dates: return None
    if (not isinstance(memory_sha256, str) or len(memory_sha256) != 64
        or any(ch not in "0123456789abcdef" for ch in memory_sha256)):
        raise ResearchWindowError("Legacy research-memory hash invalid.")
    item = {"kind": "LEGACY_DISCOVERY_RANGES", "schema_version": 1, "exposure_id": "",
            "memory_sha256": memory_sha256, "dates": sorted(dates), "created_at_utc": created_at_utc}
    item["exposure_id"] = digest(item)
    return item

def ensure_legacy_exposure(ledger, rows, memory, *, memory_sha256, created_at_utc):
    entries = ledger.setdefault("discovery_exposures", [])
    legacy = legacy_discovery_dates(rows, memory)
    already = exposure_dates(ledger) | holdout_dates(ledger)
    record = legacy_exposure_record(legacy - already, memory_sha256, created_at_utc)
    if record is not None: entries.append(record)
    return record

def package_exposure_status(ledger, exposure):
    events = [e for e in ledger.get("discovery_exposures", [])
              if e.get("kind") == "RESEARCH_PACKAGE_STATUS" and e.get("exposure_id") == exposure.get("exposure_id")]
    return events[-1]["state"] if events else exposure.get("state", "RESERVED")

def append_package_status(ledger, exposure, *, state, recorded_at_utc, reason):
    current = package_exposure_status(ledger, exposure)
    allowed = {"RESERVED": {"EXPOSED", "CLAIMED", "RELEASED"},
               "EXPOSED": {"CLAIMED", "EVALUATED"}, "CLAIMED": {"EVALUATED"},
               "EVALUATED": set(), "RELEASED": {"RESERVED"}}
    if state != current and state not in allowed.get(current, set()):
        raise ResearchWindowError(f"Invalid package lifecycle transition {current}->{state}.")
    event = {"kind": "RESEARCH_PACKAGE_STATUS", "schema_version": 1,
             "exposure_id": exposure["exposure_id"], "package_id": exposure["package_id"],
             "attempt_id": exposure["attempt_id"], "prior_state": current, "state": state,
             "recorded_at_utc": recorded_at_utc, "reason": str(reason)[:120]}
    event["event_id"] = digest(event)
    ledger.setdefault("discovery_exposures", []).append(event)
    return event

def reserve_research_package(ledger, package, *, attempt_id, created_at_utc):
    already = exposure_dates(ledger) | holdout_dates(ledger)
    overlaps = set(package["dates"]) & already
    previous = [
        item for item in ledger.get("discovery_exposures", [])
        if isinstance(item, dict) and item.get("kind") == "RESEARCH_PACKAGE"
        and item.get("package_id") == package.get("package_id")
    ]
    retry_allowed = False
    if previous:
        latest = previous[-1]
        if latest.get("package") != package:
            raise ResearchWindowError("A package ID cannot be rebound to different dates or roles.")
        retry_allowed = package_status(ledger, latest) in {"EXPOSED", "RELEASED"}
    if overlaps and not retry_allowed:
        raise ResearchWindowError("Package overlaps prior exposure or claim.")
    exposure = make_package_exposure(package, attempt_id=attempt_id, created_at_utc=created_at_utc)
    if any(item.get("attempt_id") == attempt_id and item.get("package_id") == package.get("package_id")
           for item in ledger.get("discovery_exposures", []) if isinstance(item, dict)):
        raise ResearchWindowError("This attempt already reserved the package.")
    ledger.setdefault("discovery_exposures", []).append(exposure)
    return exposure

def package_from_exposure(exposure):
    if exposure.get("kind") != "RESEARCH_PACKAGE" or exposure.get("package", {}).get("package_id") != exposure.get("package_id"):
        raise ResearchWindowError("Package exposure reservation malformed.")
    return exposure["package"]

def package_exposure_for_attempt(ledger, *, package_id=None, attempt_id=None):
    matches = [e for e in ledger.get("discovery_exposures", [])
               if e.get("kind") == "RESEARCH_PACKAGE"
               and (package_id is None or e.get("package_id") == package_id)
               and (attempt_id is None or e.get("attempt_id") == attempt_id)]
    if len(matches) > 1: raise ResearchWindowError("Package reservation ambiguous.")
    return matches[0] if matches else None

def split_from_package(rows, package, *, archive_sha256):
    validate_package(package, rows, archive_sha256=archive_sha256,
                     unavailable=set(package["previously_unavailable_dates"]))
    by_date = {r["date"]: {"date": r["date"], "numbers": list(r["numbers"])} for r in rows}
    role = {"regime": "R60", "number_pool_size": 60, "status": "READY", "reason": None,
            "pre_target_draw_count": len([r for r in rows if r["date"] < package["target_date"]]),
            "available_pre_target_draw_count": len(package["dates"]),
            "previously_consumed_draw_count": package["previously_unavailable_date_count"],
            "discovery_rows": [by_date[d] for d in package["discovery_dates"]],
            "validation_rows": [by_date[d] for d in package["validation_dates"]],
            "reverse_validation_rows": [by_date[d] for d in package["reverse_validation_dates"]],
            "discovery_count": package["discovery_count"], "validation_count": package["validation_count"],
            "reverse_validation_count": package["reverse_validation_count"],
            "discovery_start_date": package["discovery_start_date"], "discovery_end_date": package["discovery_end_date"],
            "validation_start_date": package["validation_start_date"], "validation_end_date": package["validation_end_date"],
            "reverse_validation_start_date": package["reverse_validation_start_date"],
            "reverse_validation_end_date": package["reverse_validation_end_date"]}
    return {"schema_version": 1, "partition_version": 3, "orchestrator_version": "0.6.0",
            "engine_version": "V3_UNSEEN_PACKAGE", "target_date": package["target_date"],
            "archive_sha256": archive_sha256, "archive_record_count": len(rows),
            "pre_target_draw_count": role["pre_target_draw_count"],
            "previously_consumed_validation_draw_count": package["previously_unavailable_date_count"],
            "previously_consumed_offline_development_draw_count": 0,
            "offline_development_memory_sha256": package["offline_development_memory_sha256"],
            "target_numbers_exposed": False, "target_in_discovery": False,
            "target_in_validation": False, "target_in_reverse_validation": False,
            "directional_validation_windows_disjoint": True,
            "discovery_rows": role["discovery_rows"], "discovery_count": len(role["discovery_rows"]),
            "regimes": {"R60": role}, "research_package": package}
