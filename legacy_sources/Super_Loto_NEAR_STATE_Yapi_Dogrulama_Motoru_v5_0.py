# -*- coding: utf-8 -*-
"""
SUPER LOTO NEAR-STATE YAPI DOGRULAMA MOTORU v5.0
================================================
Amaç:
  Sayısal "şifreleşme" iddiasını, geçmiş çekilişlerde kör / walk-forward
  testlerle araştırmak.

VERİ KURALI
-----------
- Gömülü arşiv: 263 çekiliş.
- Arşiv sonu: #107.
- #108 = (2, 12, 37, 52, 59, 60) ve #109 = (17, 28, 31, 44, 48, 53)
  sadece KİLİTLİ gelecek veri olarak tanımlıdır; aşağıdaki model eğitimine
  veya holdout skorlarına GİRMEZ.
- Model keşfi: #81..#213 (dizin mantığıyla 80..212)
- Bağımsız holdout: #214..#263
- Sonuçlar üzerinde "best-of-many" seçimi yapılmaz: modeller önce
  keşif bölümünde seçilir, sonra holdout'a bir kez uygulanır.

NEAR-STATE FİKRİ
----------------
Bir hedef çekiliş T için, yalnızca T'den önceki pencere içindeki çekilişler
arasından T'nin yapısına en çok benzeyen k komşuyu buluruz. Komşuların
hemen SONRAKİ çekilişlerindeki sayıları oylar ve hedef T için puanlarız.

Önemli:
  Bu bir "kesin şifre bulundu" motoru değildir. Amaç, benzer durumların
  sonraki durumla sistematik bağlantısı olup olmadığını ölçmektir.

Kullanım:
  Pydroid 3 / Python 3 ile doğrudan çalışır.
  Harici paket gerekmez (standart kütüphane).
"""

from collections import Counter, defaultdict
from math import sqrt
import random
import statistics

