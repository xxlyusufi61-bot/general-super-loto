"""GitHub Actions test ve araştırma raporlarını Türkçe olarak Telegram'a gönderir."""
import argparse
import hashlib
import json
import math
import os
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

LABELS = {
    "abc_transition_to_c": "A->B->C deneysel adayı",
    "a_to_t_pair_counts": "A->T deneysel adayı",
    "mod5_state_to_t": "MOD5->T deneysel adayı",
    "frequency_top6": "En sık çıkan 6 sayı temel yöntemi",
    "frequency_bottom6": "En az çıkan 6 sayı temel yöntemi",
    "adjacent_draw_overlap": "Ardışık çekiliş örtüşmesi temel yöntemi",
}


def read_report(path):
    if not path:
        return None
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None

    # Evaluation reports are pretty-printed JSON files, while older logs may
    # contain one JSON object per line. Support both without exposing raw data.
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        value = None
    if isinstance(value, dict):
        return value

    for line in reversed(raw.splitlines()):
        try:
            value = json.loads(line.strip())
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    return None


def metric_lines(value):
    output = []
    if not isinstance(value, dict):
        return output
    for name, stats in value.items():
        if not isinstance(stats, dict):
            continue
        hits = stats.get("mean_hits_out_of_6", stats.get("observed_mean_hits_out_of_6"))
        count = stats.get("evaluated_draws")
        if isinstance(hits, (int, float)) and not isinstance(hits, bool):
            suffix = f"{hits:.3f}/6 ortalama isabet"
            if isinstance(count, int):
                suffix += f", {count} çekiliş"
            output.append(f"- {LABELS.get(name, name)}: {suffix}")
    return output


def _safe_number(value, field):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(field + " is missing or invalid.")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 6.0:
        raise ValueError(field + " must be a finite hit metric in the 0..6 range.")
    return number


def _safe_candidate(candidate_id, spec_sha, field):
    if not isinstance(candidate_id, str) or not candidate_id or len(candidate_id) > 100:
        raise ValueError(field + " candidate identifier is invalid.")
    if (
        not isinstance(spec_sha, str)
        or len(spec_sha) != 64
        or any(char not in "0123456789abcdef" for char in spec_sha)
    ):
        raise ValueError(field + " specification hash is invalid.")
    return candidate_id, spec_sha


def _format_draw_lines(draws, direction, candidate_id, spec_sha, target_day):
    if not isinstance(draws, list) or not draws or len(draws) > 30:
        raise ValueError(direction + " per-draw details are missing or outside the safe report limit.")
    output = []
    expected_direction = "forward" if direction == "forward" else "reverse_retrospective"
    history_key = "training_draw_count" if direction == "forward" else "later_training_draw_count"
    dates = []
    for item in draws:
        if not isinstance(item, dict) or item.get("direction") != expected_direction:
            raise ValueError(direction + " per-draw direction mismatch.")
        if item.get("hypothesis_id") != candidate_id or item.get("spec_sha256") != spec_sha:
            raise ValueError(direction + " per-draw candidate provenance mismatch.")
        if item.get("target_numbers_reported") is not False:
            raise ValueError(direction + " report does not prove target-number omission.")
        if item.get("predicted_number_values_reported") is not False:
            raise ValueError(direction + " report does not prove predicted-number omission.")
        day = item.get("test_date")
        try:
            parsed = date.fromisoformat(day)
        except (TypeError, ValueError):
            raise ValueError(direction + " test date is invalid.") from None
        if parsed.isoformat() != day:
            raise ValueError(direction + " test date is not canonical.")
        if parsed < date(2020, 8, 2) or parsed >= target_day:
            raise ValueError(direction + " test date is outside the pre-target 6-of-60 scope.")
        dates.append(day)
        history_count = item.get(history_key)
        hit_count = item.get("hit_count")
        positions = item.get("hit_column_positions_1_to_6")
        if type(history_count) is not int or history_count < 1:
            raise ValueError(direction + " reference-history count is invalid.")
        if type(hit_count) is not int or not 0 <= hit_count <= 6:
            raise ValueError(direction + " hit count is invalid.")
        if not isinstance(positions, list) or len(positions) != hit_count:
            raise ValueError(direction + " matched-column positions do not reconcile to hit count.")
        if any(type(position) is not int or not 1 <= position <= 6 for position in positions):
            raise ValueError(direction + " matched-column positions are invalid.")
        if positions != sorted(set(positions)):
            raise ValueError(direction + " matched-column positions are duplicated or unordered.")
        position_text = ", ".join(str(value) for value in positions) if positions else "yok"
        output.append(
            f"{day}: {hit_count}/6 isabet | sütunlar: {position_text} | referans geçmiş: {history_count}"
        )
    expected_dates = sorted(dates, reverse=(direction == "reverse"))
    if dates != expected_dates or len(dates) != len(set(dates)):
        raise ValueError(direction + " test dates must be unique and directionally chronological.")
    return output


