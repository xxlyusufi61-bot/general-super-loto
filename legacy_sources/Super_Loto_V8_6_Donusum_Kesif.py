# -*- coding: utf-8 -*-
"""
SUPER LOTO V8.6
GERCEK DONUSUM / SIFRE MEKANIZMASI KESIF MOTORU

Amac:
- 02.08.2020 -> guncel Super Loto arsivini toplamak
- #108/#109'u egitimden tamamen cikarmak
- Ardışık cekilisler arasindaki A -> B donusumlerini incelemek
- Donusumlerin tekrar edip etmedigini bulmak
- Benzer donusum -> sonraki durum iliskisini bagimsiz walk-forward ile test etmek
- Kolon URETMEZ

Gereksinim: Python standart kutuphane + urllib
"""

import re
import sys
import time
import random
import urllib.request
from collections import Counter, defaultdict
from statistics import mean, median

BASE = "https://lotoistatistik.com/super-loto/super-loto-cekilis-arsivi-{}"
YEARS = range(2020, 2027)
START_DATE = (2020, 8, 2)
LOCKED_DATES = {(2026, 9, 8), (2026, 9, 10)}

# Gercek arsivde 2020 sistem degisimi sonrasi 02.08.2020 ile basliyoruz.
# Analiz agirligini azaltmak icin az sayida, dogrudan donusum ozelligi kullaniyoruz.
FEATURES = [
    "posdiff",
    "gapdiff",
    "rawdiff",
    "parityflip",
    "bandmove",
    "mod3diff",
    "mod5diff",
    "mod7diff",
    "sumdiff",
    "span_diff",
    "shape_diff",
    "signed_core",
]

TRANSFORMS = ["exact", "sign", "absolute"]

UA = "Mozilla/5.0 (Android; Mobile) AppleWebKit/537.36 Chrome/120 Safari/537.36"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="ignore")


def parse_year(html):
    # Once hyphenli, sonra bosluklu tarih+sayi yapilarini ara.
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"\s+", " ", text)

    out = set()

    # 02.08.2020 ... 6 sayi
    p1 = re.compile(
        r"(\d{2})[./-](\d{2})[./-](20\d{2}).{0,180}?"
        r"(\d{1,2})\s*[-]\s*(\d{1,2})\s*[-]\s*(\d{1,2})\s*[-]\s*"
        r"(\d{1,2})\s*[-]\s*(\d{1,2})\s*[-]\s*(\d{1,2})"
    )
    for m in p1.finditer(text):
        d, mo, y = map(int, m.group(1, 2, 3))
        nums = tuple(sorted(map(int, m.group(4, 5, 6, 7, 8, 9))))
        if valid(d, mo, y, nums):
            out.add(((y, mo, d), nums))

    # Tarih + sayilarin bosluk/tab ile ayrildigi varyant
    p2 = re.compile(
        r"(\d{2})[./-](\d{2})[./-](20\d{2}).{0,180}?"
        r"(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})\s+"
        r"(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})"
    )
    for m in p2.finditer(text):
        d, mo, y = map(int, m.group(1, 2, 3))
        nums = tuple(sorted(map(int, m.group(4, 5, 6, 7, 8, 9))))
        if valid(d, mo, y, nums):
            out.add(((y, mo, d), nums))

    return sorted(out)


def valid(d, mo, y, nums):
    if (y, mo, d) < START_DATE:
        return False
    if not (1 <= d <= 31 and 1 <= mo <= 12):
        return False
    if len(nums) != 6 or len(set(nums)) != 6:
        return False
    if not all(1 <= x <= 60 for x in nums):
        return False
    return True


