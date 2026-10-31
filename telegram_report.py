"""GitHub Actions test ve değerlendirme raporlarını Türkçe olarak Telegram'a gönderir."""
import argparse
import json
import os
import urllib.parse
import urllib.request
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
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
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
        for regime in ("R54", "R60"):
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
        lines.append("R54 ve R60 ayrı değerlendirildi; rejimler arasında veri havuzlama yapılmadı.")
        lines.append("A->B->C, A->T ve MOD5->T bu temel değerlendirme motorunda uygulanmış değil.")
        lines.append("Ters kronolojik sonuçlar geriye dönük incelemedir; tahmin kanıtı değildir.")
    if run_url:
        lines.extend(["", f"GitHub Actions ayrıntıları: {run_url}"])
    return "\n".join(lines)[:3800]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--run-url", default="")
    parser.add_argument("--branch", default="")
    parser.add_argument("--kind", choices=["status", "candidates", "walkforward"], default="status")
    parser.add_argument("--report-log")
    args = parser.parse_args()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id or not chat_id.lstrip("-").isdigit():
        raise SystemExit("Telegram raporu gönderilemedi: gerekli secret eksik veya geçersiz.")
    report = read_report(args.report_log)
    if args.status.lower() == "success" and args.kind in ("candidates", "walkforward") and report is None:
        raise SystemExit("Telegram raporu gönderilemedi: toplu değerlendirme raporu okunamadı.")
    message = format_message(args.title, args.status, args.run_url, args.branch, args.kind, report)
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
    print("TELEGRAM RAPOR TESLİMİ: BAŞARILI")


if __name__ == "__main__":
    main()
