# GENERAL Offline Formula Hypothesis Discovery — 2026-10-26

## Run provenance

- Workflow run (push, successful): https://github.com/xxlyusufi61-bot/general-super-loto/actions/runs/40245167285
- Workflow run (pull request, successful): https://github.com/xxlyusufi61-bot/general-super-loto/actions/runs/40245170619
- Pull request: https://github.com/xxlyusufi61-bot/general-super-loto/pull/28
- Report generation time (UTC): 2026-10-25T23:32:54.807477+00:00
- Runner: \`general_formula_discovery.py\` version \`0.1.0\`
- Parameters: 40 random six-number baselines per target, 250 block-bootstrap replicates, block length 20, seed 20261026, minimum history 20 draws.
- Archive: 1,007 valid records, 2020-01-02 through 2026-10-25.
- Isolated target: 2026-10-25. Isolation checks passed. Target values and later records were not used in scoring, selection, or tuning.
- Pre-target training archive: 1,006 records through 2026-10-22.
- The run made zero AI/provider API calls; no NVIDIA request, paid service or live AI inference was used.
- Search space: 17 predeclared candidates across six families: recent frequency, exponential-decay frequency, last-seen gap, profile neighbors, sorted-gap neighbors and sorted-position neighbors.
- R54 and R60 were evaluated separately. R54 contains 31 pre-target records and only 11 eligible evaluation targets; R60 contains 975 pre-target records.
- R54 confidence intervals are intentionally not reported because 11 targets are insufficient for the configured block-bootstrap protocol. The earlier degenerate point intervals were rejected and corrected.
- R60 uncertainty intervals use an approximate circular moving-block bootstrap. These are exploratory intervals, not proof of predictive ability.
- Multiple-testing correction was not applied. No binding rules were changed, the canonical registry remains unchanged, and no candidate was promoted.

## Mean candidate-minus-random hits per target

Positive values mean the candidate’s top-six set averaged more overlap with the held-out draw than the randomized six-number baseline. Negative values mean less overlap. The reverse column is a retrospective stress test only, not forward predictive evidence. Values below are rounded to four decimals.

| Candidate | R54 forward | R54 reverse | R54 CV | R60 forward | R60 reverse | R60 CV |
|---|---:|---:|---:|---:|---:|---:|
| recent_frequency_w20 | -0.1614 | +0.1432 | -0.1205 | -0.0178 | +0.0024 | -0.0125 |
| recent_frequency_w50 | -0.0705 | -0.1295 | -0.0295 | +0.0356 | +0.0338 | +0.0085 |
| recent_frequency_w100 | -0.0705 | -0.1295 | -0.0295 | +0.0031 | +0.0150 | +0.0085 |
| recent_frequency_w200 | -0.0705 | -0.1295 | -0.0295 | +0.0094 | +0.0098 | +0.0053 |
| decay_frequency_hl20 | -0.0705 | +0.3250 | -0.0295 | +0.0324 | +0.0307 | -0.0188 |
| decay_frequency_hl50 | -0.0705 | +0.3250 | -0.0295 | +0.0324 | +0.0338 | -0.0062 |
| decay_frequency_hl100 | -0.0705 | +0.3250 | -0.0295 | -0.0011 | +0.0171 | +0.0011 |
| last_seen_gap | +0.0205 | -0.2205 | +0.0614 | -0.0147 | -0.0049 | +0.0032 |
| profile_neighbors_k5 | -0.1614 | -0.1295 | -0.0295 | -0.0189 | -0.0300 | -0.0261 |
| profile_neighbors_k10 | -0.2523 | -0.1295 | -0.1205 | -0.0220 | -0.0300 | -0.0271 |
| profile_neighbors_k20 | -0.2523 | -0.1295 | -0.1205 | -0.0241 | -0.0143 | -0.0271 |
| gap_neighbors_k5 | -0.4341 | -0.0386 | -0.1205 | +0.0314 | +0.0077 | -0.0167 |
| gap_neighbors_k10 | -0.4341 | -0.0386 | -0.1205 | +0.0356 | +0.0056 | -0.0198 |
| gap_neighbors_k20 | -0.3432 | -0.1295 | -0.1205 | +0.0335 | +0.0129 | -0.0198 |
| position_neighbors_k5 | -0.1614 | -0.4023 | -0.0295 | +0.0073 | +0.0014 | -0.0470 |
| position_neighbors_k10 | -0.1614 | -0.4023 | -0.0295 | +0.0094 | -0.0049 | -0.0470 |
| position_neighbors_k20 | -0.0705 | -0.4023 | +0.0614 | -0.0011 | -0.0028 | -0.0470 |

## Confidence-interval interpretation for R60

- All R60 forward walk-forward and reverse stress-test bootstrap intervals include zero.
- In time-ordered cross-validation, the intervals for \`position_neighbors_k5\`, \`position_neighbors_k10\` and \`position_neighbors_k20\` are below zero; this indicates underperformance relative to the random baseline in this test, not a useful signal.
- The remaining R60 CV intervals include zero.
- R54 intervals are unavailable due to the small number of eligible outcomes.

## Conclusion

This bounded search did not establish a robust predictive advantage over the random baseline. Some R60 forward means are slightly positive, but their uncertainty intervals include zero, and those gains do not hold across all evaluation protocols. The evidence supports **no promotion and no production use**. Follow-up research, if undertaken, should be predeclared, account for multiple testing, and use a genuinely held-out chronological interval.