def collect():
    all_rows = {}
    print("=" * 72)
    print("SUPER LOTO V8.6")
    print("GERCEK DONUSUM / SIFRE MEKANIZMASI KESIF MOTORU")
    print("=" * 72)
    print("Kaynak : lotoistatistik.com")
    print("HTTP   : urllib")
    print("Kolon  : HAYIR")
    print("Kilit  : #108 / #109")
    print()

    for year in YEARS:
        url = BASE.format(year)
        try:
            html = fetch(url)
            rows = parse_year(html)
            print(f"[{year}] bulunan: {len(rows):4d}")
            for date, nums in rows:
                all_rows[date] = nums
        except Exception as e:
            print(f"[{year}] HATA: {type(e).__name__}: {e}")

    rows = sorted((d, n) for d, n in all_rows.items())
    print()
    print("=" * 72)
    print("VERI OZETI")
    print("=" * 72)
    print("Toplam temiz cekilis:", len(rows))
    if rows:
        print("Ilk:", (1, rows[0][0], rows[0][1]))
        print("Son:", (len(rows), rows[-1][0], rows[-1][1]))

    # Kilitleri analizden cikart.
    analysis = [(d, n) for d, n in rows if d not in LOCKED_DATES]

    print()
    print("Kilitlenen cekilisler:")
    for d, n in rows:
        if d in LOCKED_DATES:
            print(d, n, "-> KILITLI")

    print("Analiz icin kullanilan cekilis:", len(analysis))
    return analysis


def gaps(x):
    return tuple(x[i + 1] - x[i] for i in range(5))


def parity(x):
    return tuple(v & 1 for v in x)