DRAWS = [(8, 36, 38, 42, 53, 56), (11, 15, 20, 29, 35, 56), (2, 21, 26, 43, 44, 55), (4, 7, 11, 13, 25, 48), (2, 4, 19, 21, 39, 53), (3, 4, 11, 42, 45, 56), (33, 38, 46, 50, 55, 58), (1, 15, 18, 19, 41, 52), (13, 30, 39, 42, 44, 46), (1, 4, 8, 10, 42, 55), (2, 3, 4, 15, 38, 51), (2, 3, 4, 9, 18, 19), (5, 21, 23, 41, 42, 58), (19, 20, 24, 25, 33, 53), (4, 8, 10, 20, 44, 57), (5, 11, 32, 34, 43, 53), (3, 5, 17, 26, 30, 49), (8, 24, 33, 45, 47, 51), (15, 23, 42, 46, 47, 48), (10, 13, 18, 19, 28, 30), (2, 25, 37, 44, 48, 54), (2, 3, 7, 23, 36, 43), (3, 9, 15, 37, 54, 58), (3, 4, 36, 40, 46, 58), (2, 13, 43, 48, 50, 53), (6, 17, 31, 43, 48, 55), (2, 14, 36, 37, 52, 59), (14, 23, 24, 26, 45, 47), (6, 13, 24, 26, 29, 38), (4, 6, 8, 27, 35, 43), (1, 2, 3, 19, 33, 39), (2, 19, 44, 47, 58, 59), (11, 35, 43, 46, 57, 60), (1, 20, 28, 42, 47, 49), (12, 14, 18, 36, 44, 50), (3, 17, 20, 29, 46, 50), (15, 20, 22, 41, 48, 51), (5, 25, 31, 43, 48, 56), (2, 22, 26, 31, 35, 50), (5, 21, 24, 26, 33, 57), (15, 18, 28, 53, 55, 60), (7, 13, 15, 21, 22, 24), (2, 3, 19, 30, 40, 55), (5, 25, 26, 29, 39, 47), (9, 15, 37, 43, 44, 57), (4, 6, 7, 18, 21, 38), (10, 12, 32, 36, 44, 58), (19, 27, 34, 40, 48, 54), (9, 21, 33, 37, 39, 56), (2, 23, 29, 45, 50, 59), (28, 32, 35, 47, 54, 58), (1, 26, 34, 35, 39, 45), (1, 4, 18, 26, 35, 44), (2, 16, 25, 30, 31, 47), (7, 32, 45, 50, 56, 58), (5, 19, 33, 37, 43, 47), (1, 10, 15, 21, 35, 55), (22, 28, 33, 37, 39, 52), (1, 13, 25, 44, 46, 60), (4, 15, 28, 50, 53, 59), (12, 27, 31, 34, 40, 55), (6, 8, 12, 17, 22, 58), (5, 18, 19, 53, 57, 58), (12, 16, 24, 32, 40, 55), (6, 12, 20, 22, 30, 47), (4, 12, 23, 48, 50, 51), (22, 32, 37, 38, 49, 50), (2, 14, 34, 41, 51, 57), (6, 8, 23, 27, 46, 60), (16, 19, 25, 37, 43, 57), (7, 22, 31, 39, 48, 56), (11, 12, 21, 52, 53, 54), (9, 25, 29, 31, 35, 46), (5, 19, 20, 35, 51, 52), (10, 18, 27, 28, 29, 30), (8, 12, 27, 36, 43, 53), (12, 13, 17, 20, 33, 45), (14, 23, 39, 40, 45, 49), (1, 13, 22, 38, 56, 57), (4, 14, 23, 27, 35, 57), (11, 16, 17, 19, 34, 36), (11, 25, 26, 43, 44, 57), (4, 6, 7, 12, 14, 32), (4, 12, 26, 28, 37, 43), (5, 6, 9, 24, 32, 54), (1, 8, 14, 19, 36, 60), (5, 10, 11, 18, 24, 46), (10, 13, 15, 27, 29, 46), (20, 26, 28, 43, 47, 56), (3, 14, 19, 27, 34, 57), (7, 27, 41, 47, 51, 60), (2, 11, 13, 17, 19, 60), (15, 25, 34, 40, 46, 47), (7, 31, 35, 39, 40, 44), (2, 30, 36, 39, 40, 49), (8, 17, 18, 39, 52, 59), (4, 7, 30, 39, 44, 58), (7, 10, 23, 41, 52, 57), (25, 29, 31, 34, 37, 52), (9, 10, 11, 15, 50, 52), (12, 19, 27, 40, 49, 57), (6, 23, 26, 43, 59, 60), (3, 8, 17, 53, 55, 56), (2, 27, 32, 42, 46, 57), (25, 27, 30, 31, 44, 45), (15, 23, 24, 25, 47, 57), (11, 22, 34, 43, 53, 59), (28, 48, 57, 58, 59, 60), (5, 6, 8, 20, 23, 43), (5, 38, 40, 50, 56, 60), (23, 24, 31, 35, 43, 59), (2, 5, 42, 55, 56, 58), (4, 5, 23, 42, 48, 51), (1, 9, 19, 34, 39, 50), (1, 7, 10, 26, 33, 35), (10, 14, 31, 35, 55, 57), (15, 19, 23, 25, 38, 53), (2, 14, 21, 25, 42, 60), (1, 3, 9, 25, 37, 44), (1, 8, 16, 20, 22, 41), (4, 8, 15, 30, 43, 47), (2, 33, 41, 54, 58, 60), (3, 23, 28, 39, 44, 57), (1, 20, 22, 39, 52, 57), (13, 20, 32, 35, 37, 41), (2, 3, 11, 25, 51, 52), (14, 23, 36, 45, 47, 48), (18, 22, 26, 44, 53, 55), (10, 22, 39, 41, 48, 49), (1, 6, 35, 54, 56, 59), (15, 17, 20, 39, 44, 47), (10, 11, 26, 46, 54, 60), (2, 16, 20, 31, 39, 55), (18, 26, 34, 48, 49, 56), (7, 12, 14, 15, 17, 40), (9, 13, 18, 25, 29, 58), (6, 9, 16, 20, 26, 46), (1, 9, 16, 30, 45, 54), (2, 27, 34, 37, 48, 60), (12, 19, 41, 51, 54, 59), (2, 15, 28, 39, 48, 50), (5, 7, 8, 28, 47, 58), (9, 12, 22, 34, 43, 56), (2, 9, 16, 19, 29, 52), (9, 11, 18, 35, 49, 50), (19, 33, 37, 39, 56, 57), (17, 28, 32, 36, 38, 51), (1, 8, 29, 39, 41, 50), (2, 9, 27, 30, 36, 55), (5, 9, 32, 35, 55, 56), (4, 7, 31, 35, 40, 52), (9, 11, 12, 21, 32, 34), (13, 20, 33, 35, 40, 48), (2, 7, 19, 21, 23, 59), (6, 31, 35, 41, 44, 46), (6, 34, 35, 36, 56, 57), (34, 36, 39, 45, 53, 55), (20, 21, 23, 26, 32, 37), (6, 9, 25, 29, 45, 57), (3, 7, 12, 22, 23, 55), (3, 8, 10, 12, 32, 42), (7, 19, 23, 32, 48, 55), (1, 22, 27, 41, 44, 46), (9, 24, 25, 33, 44, 52), (6, 26, 32, 43, 48, 54), (1, 2, 36, 42, 49, 55), (12, 20, 23, 38, 49, 50), (1, 7, 16, 34, 38, 42), (1, 4, 7, 8, 11, 41), (7, 17, 21, 25, 32, 52), (21, 33, 43, 47, 55, 59), (14, 24, 34, 42, 51, 60), (8, 18, 21, 29, 36, 38), (9, 29, 36, 46, 47, 52), (3, 5, 10, 22, 23, 52), (9, 16, 31, 34, 40, 58), (2, 14, 31, 38, 47, 54), (6, 8, 43, 49, 58, 60), (14, 16, 30, 42, 46, 55), (6, 19, 31, 39, 47, 51), (3, 19, 26, 37, 44, 57), (3, 14, 27, 32, 43, 44), (17, 18, 21, 27, 37, 51), (3, 11, 16, 25, 37, 41), (11, 41, 43, 44, 51, 57), (7, 9, 14, 36, 52, 55), (3, 22, 38, 41, 44, 50), (5, 12, 21, 28, 35, 42), (1, 6, 7, 19, 31, 44), (18, 21, 34, 43, 47, 50), (8, 9, 20, 30, 33, 55), (7, 9, 20, 22, 28, 39), (7, 14, 17, 25, 42, 49), (1, 17, 28, 30, 32, 42), (2, 16, 35, 51, 52, 56), (6, 11, 22, 36, 37, 53), (1, 3, 14, 19, 48, 56), (10, 29, 36, 42, 46, 52), (6, 18, 33, 40, 50, 58), (11, 19, 23, 27, 28, 39), (13, 19, 21, 30, 42, 43), (15, 20, 29, 51, 56, 57), (1, 11, 26, 49, 55, 59), (8, 31, 35, 36, 49, 55), (9, 26, 30, 39, 41, 45), (6, 36, 38, 44, 49, 53), (2, 14, 22, 34, 48, 59), (25, 35, 39, 41, 54, 56), (1, 13, 19, 27, 42, 56), (14, 20, 31, 35, 38, 39), (1, 11, 20, 33, 35, 47), (1, 8, 31, 38, 40, 56), (2, 19, 28, 30, 31, 37), (13, 18, 35, 45, 46, 50), (4, 9, 11, 19, 24, 26), (3, 23, 27, 47, 50, 53), (7, 21, 23, 25, 37, 51), (19, 22, 28, 30, 44, 46), (9, 17, 38, 47, 54, 58), (12, 21, 23, 45, 49, 55), (2, 4, 13, 16, 48, 58), (15, 18, 20, 31, 43, 56), (4, 6, 7, 10, 20, 28), (2, 8, 21, 24, 36, 47), (10, 11, 35, 38, 41, 57), (15, 19, 30, 38, 57, 60), (3, 26, 27, 32, 44, 46), (8, 14, 23, 32, 58, 60), (3, 27, 29, 33, 34, 58), (5, 6, 24, 37, 43, 48), (4, 29, 30, 31, 37, 39), (5, 11, 31, 46, 51, 56), (3, 6, 12, 14, 23, 47), (6, 8, 15, 18, 19, 29), (6, 32, 37, 38, 40, 57), (40, 41, 46, 52, 57, 60), (4, 26, 30, 38, 51, 53), (3, 16, 35, 44, 50, 57), (14, 15, 20, 30, 36, 44), (7, 8, 13, 27, 37, 55), (6, 26, 30, 33, 38, 46), (11, 32, 33, 47, 53, 59), (2, 13, 30, 52, 58, 60), (2, 11, 43, 47, 48, 58), (6, 9, 22, 27, 41, 46), (3, 6, 24, 50, 51, 53), (1, 25, 28, 41, 55, 56), (1, 4, 7, 16, 30, 60), (16, 23, 24, 42, 46, 57), (24, 27, 38, 41, 49, 50), (10, 27, 31, 32, 39, 54), (7, 16, 22, 39, 40, 60), (18, 24, 32, 34, 52, 60), (8, 20, 38, 45, 49, 50), (16, 28, 45, 49, 52, 54), (8, 21, 25, 46, 48, 57), (13, 25, 26, 34, 35, 38), (13, 19, 29, 31, 34, 53), (15, 26, 27, 41, 49, 52), (6, 16, 25, 30, 33, 46), (28, 30, 40, 42, 47, 59), (3, 15, 20, 23, 30, 60), (27, 30, 31, 41, 43, 57)]

