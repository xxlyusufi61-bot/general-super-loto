# GENERAL AI Agent Project

> **Project:** GENERAL Super Loto  
> **Repository:** `xxlyusufi61-bot/general-super-loto`  
> **Documentation status:** Current-state guide and permanent project constraints  
> **Important:** A green workflow confirms only the checks that workflow actually runs. It does not prove the whole AI-agent system or a lottery method is correct.

## 1. Purpose

GENERAL coordinates specialized tasks for Super Loto archive integrity, target-draw isolation, method research, code testing, and evidence-quality reporting. It is a research and software-validation project, not a way to guarantee lottery outcomes. Draw outcomes remain uncertain.

## 2. Current architecture

- `.github/workflows/general-agent.yml` is the manually triggered agent workflow. A single-agent report is not persisted to shared memory unless the operator explicitly chooses `publish_memory_pr=YES`; publication is review-only and automatic merge is disabled.
- `general_core.py` applies task-mode and target-date rules, prepares guarded data when needed, builds bounded source context, calls the configured NVIDIA model API when its safety guards allow it, and validates the report structure.
- `general_data_guard.py` validates archive records and isolates a target draw.
- `general_archive_fetch.py` refreshes the canonical archive; `general_archive_cross_source_audit.py` is a separate cross-source audit path.
- `general_method_registry.json` contains only three open research-family labels and experiment safeguards. It must not contain old formula implementations, source pointers, tuned weights, or historical formula scores.
- `general_walkforward.py` implements separate chronology-aware baseline evaluation. It does not define the three open formula families.
- `general_nvidia_guard.py` enforces live-inference safety and consent controls. Offline tests should be preferred when they answer the question.
- `general_memory.json` stores agent reports, but reports are evidence to inspect rather than proof. Current runtime prompts do not load the historical shared-memory report entries.
- `.github/workflows/nvidia-safety-tests.yml` runs offline NVIDIA timeout/guard regression checks. It does not make a live inference request.
- `.github/workflows/general.yml` is the Telegram heartbeat path. Its success is not proof that the GENERAL core or autonomous agents are continuously working.

GitHub Actions is a job runner, not a continuously running server. A scheduled heartbeat is not proof of 24/7 agent reasoning. Do not claim 24/7 operation without independent evidence of a separate continuously available runtime.

## 3. Agent roles

| Agent | Responsibility | Boundary |
|---|---|---|
| Research | Develop and test new hypotheses from scratch. | No inherited formula implementations or old formula scores. |
| Debug | Find reproducible code defects and test fixes. | Do not claim tests passed unless their results are available. |
| Validation | Check archive integrity, chronology, target partitioning, and evidence quality. | State exactly what was checked and what remains unknown. |
| Crossover | Compare newly defined methods and test combinations. | Avoid overfitting and never reuse old tuned weights. |
| Archive | Work with archive retrieval and integrity checks. | Retrieval alone does not prove every historic draw is independently verified. |
| Isolation test | Check target separation from training. | A passing test proves only the assertions it actually exercises. |

Every model-backed specialized agent must report `SCOPE_AND_EVIDENCE`, `CHECKS_PERFORMED`, `FINDINGS`, `LIMITATIONS`, and `NEXT_OFFLINE_CHECK`. Non-empty findings must be labeled `VERIFIED_FROM_SUPPLIED_EVIDENCE`, `HYPOTHESIS`, or `UNVERIFIED`. Structural checks cannot independently prove a claim is true.

## 4. Permanent Super Loto research rules

These are binding project constraints:

1. Keep the target draw hidden during discovery, training, feature construction, model selection, and tuning.
2. Use only records strictly earlier than the isolated target for predictive evaluation.
3. Validate archive structure and chronology before analysis; stop if a required check fails.
4. Preserve `A->B->C`, `A->T`, and `MOD5->T` as research-family labels only. Each exact mathematical definition and implementation must be developed from scratch and remains `OPEN_DEFINITION` until explicitly approved.
5. Use leakage-free forward walk-forward evaluation and time-ordered cross-validation.
6. Keep reverse chronology as a separately labeled retrospective stress test, never predictive evidence.
7. Keep R54 and R60 separate; do not silently mix the regimes.
8. Never invent archive data, experiments, results, or test status.
9. Never change permanent project rules silently; ask the user before changing them.
10. Separate hypotheses from verified findings and preserve reproducible tests, boundaries, baselines, and uncertainty.
11. Never promise a winning prediction.

## 5. Fresh-start method registry

The active repository contains no inherited formula-source files, old experimental candidate engine, old formula evidence catalog, or historical formula weights/results in the method registry. The registry is deliberately limited to the three research-family labels, blank canonical definitions, and general validation gates. The runtime rejects a registry that contains non-empty history or legacy candidate pointers.

A method is not approved merely because a family label exists. Before a new method can be tested on the real archive, its mathematical definition must be written from scratch, deterministic synthetic tests must pass, archive/target boundaries must be checked, and the agreed chronology-aware protocols and null baseline must be run. Promotion still requires explicit user approval.

### Active research notation and governance reference

`docs/research-notation-and-governance.md` defines only the neutral meanings of A, B, C, T, MOD5, the three open research-family labels, and the binding research/service rules. It contains no historical formula algorithms, prior test results, scores, weights, or candidate rankings.

The notation and research rules may be supplied to agents. The three families remain `OPEN_DEFINITION`; every mathematical definition must be developed and tested from scratch. Historical formula documents and old experiment outputs must not be loaded as agent context.

## 6. Interpretation of checks

- A successful workflow confirms only the checks named in its logs.
- A successful Telegram heartbeat confirms only the checks it performs.
- An offline API safety test does not verify the live inference service.
- A successful code or archive test does not establish lottery predictability.
- An experimental method must not be called predictive without reproducible leakage-free evidence; no method can guarantee lottery outcomes.

## 7. Safe workflow practice

Use the offline checks first. Do not make live inference requests unless all project safety guards allow them and the required consent/preflight are satisfied. Do not merge a pull request solely because it is green; inspect changed files and the exact scope of every passing check.