def bands(x):
    return tuple((v - 1) // 10 for v in x)


def shape(x):
    # 5 gap'in normalize edilmis buyuk/kucuk sirasi
    g = gaps(x)
    return tuple(1 if v >= median(g) else -1 for v in g)


def state_features(a, b):
    ga, gb = gaps(a), gaps(b)
    pa, pb = parity(a), parity(b)
    ba, bb = bands(a), bands(b)
    sa, sb = shape(a), shape(b)

    return {
        "posdiff": tuple(b[i] - a[i] for i in range(6)),
        "gapdiff": tuple(gb[i] - ga[i] for i in range(5)),
        "rawdiff": tuple((b[i] - a[i]) for i in range(6)),
        "parityflip": tuple(pb[i] - pa[i] for i in range(6)),
        "bandmove": tuple(bb[i] - ba[i] for i in range(6)),
        "mod3diff": tuple((b[i] % 3) - (a[i] % 3) for i in range(6)),
        "mod5diff": tuple((b[i] % 5) - (a[i] % 5) for i in range(6)),
        "mod7diff": tuple((b[i] % 7) - (a[i] % 7) for i in range(6)),
        "sumdiff": (sum(b) - sum(a),),
        "span_diff": ((b[-1] - b[0]) - (a[-1] - a[0]),),
        "shape_diff": tuple(sb[i] - sa[i] for i in range(5)),
        "signed_core": tuple(
            (b[i] - a[i]) + (gb[i - 1] - ga[i - 1] if i > 0 else 0)
            for i in range(6)
        ),
    }


def transform(v, mode):
    if mode == "exact":
        return tuple(v)
    if mode == "sign":
        return tuple(0 if x == 0 else (1 if x > 0 else -1) for x in v)
    return tuple(abs(x) for x in v)


def all_transitions(rows):
    # transition i = rows[i] -> rows[i+1]
    out = []
    for i in range(len(rows) - 1):
        a = rows[i][1]
        b = rows[i + 1][1]
        out.append({
            "i": i,
            "date_a": rows[i][0],
            "date_b": rows[i + 1][0],
            "a": a,
            "b": b,
            "f": state_features(a, b),
        })
    return out


def signature(t, feature, mode):
    return transform(t["f"][feature], mode)


def hamming(a, b):
    if len(a) != len(b):
        return 999
    return sum(x != y for x, y in zip(a, b))


def l1(a, b):
    if len(a) != len(b):
        return 999999
    return sum(abs(x - y) for x, y in zip(a, b))


def dist(a, b, mode):
    if mode == "exact":
        return 0 if a == b else 999
    return hamming(a, b) if mode == "sign" else l1(a, b)


def similarity_rank(target_sig, past, mode, k=5):
    scored = []
    for item in past:
        s = signature(item, CURRENT_FEATURE, mode)
        d = dist(target_sig, s, mode)
        scored.append((d, item))
    scored.sort(key=lambda z: z[0])
    return scored[:k]


CURRENT_FEATURE = None


def predict_next_from_transitions(target_transition, history, feature, mode, k):
    global CURRENT_FEATURE
    CURRENT_FEATURE = feature
    ts = signature(target_transition, feature, mode)
    neigh = similarity_rank(ts, history, mode, k)
    if not neigh:
        return None

    # Komşu dönüşümlerin hedefi olan B çekilislerini oyla.
    votes = Counter()
    for d, item in neigh:
        # Mesafe agirlikli oy
        w = 1.0 / (1.0 + d)
        for n in item["b"]:
            votes[n] += w

    return tuple(sorted(n for n, _ in votes.most_common(6)))


def score(pred, actual):
    if pred is None:
        return 0
    return len(set(pred) & set(actual))


def exact_recurrence(rows):
    trs = all_transitions(rows)
    counts = Counter()
    locations = defaultdict(list)
    for t in trs:
        for feature in FEATURES:
            for mode in TRANSFORMS:
                sig = (feature, mode, signature(t, feature, mode))
                counts[sig] += 1
                locations[sig].append(t["i"])
    return counts, locations


def discover_repeated_transformations(rows):
    print()
    print("=" * 72)
    print("1) TEKRAR EDEN DONUSUMLER")
    print("=" * 72)

    counts, locations = exact_recurrence(rows)
    ranked = sorted(counts.items(), key=lambda x: x[1], reverse=True)

    shown = 0
    for (feature, mode, sig), c in ranked:
        if c >= 4:
            print(f"{c:3d} tekrar | {feature:12s} | {mode:8s} | {sig}")
            shown += 1
            if shown >= 20:
                break

    if shown == 0:
        print("4+ kez ayni donusum bulunmadi.")

    return counts, locations


def evaluate_model(rows, feature, mode, k, start_idx, end_idx):
    """
    Gercek walk-forward:
    target transition i icin sadece transition < i kullanilir.
    Tahmin edilen sey, target transition'in B durumudur.
    """
    trs = all_transitions(rows)
    vals = []

    for i in range(start_idx, min(end_idx, len(trs))):
        if i < 1:
            continue

        target = trs[i]
        history = trs[max(0, i - 200):i]

        pred = predict_next_from_transitions(
            target, history, feature, mode, k
        )
        vals.append(score(pred, target["b"]))

    return mean(vals) if vals else 0.0, vals


def nearest_transformation_tests(rows):
    print()
    print("=" * 72)
    print("2) DONUSUM -> SONRAKI DURUM WALK-FORWARD")
    print("=" * 72)

    # Once makul sayida modeli hizli olarak test ediyoruz.
    candidates = []
    trs_n = len(rows) - 1
    cut = int(trs_n * 0.55)

    for feature in FEATURES:
        for mode in TRANSFORMS:
            for k in (1, 3, 5):
                # Hemen tum araligi degil, discovery bolumunu kullan.
                s, _ = evaluate_model(
                    rows, feature, mode, k, max(1, 80), cut
                )
                candidates.append((s, feature, mode, k))

    candidates.sort(reverse=True)
    print("DISCOVERY TOP 15")
    for j, (s, f, m, k) in enumerate(candidates[:15], 1):
        print(f"{j:2d}. {s:.3f} | {f} / {m} / K{k}")

    frozen = candidates[:10]
    print()
    print("FROZEN BAGIMSIZ TEST")
    print("Discovery -> Validation -> Final")

    dev_end = cut
    val_end = int(trs_n * 0.75)

    results = []
    for _, f, m, k in frozen:
        dev_s, _ = evaluate_model(rows, f, m, k, 80, dev_end)
        val_s, _ = evaluate_model(rows, f, m, k, dev_end, val_end)
        fin_s, _ = evaluate_model(rows, f, m, k, val_end, trs_n)
        all_s = (dev_s + val_s + fin_s) / 3.0
        results.append((val_s, fin_s, all_s, dev_s, f, m, k))

    results.sort(key=lambda x: (x[1], x[0]), reverse=True)

    for j, (v, fin, all_s, dev, f, m, k) in enumerate(results, 1):
        print(
            f"{j:2d}. DEV {dev:.3f} | VAL {v:.3f} | "
            f"FINAL {fin:.3f} | {f} / {m} / K{k}"
        )

    return results


def repeated_state_test(rows):
    print()
    print("=" * 72)
    print("3) AYNI DONUSUM -> AYNI/DEGISKEN SONRAKI DURUM TESTI")
    print("=" * 72)

    trs = all_transitions(rows)

    for feature in FEATURES:
        for mode in TRANSFORMS:
            groups = defaultdict(list)
            for t in trs:
                groups[signature(t, feature, mode)].append(t["b"])

            repeated = [v for v in groups.values() if len(v) >= 4]
            if not repeated:
                continue

            # Grup icindeki ortalama pairwise overlap.
            vals = []
            for g in repeated:
                if len(g) > 12:
                    g = g[:12]
                for i in range(len(g)):
                    for j in range(i + 1, len(g)):
                        vals.append(len(set(g[i]) & set(g[j])))

            if vals:
                print(
                    f"{feature:12s} {mode:8s} | "
                    f"grup={len(repeated):3d} | "
                    f"ortalama sonraki overlap={mean(vals):.3f}"
                )


def null_test(rows, model, reps=8):
    print()
    print("=" * 72)
    print("4) SHUFFLE / NULL KONTROLU")
    print("=" * 72)

    _, _, real, _, f, m, k = model
    print(f"Model: {f} / {m} / K{k}")
    print(f"Gercek FINAL: {real:.3f}")

    # Final araligini ayni tut; sadece gecmis transition'larin sirasi karistirilir.
    trs = all_transitions(rows)
    n = len(trs)
    val_end = int(n * 0.75)

    vals = []
    for r in range(reps):
        shuffled = trs[:val_end]
        shuffled = shuffled[:]
        random.shuffle(shuffled)

        # Kucuk, hizli null: final targetlar icin karistirilmis history.
        scores = []
        for i in range(val_end, n):
            target = trs[i]
            history = shuffled[-200:]
            pred = predict_next_from_transitions(target, history, f, m, k)
            scores.append(score(pred, target["b"]))
        vals.append(mean(scores) if scores else 0)

    print("Null:", " | ".join(f"{x:.3f}" for x in vals))
    print("Null ortalama:", f"{mean(vals):.3f}")
    print("Delta:", f"{real - mean(vals):+.3f}")


def main():
    rows = collect()
    if len(rows) < 300:
        print("YETERLI VERI YOK.")
        return

    # Kilitli son iki tarih zaten cikartildi.
    discover_repeated_transformations(rows)
    results = nearest_transformation_tests(rows)
    repeated_state_test(rows)

    if results:
        best = results[0]
        null_test(rows, best, reps=8)

    print()
    print("=" * 72)
    print("FINAL YAPISAL KARAR")
    print("=" * 72)

    if results:
        best = results[0]
        v, fin, all_s, dev, f, m, k = best
        print(f"En iyi frozen model : {f} / {m} / K{k}")
        print(f"Development         : {dev:.3f}")
        print(f"Validation          : {v:.3f}")
        print(f"Final               : {fin:.3f}")
        print()
        print("Bu surum KOLON URETMEZ.")
        print("Amac: A -> B donusumlerinin gercekten tekrar edip")
        print("sonraki durumu belirleyip belirlemedigini test etmektir.")
        print()
        print("Not: Final > null ve farkin farkli zaman bloklarinda")
        print("tekrar etmesi olmadan deterministik sifre iddiasi kurulmaz.")

    print()
    print(">>> V8.6 ANALIZI TAMAMLANDI.")


if __name__ == "__main__":
    main()