def format_research_messages(report, run_url="", branch="main", title="Otonom Araştırma"):
    """Create separate safe forward and reverse Telegram messages."""
    if not isinstance(report, dict) or report.get("status") != "EVALUATED_EXPLORATORY_FIXED_SPEC":
        raise ValueError("A completed sealed evaluation report is required.")
    safety_flags = (
        "current_isolated_target_included",
        "current_target_numbers_exposed",
        "target_or_future_rows_used",
        "per_draw_target_numbers_reported",
        "candidate_rankings_reported",
    )
    if any(report.get(key) is not False for key in safety_flags):
        raise ValueError("Evaluation report failed target/number omission checks.")

    target_date = report.get("target_date")
    try:
        target_day = date.fromisoformat(target_date)
    except (TypeError, ValueError):
        raise ValueError("Isolated target date is invalid.") from None
    if target_day.isoformat() != target_date:
        raise ValueError("Isolated target date is not canonical.")

    selection = report.get("candidate_selection")
    forward = report.get("forward_walk_forward")
    reverse = report.get("reverse_discovered_candidate_holdout")
    forward_cv = report.get("time_ordered_cross_validation")
    reverse_cv = report.get("reverse_time_ordered_cross_validation")
    if not all(isinstance(item, dict) for item in (selection, forward, reverse, forward_cv, reverse_cv)):
        raise ValueError("Bidirectional evaluation sections are incomplete.")

    forward_id, forward_sha = _safe_candidate(
        selection.get("forward_hypothesis_id"),
        selection.get("forward_spec_sha256"),
        "forward",
    )
    reverse_id, reverse_sha = _safe_candidate(
        selection.get("reverse_hypothesis_id"),
        selection.get("reverse_spec_sha256"),
        "reverse",
    )
    if reverse.get("predictive_evidence") is not False:
        raise ValueError("Reverse results must be explicitly marked retrospective-only.")
    if reverse_cv.get("predictive_evidence") is not False:
        raise ValueError("Reverse cross-validation must be explicitly marked retrospective-only.")

    forward_mean = _safe_number(forward.get("mean_hits_out_of_6"), "forward mean")
    forward_random = _safe_number(
        forward.get("uniform_random_expected_hits_out_of_6"), "forward random reference"
    )
    forward_cv_mean = _safe_number(forward_cv.get("mean_hits_out_of_6"), "forward CV mean")
    reverse_mean = _safe_number(reverse.get("mean_hits_out_of_6"), "reverse mean")
    reverse_random = _safe_number(
        reverse.get("uniform_random_expected_hits_out_of_6"), "reverse random reference"
    )
    reverse_cv_mean = _safe_number(reverse_cv.get("mean_hits_out_of_6"), "reverse CV mean")
    forward_count = forward.get("validation_draw_count")
    reverse_count = reverse.get("test_draw_count")
    forward_folds = forward_cv.get("folds")
    reverse_folds = reverse_cv.get("folds")
    if any(type(item) is not int or item < 1 for item in (
        forward_count, reverse_count, forward_folds, reverse_folds
    )):
        raise ValueError("Directional evaluation counts or fold counts are invalid.")

    forward_draws = _format_draw_lines(forward.get("per_draw"), "forward", forward_id, forward_sha, target_day)
    reverse_draws = _format_draw_lines(reverse.get("per_draw"), "reverse", reverse_id, reverse_sha, target_day)
    if len(forward_draws) != forward_count or len(reverse_draws) != reverse_count:
        raise ValueError("Per-draw report counts do not match their directional holdout counts.")
    forward_dates = [item["test_date"] for item in forward["per_draw"]]
    reverse_dates = [item["test_date"] for item in reverse["per_draw"]]
    if set(forward_dates).intersection(reverse_dates):
        raise ValueError("Forward and reverse Telegram reports contain overlapping holdout dates.")
    if forward_dates[0] != report.get("validation_start_date") or forward_dates[-1] != report.get("validation_end_date"):
        raise ValueError("Forward Telegram dates do not match the sealed validation range.")
    if reverse_dates[0] != reverse.get("first_test_date") or reverse_dates[-1] != reverse.get("last_test_date"):
        raise ValueError("Reverse Telegram dates do not match the sealed validation range.")

    common = [
        f"Hedef tarihi (çekiliş sayıları gizli): {target_date}",
        "Arşiv kapsamı: 02.08.2020 ve sonrası, 6/60.",
        "Hedef çekiliş keşif ve puanlamaya alınmadı.",
    ]
    link = f"GitHub Actions: {run_url}" if run_url else ""
    forward_lines = [
        f"✅ GENERAL — İLERİ YÖN | {title}",
        *common,
        f"Aday: {forward_id}",
        f"Tanım SHA-256: {forward_sha}",
        f"İleri holdout: {report.get('validation_start_date')} – {report.get('validation_end_date')}",
        f"Test: {forward_count} çekiliş | ortalama {forward_mean:.3f}/6",
        f"Rastgele referans: {forward_random:.3f}/6",
        f"Zaman sıralı CV: {forward_folds} kat | {forward_cv_mean:.3f}/6",
        "Not: CV, aynı mühürlü aralığın bölünmüş özetidir; bağımsız doğrulama değildir.",
        "Çekiliş başına sonuçlar (tahmin edilen sayılar gösterilmez):",
        *forward_draws,
        "Durum: Deneysel bulgu; tek başına tahmin gücü veya mekanizma kanıtı değildir.",
    ]
    reverse_lines = [
        f"✅ GENERAL — TERS YÖN / RETROSPEKTİF | {title}",
        *common,
        f"Aday: {reverse_id}",
        f"Tanım SHA-256: {reverse_sha}",
        f"Ters holdout: {reverse.get('first_test_date')} – {reverse.get('last_test_date')}",
        f"Test: {reverse_count} çekiliş | ortalama {reverse_mean:.3f}/6",
        f"Rastgele referans: {reverse_random:.3f}/6",
        f"Ters zaman sıralı CV: {reverse_folds} kat | {reverse_cv_mean:.3f}/6",
        "Not: CV, aynı ters holdout aralığının bölünmüş özetidir; bağımsız doğrulama değildir.",
        "Çekiliş başına sonuçlar (tahmin edilen sayılar gösterilmez):",
        *reverse_draws,
        "ÖNEMLİ: Ters yön daha sonraki tarihli verileri kullandığından yalnızca retrospektif stres testidir; ileriye dönük tahmin kanıtı değildir.",
    ]
    if branch:
        forward_lines.append("Dal: " + branch)
        reverse_lines.append("Dal: " + branch)
    if link:
        forward_lines.extend(["", link])
        reverse_lines.extend(["", link])
    return ["\n".join(forward_lines)[:3800], "\n".join(reverse_lines)[:3800]]



