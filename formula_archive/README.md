# GENERAL — Legacy Formula Candidate Archive

**Archive version:** 0.1.0  
**Date:** 2026-10-26  
**Purpose:** Keep a traceable catalogue of old formula/method candidates without silently approving them.

## Safety and binding rules

This archive is a **catalogue of candidates, not an approval to run them**. It does not change `general_method_registry.json`. The three project methods—`A->B->C`, `A->T`, and `MOD5->T`—remain `OPEN_DEFINITION`, with `canonical_definition: null` and `execution_ready: false`.

All future evaluation must use the canonical, validated Super Loto archive; isolate the current target; exclude that target and every later draw from discovery/training; keep forward walk-forward, reverse retrospective stress testing and time-ordered cross-validation as separate protocols; and compare results with an appropriate random/null baseline and uncertainty. A legacy result is not proof of predictive ability.

## Archived legacy artifacts

| Artifact | SHA-256 | Contents and warning |
|---|---|---|
| `Super_Loto_V8_6_Donusum_Kesif.py` | `47dd3164def60ca0ea29cdbb07d11be061d10e8cc7a20dfa5f0cd72fc3a4f1aa` | Transition-signature features, exact/sign/absolute transforms and nearest-transition voting. The historical evaluator scores the current transition's B draw, not following C. It also duplicates `posdiff` and `rawdiff`. |
| `Super_Loto_Sifre_Kesif_Motoru_v1.py` | `1d6df432818693d764a028a4be190e497a42b7068c309585cabf6fe2aaa0b6b2` | A collection of 21 legacy generators. It embeds its own old draw archive and fixed #31/#70/#107 windows. Do not execute its embedded archive or reuse its fixed target rules. |

The source filenames, hashes and relevant line ranges are recorded in `legacy_candidates.json` so the catalogue can be checked against the original artifacts. The source code itself is not copied into this archive because one legacy file contains hard-coded draw records; the catalogue records the algorithms and audit issues without importing those records.

## The three binding method names

- **A→B→C:** The V8.6 source constructs A→B transition signatures and votes on the matched transitions' B outcomes. That is not a C-state predictor, so this candidate is not canonical-ready.
- **A→T:** The cipher motor counts number-to-next-draw-number co-occurrences. The project's exact A/T semantics and tie/minimum-history rules have not been explicitly approved.
- **MOD5→T:** The V8.6 source uses per-position modulo-5 residue changes as a transition feature; it does not define a MOD5-keyed mapping to T.

## Generator catalogue

`legacy_candidates.json` lists 21 named generators with their source function, parameter values, calculation summary, minimum history (when specified), and known limitations. Every generator has `execution_ready: false` and `status: LEGACY_CANDIDATE_NOT_VALIDATED`.

Notable audit issues:
1. The old cipher script chooses and reports its winner using the same purported final holdout window, compromising that window as an untouched final estimate.
2. It silently skips failures from individual generators and fills incomplete outputs with the lowest numbers.
3. It does not compute an appropriate repeated null/random baseline with uncertainty intervals.
4. The source embeds historic draw data and hard-coded dates; neither is an authorized current data source or target protocol.

## Promotion process

Do not promote any entry or change the method registry automatically. First get explicit approval of the mathematical definition, then implement isolated deterministic synthetic tests, validate archive/target isolation, and run the required chronological protocols against baselines. Preserve any failed tests and contradictory results in the registry.
