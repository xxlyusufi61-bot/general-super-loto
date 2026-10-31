"""
GENERAL Super Loto - Data Guard
Version: 0.1.1

Gorevler:
- Arsiv kayitlarini dogrulamak.
- Tarih siralamasini ve tekrar eden tarihleri kontrol etmek.
- Gecersiz cekilis kayitlarini reddetmek.
- Hedef tarihinden onceki verileri egitim icin ayirmak.
- Hedef tarihindeki ve sonraki cekilislerin sayilarini
  sonuc raporlarina aktarmamak.

Beklenen kayit bicimi:
{
    "date": "YYYY-MM-DD",
    "numbers": [1, 12, 23, 34, 45, 56]
}

Bu modul veri indirmez, veri uretmez ve tahmin yapmaz.
"""

from datetime import date
from typing import Any, Iterable


GUARD_VERSION = "0.2.0"
RESEARCH_SCOPE_START = date(2020, 8, 2)
MIN_NUMBER = 1
MAX_NUMBER = 60
NUMBERS_PER_DRAW = 6


class DataGuardError(ValueError):
    """Arsiv veya hedef izolasyonu gecersiz oldugunda kullanilir."""


def parse_date(value: Any) -> date:
    """YYYY-MM-DD bicimindeki tarihi dogrular."""

    if not isinstance(value, str):
        raise DataGuardError(
            "Tarih YYYY-MM-DD biciminde metin olmalidir."
        )

    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise DataGuardError(
            "Gecersiz tarih bicimi."
        ) from exc

    if parsed.isoformat() != value:
        raise DataGuardError(
            "Tarih tam olarak YYYY-MM-DD biciminde olmalidir."
        )

    return parsed


def validate_numbers(value: Any) -> list[int]:
    """Bir cekilisin alti farkli sayisini dogrular."""

    if not isinstance(value, (list, tuple)):
        raise DataGuardError(
            "Cekilis sayilari liste veya demet olmalidir."
        )

    if len(value) != NUMBERS_PER_DRAW:
        raise DataGuardError(
            "Her cekiliste tam olarak 6 sayi bulunmalidir."
        )

    if any(type(number) is not int for number in value):
        raise DataGuardError(
            "Cekilis sayilari tam sayi olmalidir."
        )

    if any(
        number < MIN_NUMBER or number > MAX_NUMBER
        for number in value
    ):
        raise DataGuardError(
            "Cekilis sayilari 1 ile 60 arasinda olmalidir."
        )

    if len(set(value)) != NUMBERS_PER_DRAW:
        raise DataGuardError(
            "Bir cekiliste yinelenen sayi bulundu."
        )

    return list(value)