OFFLINE_TEMPLATE_FAMILIES = {
    "abc_persistent_membership": "A→B→C",
    "abc_disappearing_membership": "A→B→C",
    "abc_new_persistent_membership": "A→B→C",
    "abc_reappearance_membership": "A→B→C",
    "at_exact_historical_frequency": "A→T",
    "at_same_parity": "A→T",
    "at_same_final_digit": "A→T",
    "at_recent_exact_frequency_5": "A→T",
    "at_recent_exact_frequency_10": "A→T",
    "at_recent_exact_frequency_20": "A→T",
    "at_neighbourhood_radius_1": "A→T",
    "at_neighbourhood_radius_2": "A→T",
    "mod5_same_residue_frequency": "MOD5→T",
    "mod5_recent_same_residue_frequency_5": "MOD5→T",
    "mod5_recent_same_residue_frequency_10": "MOD5→T",
    "mod5_recent_same_residue_frequency_20": "MOD5→T",
}


def _offline_metric(value, field):
    return _safe_number(value, field)


def _validate_offline_candidate(candidate, training_count, development_count):
    from general_hypothesis_execution import validate_execution_spec
    from general_hypothesis_proposals import _render_expression, execution_spec_matches_proposal

    required = {
        "hypothesis_id", "family", "template_id", "proposal",
        "execution_spec", "specification_sha256", "training_signal",
        "synthetic_tests", "forward_walk_forward",
        "time_ordered_cross_validation", "reverse_retrospective_only",
    }
    if not isinstance(candidate, dict) or set(candidate) != required:
        raise ValueError("Offline candidate fields do not match the safe report schema.")
    hypothesis_id = candidate.get("hypothesis_id")
    import re
    if not isinstance(hypothesis_id, str) or not re.fullmatch(r"HYP-[0-9]{3}", hypothesis_id):
        raise ValueError("Offline hypothesis identifier is invalid.")
    family = candidate.get("family")
    template_id = candidate.get("template_id")
    if (
        template_id not in OFFLINE_TEMPLATE_FAMILIES
        or OFFLINE_TEMPLATE_FAMILIES[template_id] != family
    ):
        raise ValueError("Offline hypothesis family/template pairing is invalid.")

    spec = validate_execution_spec(candidate["execution_spec"])
    encoded = json.dumps(
        spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    spec_hash = hashlib.sha256(encoded).hexdigest()
    if candidate["specification_sha256"] != spec_hash:
        raise ValueError("Offline hypothesis specification hash is invalid.")

    proposal = candidate["proposal"]
    if (
        not isinstance(proposal, dict)
        or proposal.get("hypothesis_id") != hypothesis_id
        or proposal.get("proposed_family") != family
        or proposal.get("discovery_direction") != "forward"
        or proposal.get("status") != "PROPOSAL_ONLY"
        or proposal.get("execution_ready") is not False
        or proposal.get("registry_write_allowed") is not False
        or not execution_spec_matches_proposal(proposal, spec)
    ):
        raise ValueError("Offline proposal does not match the non-executable DSL candidate.")
    expected_definition = (
        "Formal DSL definition: score(candidate) = "
        + _render_expression(spec["score_expression"]) + "."
    )
    if proposal.get("exact_definition") != expected_definition:
        raise ValueError("Offline proposal definition is not the canonical rendered DSL expression.")

    training = candidate["training_signal"]
    if (
        not isinstance(training, dict)
        or set(training) != {
            "distinct_score_values", "score_range", "training_draw_count",
            "selection_source", "validation_data_used", "target_data_used",
        }
        or type(training.get("distinct_score_values")) is not int
        or not 2 <= training["distinct_score_values"] <= 60
        or type(training.get("score_range")) is not int
        or training["score_range"] <= 0
        or training.get("training_draw_count") != training_count
        or training.get("selection_source") != "training_prefix_only"
        or training.get("validation_data_used") is not False
        or training.get("target_data_used") is not False
    ):
        raise ValueError("Offline training-only discovery signal is invalid.")

    synthetic = candidate["synthetic_tests"]
    if (
        not isinstance(synthetic, dict)
        or synthetic.get("status") != "PASS"
        or synthetic.get("target_values_used") is not False
        or synthetic.get("real_archive_used") is not False
    ):
        raise ValueError("Offline deterministic DSL test evidence is invalid.")

    forward = candidate["forward_walk_forward"]
    cv = candidate["time_ordered_cross_validation"]
    reverse = candidate["reverse_retrospective_only"]
    if (
        not isinstance(forward, dict)
        or forward.get("predictive_claim") is not False
        or forward.get("validation_draw_count") != development_count
        or not isinstance(cv, dict)
        or cv.get("predictive_claim") is not False
        or cv.get("validation_draw_count") != development_count
        or cv.get("folds") != 5
        or not isinstance(reverse, dict)
        or reverse.get("status") != "EVALUATED_RETROSPECTIVE_ONLY"
        or reverse.get("predictive_evidence") is not False
    ):
        raise ValueError("Offline evaluation protocol/safety metadata is invalid.")

    forward_mean = _offline_metric(forward.get("mean_hits_out_of_6"), "forward walk-forward mean")
    forward_baseline = _offline_metric(
        forward.get("uniform_random_expected_hits_out_of_6"), "forward random baseline"
    )
    cv_mean = _offline_metric(cv.get("mean_hits_out_of_6"), "time-ordered CV mean")
    cv_baseline = _offline_metric(
        cv.get("uniform_random_expected_hits_out_of_6"), "CV random baseline"
    )
    reverse_mean = _offline_metric(reverse.get("mean_hits_out_of_6"), "reverse retrospective mean")
    reverse_baseline = _offline_metric(
        reverse.get("uniform_random_expected_hits_out_of_6"), "reverse random baseline"
    )
    if any(abs(value - 0.6) > 1e-6 for value in (
        forward_baseline, cv_baseline, reverse_baseline
    )):
        raise ValueError("Offline 6-of-60 baseline must equal 0.600/6.")

    expression = expected_definition[
        len("Formal DSL definition: score(candidate) = "):-1
    ]
    return {
        "hypothesis_id": hypothesis_id,
        "family": family,
        "template_id": template_id,
        "expression": expression,
        "distinct_score_values": training["distinct_score_values"],
        "score_range": training["score_range"],
        "forward_mean": forward_mean,
        "cv_mean": cv_mean,
        "reverse_mean": reverse_mean,
    }


def format_offline_research_messages(report, run_url="", branch="general-automation-state", title="Çevrimdışı matematiksel keşif"):
    """Format offline GENERAL discoveries without exposing draw or predicted-number values."""
    if not isinstance(report, dict) or report.get("status") != "COMPLETED_EXPLORATORY_ONLY":
        raise ValueError("A completed offline exploratory report is required.")
    for key in (
        "target_numbers_exposed", "target_or_future_rows_used",
        "sealed_holdout_used", "method_registry_modified",
        "automatic_candidate_selection",
    ):
        if report.get(key) is not False:
            raise ValueError("Offline report failed the " + key + " safety check.")
    if report.get("candidate_selection") != "NONE":
        raise ValueError("Offline report must not select a winning candidate.")
    target_date = report.get("target_date")
    try:
        target_day = date.fromisoformat(target_date)
    except (TypeError, ValueError):
        raise ValueError("Offline isolated target date is invalid.") from None
    if target_day.isoformat() != target_date:
        raise ValueError("Offline isolated target date is not canonical.")

    training_count = report.get("training_draw_count")
    development_count = report.get("development_draw_count")
    candidates = report.get("candidates")
    if (
        type(training_count) is not int or training_count < 40
        or type(development_count) is not int or development_count < 5
        or not isinstance(candidates, list)
        or not 1 <= len(candidates) <= 16
        or report.get("candidate_count") != len(candidates)
        or report.get("development_interval_is_confirmatory") is not False
    ):
        raise ValueError("Offline training/development counts or candidate list are invalid.")
    checked = [
        _validate_offline_candidate(item, training_count, development_count)
        for item in candidates
    ]
    ids = [item["hypothesis_id"] for item in checked]
    if len(ids) != len(set(ids)):
        raise ValueError("Offline report contains duplicate hypothesis identifiers.")

    common = [
        f"Hedef tarihi: {target_date} (çekiliş sayıları gizli)",
        f"Eğitim: {training_count} çekiliş | geliştirme: {development_count} yeni çekiliş",
        "Aday tanımları yalnızca eğitim ön ekiyle üretildi ve puan çeşitliliği açısından tarandı.",
        "İleri değerlendirme: walk-forward + 5 katlı zaman sıralı CV.",
        "Ters değerlendirme: yalnızca retrospektif stres testi; tahmin kanıtı değildir.",
        "Rastgele 6/60 referansı: 0.600/6.",
        "Otomatik kazanan/formül seçimi: YOK | Method registry değişikliği: YOK.",
        "Geliştirme aralığı keşif amaçlı tüketilmiştir; bağımsız doğrulama değildir.",
    ]
    link = f"GitHub Actions: {run_url}" if run_url else ""
    messages = [
        f"✅ GENERAL — ÇEVRİMDIŞI MATEMATİKSEL KEŞİF | {title}\n"
        + "\n".join(common)
        + f"\nGeçerli aday: {len(checked)} | Hedef izolasyonu: BAŞARILI"
        + (f"\nDal: {branch}" if branch else "")
        + (f"\n{link}" if link else "")
    ]
    for family in ("A→B→C", "A→T", "MOD5→T"):
        family_candidates = [item for item in checked if item["family"] == family]
        if not family_candidates:
            continue
        lines = [f"GENERAL — {family} | {len(family_candidates)} eğitim-temelli aday"]
        for item in family_candidates:
            lines.extend([
                f"{item['hypothesis_id']} · {item['template_id']}",
                "DSL: " + item["expression"],
                (
                    f"Eğitim sinyali: {item['distinct_score_values']} farklı skor "
                    f"(aralık {item['score_range']})"
                ),
                (
                    f"İleri walk-forward: {item['forward_mean']:.3f}/6 | "
                    f"5-kat CV: {item['cv_mean']:.3f}/6 | "
                    f"Ters retrospektif: {item['reverse_mean']:.3f}/6"
                ),
            ])
        lines.append("Bulgular deneysel özettir; formül onayı veya tahmin garantisi değildir.")
        if branch:
            lines.append("Dal: " + branch)
        if link:
            lines.append(link)
        message = "\n".join(lines)
        if len(message) > 3800:
            raise ValueError("Offline family report exceeds the Telegram safe message limit.")
        messages.append(message)
    return [message[:3800] for message in messages]



def format_message(title, status, run_url, branch, kind="status", report=None):
    basarili = status.lower() == "success"
    lines = [
        f"{'✅ BAŞARILI' if basarili else '❌ BAŞARISIZ'} — GENERAL: {title}",
        f"Sonuç: {'BAŞARILI' if basarili else 'BAŞARISIZ'}",
        f"Dal: {branch or 'bilinmiyor'}",
    ]
    if not basarili:
        lines.append("Hata ayrıntıları için aşağıdaki GitHub Actions çalıştırmasını aç.")
    elif kind == "candidates" and report:
        lines.append("İleriye dönük kronolojik test — deneysel yöntemler:")
        forward = report.get("forward_walk_forward", {})
        lines.extend(metric_lines(forward.get("baselines") if isinstance(forward, dict) else None))
        lines.append("Durum: Yalnızca deneysel; geçmişteki isabetler tahmin gücünü kanıtlamaz.")
        lines.append("Ters kronolojik sonuçlar yalnızca geriye dönük stres testidir; tahmin kanıtı değildir.")
    elif kind == "walkforward" and report:
        izolasyon = (
            report.get("current_target_excluded_from_scoring") is True
            and report.get("current_target_numbers_used_for_scoring") is False
            and report.get("current_target_numbers_exposed") is False
        )
        lines.append("Hedef çekiliş izolasyonu: " + ("BAŞARILI" if izolasyon else "DOĞRULANAMADI"))
        count = report.get("pre_target_history_records")
        if isinstance(count, int):
            lines.append(f"Hedef öncesi geçmiş kayıt sayısı: {count}")
        regimes = report.get("regimes", {})
        if not isinstance(regimes, dict):
            regimes = {}
        for regime in ("R60",):
            details = regimes.get(regime, {})
            lines.append(regime + " rejimi:")
            if not isinstance(details, dict):
                lines.append("- Rapor bilgisi yok.")
                continue
            pool_size = details.get("number_pool_size")
            random_expected = details.get("random_uniform_expected_hits_out_of_6")
            if (
                isinstance(pool_size, int)
                and not isinstance(pool_size, bool)
                and isinstance(random_expected, (int, float))
                and not isinstance(random_expected, bool)
            ):
                lines.append(
                    f"- Teorik rastgele referans: {random_expected:.3f}/6 "
                    f"(1–{pool_size} sayı havuzu; 36/{pool_size})."
                )
            if details.get("status") == "INSUFFICIENT_PRETARGET_HISTORY":
                lines.append(
                    "- Yetersiz hedef-öncesi geçmiş: "
                    + str(details.get("pre_target_history_records", 0))
                    + " kayıt; gerekli başlangıç penceresi "
                    + str(details.get("minimum_training_draws", "bilinmiyor"))
                    + "."
                )
                continue
            if details.get("status") != "EVALUATED":
                lines.append("- Değerlendirme durumu doğrulanamadı.")
                continue
            lines.append("- İleriye dönük temel yöntemler:")
            forward = details.get("forward_walk_forward", {})
            lines.extend(metric_lines(forward.get("baselines") if isinstance(forward, dict) else None))
            lines.append("- Zaman sıralı çapraz doğrulama:")
            cv = details.get("time_ordered_cross_validation", {})
            lines.extend(metric_lines(cv.get("baselines") if isinstance(cv, dict) else None))
        lines.append("Yalnızca 2 Ağustos 2020 ve sonrası çekilişleri değerlendirildi.")
        lines.append("A->B->C, A->T ve MOD5->T bu temel değerlendirme motorunda uygulanmış değil.")
        lines.append("Ters kronolojik sonuçlar geriye dönük incelemedir; tahmin kanıtı değildir.")
    if run_url:
        lines.extend(["", f"GitHub Actions ayrıntıları: {run_url}"])
    return "\n".join(lines)[:3800]


def _send_message(token, chat_id, message):
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": "true",
    }).encode()
    request = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/sendMessage",
        data=data,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception:
        raise SystemExit("Telegram raporu gönderilemedi: gönderim isteği tamamlanmadı.") from None
    if (
        not isinstance(result, dict)
        or result.get("ok") is not True
        or not isinstance(result.get("result"), dict)
        or not isinstance(result["result"].get("message_id"), int)
    ):
        raise SystemExit("Telegram raporu gönderilemedi: Telegram teslimatı doğrulamadı.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--run-url", default="")
    parser.add_argument("--branch", default="")
    parser.add_argument("--kind", choices=["status", "candidates", "walkforward", "research", "offline"], default="status")
    parser.add_argument("--report-log")
    args = parser.parse_args()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id or not chat_id.lstrip("-").isdigit():
        raise SystemExit("Telegram raporu gönderilemedi: gerekli secret eksik veya geçersiz.")
    report = read_report(args.report_log)
    if args.status.lower() == "success" and args.kind in ("candidates", "walkforward", "research", "offline") and report is None:
        raise SystemExit("Telegram raporu gönderilemedi: toplu değerlendirme raporu okunamadı.")
    if args.kind == "research" and args.status.lower() == "success":
        messages = format_research_messages(report, args.run_url, args.branch, args.title)
    elif args.kind == "offline" and args.status.lower() == "success":
        messages = format_offline_research_messages(report, args.run_url, args.branch, args.title)
    else:
        messages = [format_message(args.title, args.status, args.run_url, args.branch, args.kind, report)]
    for message in messages:
        _send_message(token, chat_id, message)
    print("TELEGRAM RAPOR TESLİMİ: BAŞARILI (" + str(len(messages)) + " mesaj)")


if __name__ == "__main__":
    main()