LOCKED_108 = (2, 12, 37, 52, 59, 60)
LOCKED_109 = (17, 28, 31, 44, 48, 53)

WINDOWS = (20, 30, 40, 60, 80, 100)
KS = (1, 3, 5, 10)
FEATURES = (
    "raw", "sq", "gap", "gapdiff", "col",
    "parity", "band", "end", "shape", "sumspan"
)

DISC_START = 80       # #81
DISC_END = 213        # inclusive, 1-based draw number
HOLD_START = 213      # zero-based index => #214
HOLD_END = 263        # exclusive
NULL_REPS = 12
SEED = 20260911


def norm(draw):
    return tuple(sorted(int(x) for x in draw))


def gaps(d):
    d = norm(d)
    return tuple(d[i+1] - d[i] for i in range(5))


def gapdiff(d):
    g = gaps(d)
    return tuple(g[i+1] - g[i] for i in range(4))


def cols(d):
    return tuple((x - 1) % 10 for x in norm(d))


def parity(d):
    return tuple(x % 2 for x in norm(d))


def bands(d):
    return tuple((x - 1) // 10 for x in norm(d))


def ends(d):
    return tuple(x % 10 for x in norm(d))


def shape(d):
    # Adjacent-gap sign shape + equality-free coarse structure.
    g = gaps(d)
    return tuple(
        1 if g[i+1] > g[i] else (-1 if g[i+1] < g[i] else 0)
        for i in range(4)
    )


def sumspan(d):
    d = norm(d)
    return (sum(d), d[-1] - d[0])


def feature(d, kind):
    if kind == "raw":
        return norm(d)
    if kind == "sq":
        return tuple(x*x for x in norm(d))
    if kind == "gap":
        return gaps(d)
    if kind == "gapdiff":
        return gapdiff(d)
    if kind == "col":
        return cols(d)
    if kind == "parity":
        return parity(d)
    if kind == "band":
        return bands(d)
    if kind == "end":
        return ends(d)
    if kind == "shape":
        return shape(d)
    if kind == "sumspan":
        return sumspan(d)
    raise ValueError(kind)


def vec_distance(a, b, kind):
    fa = feature(a, kind)
    fb = feature(b, kind)

    # Feature-specific scaling prevents raw numeric dimensions from
    # automatically dominating the comparison.
    if kind in ("raw", "sq"):
        scale = 59.0 if kind == "raw" else 59.0 * 59.0
        return sum(abs(x-y) / scale for x, y in zip(fa, fb))
    if kind == "gap":
        return sum(abs(x-y) / 59.0 for x, y in zip(fa, fb))
    if kind == "gapdiff":
        return sum(abs(x-y) / 59.0 for x, y in zip(fa, fb))
    if kind == "col":
        return sum(min(abs(x-y), 10-abs(x-y)) / 5.0 for x, y in zip(fa, fb))
    if kind == "parity":
        return sum(x != y for x, y in zip(fa, fb))
    if kind == "band":
        return sum(abs(x-y) / 5.0 for x, y in zip(fa, fb))
    if kind == "end":
        return sum(min(abs(x-y), 10-abs(x-y)) / 5.0 for x, y in zip(fa, fb))
    if kind == "shape":
        return sum(x != y for x, y in zip(fa, fb))
    if kind == "sumspan":
        return abs(fa[0]-fb[0]) / 354.0 + abs(fa[1]-fb[1]) / 59.0
    raise ValueError(kind)


def nearest_indices(history, target, window, kind, exclude_recent=0):
    """
    history = draws strictly before target.
    exclude_recent can be used as a guard so a neighbor cannot be the
    immediately preceding draw when desired.
    """
    end = len(history) - exclude_recent
    start = max(0, end - window)
    candidates = range(start, end)

    scored = []
    for i in candidates:
        # Need a NEXT draw to evaluate the transfer.
        if i + 1 >= len(history):
            continue
        dist = vec_distance(history[i], target, kind)
        scored.append((dist, i))
    scored.sort(key=lambda z: (z[0], z[1]))
    return scored


def predict_by_neighbors(history, target, window, k, kind, mode="vote"):
    scored = nearest_indices(history, target, window, kind)
    if not scored:
        return tuple(), []

    chosen = scored[:min(k, len(scored))]
    weights = Counter()

    for rank, (dist, idx) in enumerate(chosen):
        nxt = history[idx + 1]
        if mode == "vote":
            w = 1.0
        elif mode == "rank":
            w = 1.0 / (rank + 1)
        elif mode == "plain":
            w = 1.0 / (1.0 + dist)
        else:
            raise ValueError(mode)

        for n in nxt:
            weights[n] += w

    pred = tuple(n for n, _ in sorted(weights.items(),
                                       key=lambda z: (-z[1], z[0]))[:6])
    return pred, chosen


def hit_count(pred, actual):
    return len(set(pred) & set(actual))


def evaluate_model(draws, start_idx, end_idx, window, k, kind, mode):
    hits = []
    details = []
    for t in range(start_idx, end_idx):
        history = draws[:t]
        target = draws[t]
        pred, chosen = predict_by_neighbors(
            history, target, window, k, kind, mode
        )
        h = hit_count(pred, target)
        hits.append(h)
        details.append((t+1, pred, target, chosen, h))
    return hits, details


def summary(hits):
    if not hits:
        return (0.0, 0.0, 0.0, 0.0, 0)
    return (
        statistics.mean(hits),
        sum(h >= 2 for h in hits) / len(hits),
        sum(h >= 3 for h in hits) / len(hits),
        max(hits),
        len(hits),
    )


def shuffle_draw_order(draws, seed, block=None):
    rng = random.Random(seed)
    out = list(draws)
    if block is None:
        rng.shuffle(out)
        return out

    # Block shuffle: preserve local order inside blocks but shuffle blocks.
    blocks = [out[i:i+block] for i in range(0, len(out), block)]
    rng.shuffle(blocks)
    return [d for b in blocks for d in b]


def model_key(window, k, kind, mode):
    return (window, k, kind, mode)


def fmt_result(name, s):
    avg, p2, p3, mx, n = s
    return f"{name:<30} avg={avg:.3f} 2+={p2*100:5.1f}% 3+={p3*100:5.1f}% max={mx} n={n}"


def rank_models(draws, start_idx, end_idx, top_n=30):
    rows = []
    for kind in FEATURES:
        for window in WINDOWS:
            for k in KS:
                for mode in ("vote", "rank", "plain"):
                    hits, _ = evaluate_model(
                        draws, start_idx, end_idx, window, k, kind, mode
                    )
                    s = summary(hits)
                    rows.append((s[0], s[1], s[2], s[3],
                                 model_key(window, k, kind, mode)))
    rows.sort(key=lambda r: (r[0], r[1], r[2], r[3]), reverse=True)
    return rows[:top_n]


def holdout_scores(draws, models):
    rows = []
    for _, _, _, _, key in models:
        window, k, kind, mode = key
        hits, details = evaluate_model(
            draws, HOLD_START, HOLD_END, window, k, kind, mode
        )
        rows.append((summary(hits), key, details))
    rows.sort(key=lambda r: (r[0][0], r[0][1], r[0][2], r[0][3]),
              reverse=True)
    return rows


def null_delta(draws, key, reps=NULL_REPS, block=None):
    window, k, kind, mode = key
    real_hits, _ = evaluate_model(
        draws, HOLD_START, HOLD_END, window, k, kind, mode
    )
    real_avg = statistics.mean(real_hits)

    vals = []
    for r in range(reps):
        sh = shuffle_draw_order(
            draws, SEED + r + (10000 if block else 0), block=block
        )
        h, _ = evaluate_model(
            sh, HOLD_START, HOLD_END, window, k, kind, mode
        )
        vals.append(statistics.mean(h))

    return real_avg, statistics.mean(vals), real_avg - statistics.mean(vals), vals


def repeated_feature_states(draws, kind):
    c = Counter(feature(d, kind) for d in draws)
    repeated = sum(v > 1 for v in c.values())
    maxfreq = max(c.values()) if c else 0
    return repeated, maxfreq


def recurrence_analysis(draws, kind, window=60):
    """
    Measures whether feature states recur and whether their NEXT draw has
    unusually high overlap with the later occurrence's next draw.
    This is descriptive, not a predictive score.
    """
    states = defaultdict(list)
    for i, d in enumerate(draws):
        states[feature(d, kind)].append(i)

    overlaps = []
    distances = []
    for idxs in states.values():
        if len(idxs) < 2:
            continue
        for a, b in zip(idxs, idxs[1:]):
            if a + 1 >= len(draws) or b + 1 >= len(draws):
                continue
            if b - a > window:
                continue
            overlaps.append(hit_count(draws[a+1], draws[b+1]))
            distances.append(b-a)

    if not overlaps:
        return 0.0, 0.0, 0
    return statistics.mean(overlaps), statistics.mean(distances), len(overlaps)


def print_top(title, rows, n=20):
    print("\n" + "="*80)
    print(title)
    print("="*80)
    for i, row in enumerate(rows[:n], 1):
        if len(row) == 5 and isinstance(row[0], float):
            avg, p2, p3, mx, key = row
            print(
                f"{i:2d} {key} -> avg={avg:.3f} "
                f"2+={p2*100:.1f}% 3+={p3*100:.1f}% max={mx}"
            )
        else:
            s, key, _ = row
            print(
                f"{i:2d} {key} -> avg={s[0]:.3f} "
                f"2+={s[1]*100:.1f}% 3+={s[2]*100:.1f}% max={s[3]}"
            )


def main():
    print("="*80)
    print("SUPER LOTO NEAR-STATE YAPI DOGRULAMA MOTORU v5.0")
    print("="*80)
    print(f"Arşiv: {len(DRAWS)} çekiliş | #108/#109 KİLİTLİ")
    print("Arşiv sonu: #107")
    print("Keşif: #81-#213 | Bağımsız holdout: #214-#263")
    print(f"Özellik={len(FEATURES)} pencere={len(WINDOWS)} "
          f"k={len(KS)} mod={3}")
    print("")

    # 1) Development scan
    dev = rank_models(DRAWS, DISC_START, DISC_END, top_n=30)
    print_top("1) GELİŞTİRME / YAPI TARAMASI - TOP 30", dev, 30)

    # 2) Freeze top 30, then independent holdout
    hold = holdout_scores(DRAWS, dev)
    print_top("2) DONDURULMUŞ MODELLER - BAĞIMSIZ HOLDOUT", hold, 30)

    # 3) Null tests for the strongest holdout models
    print("\n" + "="*80)
    print("3) NULL / SHUFFLE KONTROLÜ - EN GÜÇLÜ 8")
    print("="*80)

    survivors = []
    for s, key, details in hold[:8]:
        real, full, d_full, _ = null_delta(DRAWS, key, reps=NULL_REPS)
        real2, block, d_block, _ = null_delta(
            DRAWS, key, reps=NULL_REPS, block=10
        )
        print(
            f"{key} | REAL={real:.3f} | FULL={full:.3f} "
            f"Δ={d_full:+.3f} | BLOCK={block:.3f} Δ={d_block:+.3f}"
        )

        # Conservative exploratory filter. It does NOT declare a cipher.
        if d_full > 0.03 and d_block > 0.03 and s[0] >= 0.65:
            survivors.append((s, key, details, d_full, d_block))

    # 4) Holdout block profile
    print("\n" + "="*80)
    print("4) HOLDOUT BLOK PROFİLİ - EN GÜÇLÜ 8")
    print("="*80)
    block_edges = [(213, 223), (223, 233), (233, 243), (243, 253), (253, 263)]
    for s, key, details in hold[:8]:
        avgs = []
        window, k, kind, mode = key
        for a, b in block_edges:
            h, _ = evaluate_model(DRAWS, a, b, window, k, kind, mode)
            avgs.append(statistics.mean(h) if h else 0.0)
        loo_min = min(avgs) if avgs else 0.0
        print(f"{key} -> " + " ".join(f"{x:.2f}" for x in avgs)
              + f" | LOO-min={loo_min:.3f}")

    # 5) k contribution for the best feature/window/mode families
    print("\n" + "="*80)
    print("5) k KATKI TESTİ")
    print("="*80)
    seen_family = set()
    for s, key, details in hold[:10]:
        window, k, kind, mode = key
        fam = (window, kind, mode)
        if fam in seen_family:
            continue
        seen_family.add(fam)
        parts = []
        for kk in KS:
            h, _ = evaluate_model(DRAWS, HOLD_START, HOLD_END,
                                  window, kk, kind, mode)
            parts.append((kk, statistics.mean(h)))
        print(f"family={fam} -> " + " ".join(
            f"k{kk}={avg:.3f}" for kk, avg in parts
        ))

    # 6) Recurrence / state repetition
    print("\n" + "="*80)
    print("6) TEKRAR EDEN DURUM / RECURRENCE ANALİZİ")
    print("="*80)
    for kind in FEATURES:
        rep, mx = repeated_feature_states(DRAWS, kind)
        ov, md, cnt = recurrence_analysis(DRAWS, kind)
        print(
            f"{kind:<8} repeated={rep:4d} maxfreq={mx:3d} "
            f"next-overlap={ov:.3f} pairs={cnt:4d} avg_gap={md:.2f}"
        )

    # 7) Decision
    print("\n" + "="*80)
    print("7) OTOMATİK SINIFLANDIRMA")
    print("="*80)

    if survivors:
        print(f"İncelenmeye değer bağımsız yapı sayısı: {len(survivors)}")
        for s, key, _, df, db in survivors[:5]:
            print(
                f"  {key} | holdout avg={s[0]:.3f} "
                f"fullΔ={df:+.3f} blockΔ={db:+.3f}"
            )
        print(
            "SONUÇ: Bazı yapılar shuffle kontrollerinden ayrışıyor. "
            "Bu, şifre bulunduğu anlamına gelmez; bir sonraki aşama "
            "mekanizma/transfer doğrulamasıdır."
        )
    else:
        print(
            "SONUÇ: Bağımsız holdout + null koşullarını birlikte geçen "
            "yeterli yapı yok. Yeni bir şifreleşme ailesi denenmeli."
        )

    print("\n#109 KİLİTLİ TEST NOTU")
    print("Bu v5.0 çalıştırması #109'u eğitimde KULLANMAZ.")
    print("Kilitleme: #108 ve #109 arşive eklenmemiştir.")
    print("Yeni veri geldikçe ayrıca tek-seferlik kör test yapılmalıdır.")


if __name__ == "__main__":
    main()