def validate_archive(
    rows: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    """
    Arsiv yapisini dogrular.

    Kayitlar tarih artan sirada olmalidir.
    Yinelenen tarihler reddedilir.
    Cekilis sayilari rapora yazdirilmaz.
    """

    if isinstance(rows, (str, bytes, dict)):
        raise DataGuardError(
            "Arsiv, kayit sozluklerinden olusmalidir."
        )

    try:
        records = list(rows)
    except TypeError as exc:
        raise DataGuardError(
            "Arsiv okunabilir bir kayit dizisi olmalidir."
        ) from exc

    if not records:
        raise DataGuardError(
            "Arsiv bos. Dogrulama yapilamadi."
        )

    seen_dates = set()
    previous_date = None
    first_date = None
    last_date = None

    for index, row in enumerate(records, start=1):
        if not isinstance(row, dict):
            raise DataGuardError(
                f"{index}. kayit sozluk biciminde degil."
            )

        if "date" not in row or "numbers" not in row:
            raise DataGuardError(
                f"{index}. kayitta gerekli alanlar eksik."
            )

        current_date = parse_date(row["date"])
        if current_date < RESEARCH_SCOPE_START:
            raise DataGuardError(
                "Archive contains a record before the active research scope."
            )
        numbers = validate_numbers(row["numbers"])
        if numbers != sorted(numbers):
            raise DataGuardError(
                "Draw numbers must be sorted ascending in the canonical archive."
            )

        if current_date in seen_dates:
            raise DataGuardError(
                "Yinelenen cekilis tarihi bulundu."
            )

        if (
            previous_date is not None
            and current_date <= previous_date
        ):
            raise DataGuardError(
                "Arsiv kronolojik sirada degil. "
                "Kayitlar eski tarihten yeni tarihe siralanmalidir."
            )

        seen_dates.add(current_date)
        previous_date = current_date

        if first_date is None:
            first_date = current_date

        last_date = current_date

    return {
        "guard_version": GUARD_VERSION,
        "valid": True,
        "record_count": len(records),
        "unique_dates": len(seen_dates),
        "first_date": first_date.isoformat(),
        "last_date": last_date.isoformat(),
        "draw_numbers_exposed": False,
        "research_scope_start_date": RESEARCH_SCOPE_START.isoformat(),
    }


def isolate_target(
    rows: Iterable[dict[str, Any]],
    target_date: str,
) -> dict[str, Any]:
    """
    Hedef cekilisi egitim verisinden ayirir.

    Egitim:
        Tarih < hedef tarihi

    Dislanan veriler:
        Tarih >= hedef tarihi

    Onemli:
    - Hedef ve sonraki cekilislerin sayilari sonuc nesnesine
      aktarilmaz.
    - Hedef sayilarini raporlamaz.
    - Gecersiz arsivde islemi durdurur.
    - Hedef tarihini kendiliginden belirlemez.
    """

    parsed_target = parse_date(target_date)

    try:
        records = list(rows)
    except TypeError as exc:
        raise DataGuardError(
            "Arsiv kayitlari okunamadi."
        ) from exc

    archive_summary = validate_archive(records)

    train = []
    excluded_dates = []
    target_present = False

    for row in records:
        current_date = parse_date(row["date"])

        if current_date < parsed_target:
            # Egitime yalnizca hedef oncesi kayitlar girer.
            train.append({
                "date": current_date.isoformat(),
                "numbers": validate_numbers(row["numbers"]),
            })
        else:
            # Hedef ve sonraki kayitlardan yalnizca tarih tutulur.
            # Cekilis sayilari bu listeye kesinlikle eklenmez.
            excluded_dates.append(current_date.isoformat())

        if current_date == parsed_target:
            target_present = True

    # Savunmaci kontroller: ihlal varsa islem durur.
    for row in train:
        if parse_date(row["date"]) >= parsed_target:
            raise DataGuardError(
                "IZOLASYON HATASI: Hedef veya sonrasi egitime girdi."
            )

    if len(train) + len(excluded_dates) != len(records):
        raise DataGuardError(
            "IZOLASYON HATASI: Kayit sayilari tutarsiz."
        )

    if any(
        parse_date(item) < parsed_target
        for item in excluded_dates
    ):
        raise DataGuardError(
            "IZOLASYON HATASI: Hedef oncesi kayit yanlis ayrildi."
        )

    return {
        "guard_version": GUARD_VERSION,
        "isolation_pass": True,
        "target_date": parsed_target.isoformat(),
        "target_present_in_archive": target_present,
        "archive_record_count": archive_summary["record_count"],
        "training_record_count": len(train),
        "excluded_record_count": len(excluded_dates),
        "training_last_date": (
            train[-1]["date"] if train else None
        ),
        "excluded_first_date": (
            excluded_dates[0] if excluded_dates else None
        ),
        "target_numbers_exposed": False,
        "train": train,
        "excluded_dates": excluded_dates,
    }


def safe_summary(result: dict[str, Any]) -> dict[str, Any]:
    """
    Guvenli durum raporu olusturur.

    Egitim kayitlarini veya cekilis sayilarini dondurmez.
    """

    allowed_keys = (
        "guard_version",
        "isolation_pass",
        "target_date",
        "target_present_in_archive",
        "archive_record_count",
        "training_record_count",
        "excluded_record_count",
        "training_last_date",
        "excluded_first_date",
        "target_numbers_exposed",
    )

    return {
        key: result.get(key)
        for key in allowed_keys
    }


def main() -> None:
    """Dosyanin dogrudan calistirilmasi durumunda bilgi verir."""

    print("GENERAL Data Guard", GUARD_VERSION)
    print("Durum: Modul yuklendi.")
    print("Arsiv verisi otomatik olarak yuklenmedi.")
    print("Henuz gercek arsiv dogrulamasi yapilmadi.")
    print("Henuz hedef izolasyonu testi yapilmadi.")
    print("Cekilis tahmini uretilmedi.")


if __name__ == "__main__":
    main()
