# GENERAL AI Agent Project

> **Project:** GENERAL Super Loto  
> **Repository:** `xxlyusufi61-bot/general-super-loto`  
> **Documentation status:** Current-state guide and binding project constraints  
> **Important:** A green workflow/check confirms only the checks that workflow actually runs. It does not, by itself, prove the whole AI-agent system or a lottery method is correct.

## 1. Purpose and scope

GENERAL is a GitHub-hosted project intended to coordinate specialized AI-agent tasks and support careful research on the Super Loto draw archive. Its responsibilities include archive retrieval and integrity checks, target-draw isolation, method research, code tests, experiment reporting, and maintaining an auditable record of what is implemented versus what remains experimental.

GENERAL is a research and software-validation project—not a way to guarantee lottery outcomes. Random draw outcomes remain uncertain. No method should be described as predictive or successful without reproducible, leakage-free evidence.

## 2. Current architecture

### Runtime and orchestration

- **GitHub repository and GitHub Actions** hold the source code and run explicitly triggered workflows; some workflows also run on a schedule.
- **`.github/workflows/general-agent.yml`** is the manually triggered agent workflow. It offers the `research`, `debug`, `validation`, `crossover`, `archive`, and `isolation-test` choices. A single-agent report is not persisted to shared memory unless the operator explicitly chooses `publish_memory_pr=YES`; then the report must pass the memory-delta guard and is proposed in a public review-only PR. The default is `NO`, and automatic merge is disabled.
- **`.github/workflows/general.yml`** is the scheduled/manual Telegram heartbeat workflow. It checks Telegram bot authentication and message delivery. It explicitly does **not** establish that the GENERAL core, archive integrity, or target isolation are healthy.
- **`general_core.py`** is the Python coordinator for the four AI roles. It applies task-mode and target-date rules, prepares guarded data when needed, builds model context, calls the configured NVIDIA model API, and can save reports to project memory.
- **`NVIDIA_API_KEY`** and **`TELEGRAM_BOT_TOKEN`** are expected to be stored as GitHub Actions secrets, not committed to source files.

GitHub Actions is a job runner, not a continuously running server. A scheduled heartbeat is not proof of 24/7 agent reasoning. Any separate AlwaysData account, scheduled task, or `heartbeat.py` setup is external to the workflows documented here; its present availability and runtime are **not verified by this repository README**. Preserve that setup unless it is separately inspected and a change is approved.

### Data and research components

- **`general_archive_fetch.py`** retrieves/refreshes the draw archive.
- **`general_data_guard.py`** validates archive records and supports target isolation.
- **`general_archive_cross_source_audit.py`** provides a separate archive cross-source audit.
- **`general_method_registry.json`** stores method definitions/statuses and historical-result records with provenance fields.
- **`formula_evidence_catalog.json`** records formula-source evidence, recovery status, known limitations, and unverified historical results.
- **`general_method_candidates.py`** contains experimental candidate implementations and evaluation support.
- **`general_walkforward.py`** implements a separate walk-forward/chronological evaluation path.
- **`legacy_sources/`** preserves recovered historical source files for audit; their presence does not mean their logic or old scores have been approved.
- **`v86_leakage_safe_evaluator.py`** is a separate audit candidate with synthetic tests. It is not a canonical formula and has not been validated on the real archive.
- **`general_memory.json` / `general_memory.py`** store and manage agent reports. Reports are evidence to inspect, not proof by themselves.

## 3. Agent roles

| Agent | Intended responsibility | Boundary |
|---|---|---|
| **Research** | Investigate hypotheses and method families, including A→B→C, A→T, and MOD5→T. | Must separate hypotheses from verified findings and respect target isolation. |
| **Debug** | Find reproducible code defects and propose/test fixes. | Must not claim a fix passed tests unless those tests actually ran. |
| **Validation** | Check archive integrity, chronology, target partitioning, and evidence quality. | Must report exactly what was checked and what remains unknown. |
| **Crossover** | Compare candidate methods and test combinations/weights. | Selection must not use the final test interval; avoid overfitting. |
| **Archive** | Run the archive-related workflow path. | A successful retrieval alone is not proof that every historical source or draw has been independently verified. |
| **Isolation test** | Check that the latest valid archive draw is isolated from training. | A passing test verifies the assertions in that workflow, not every possible leakage route in all code. |

These are implemented workflow choices and role descriptions. They do not imply that six independent, continuously running agents are operating autonomously or communicating in the background.

## 4. Binding Super Loto research rules

The following are project constraints, not optional recommendations:

1. **Keep the target draw isolated.** Do not use its numbers during formula discovery, training, feature construction, model selection, or tuning.
2. **Use strict chronology.** For a target date T, training data must be strictly earlier than T. Exclude all target/future records from target-specific training.
3. **Validate the archive before analysis.** Check schema, date order, duplicate dates, draw-number validity, missing/conflicting records, and the expected date range before trusting an experiment.
4. **Preserve the established method families:** A→B→C, A→T, and MOD5→T. Do not silently replace or redefine them.
5. **Evaluate chronologically.** Use walk-forward testing and chronological cross-validation. Model selection/tuning must be separated from a frozen final evaluation.
6. **Use two-way chronology for mechanism discovery and testing.** Evaluate forward in time (2020 toward the latest pre-target period) and run reverse chronology as a separately labelled retrospective stress test. Reverse results are not forward predictive evidence.
7. **Keep target dates, locked targets, and post-target information isolated.** Do not expose target numbers in logs, reports, model context, or memory intended for earlier-target runs.
8. **Do not invent results.** Distinguish code inspection, synthetic tests, archive-level checks, and actual chronological experiments. A passing unit test does not prove lottery predictive performance.
9. **Preserve negative results and provenance.** Record source/version, archive range, split boundaries, configuration, test outputs, and known defects when available.
10. **Do not silently change binding rules.** Explain a proposed conflict and obtain user approval before changing established constraints.
11. **Do not promise a winning prediction.** Lottery outcomes are uncertain; no candidate should be promoted without reproducible evidence.

### Candidate promotion gate

A formula is not canonical merely because code exists or a historical score was reported. Before promotion, document its exact definition and source version; audit the archive and target boundary; run leakage-free chronological validation and cross-validation; separate model selection from final evaluation; include the reverse-time stress test with the correct interpretation; and preserve reproducible outputs. If evidence is missing, the method remains experimental or open-definition.

## 5. Current status: implemented vs. unverified

### Present in the repository

- A Python coordinator with task modes and target-date handling.
- An archive validator/target-isolation component and workflows that call it.
- Research, debug, validation, crossover, archive, and isolation-test workflow choices.
- Separate workflow paths for method candidates, walk-forward evaluation, security checks, and registry/evidence tests.
- A method registry and formula evidence catalog.
- Recovered legacy source files and a separate V8.6 audit-candidate evaluator.
- A Telegram heartbeat workflow and secret-based API configuration.

### Not established by the existence of these files

- That every draw in the archive has been independently verified against an authoritative source.
- That every legacy algorithm is correct or leakage-free.
- That historical V19/V0.3.1 weights or reported scores have been reproduced and validated.
- That A→B→C, A→T, or MOD5→T has a final approved canonical definition.
- That the V8.6 audit candidate has been run against the real archive or that its performance is better than a baseline.
- That nested chronological cross-validation is fully implemented and verified end to end.
- That multiple agents run continuously, operate independently, or communicate autonomously in the background.
- That an external AlwaysData service/task is currently active.
- That the system can guarantee or reliably predict future winning numbers.

Status must be updated only when supported by actual code inspection or reproducible tests. Never turn a planned capability into a completed claim in this document.

## 6. Security, cost, and change management

- Prefer free-tier services and avoid uncontrolled charges. Services requiring payment-card verification are disallowed under the current project constraints.
- Do not enable automatic paid upgrades or services with unclear billing risk.
- Never commit API keys, bot tokens, passwords, or other secrets. Use repository secrets and redact sensitive values from logs.
- Do not expose draw numbers from a locked target in logs or summaries.
- Do not delete or change working infrastructure, scheduled tasks, secrets, archive data, or binding rules without first explaining the effect and receiving approval where required.
- Keep legacy sources unchanged during audit. Make corrections in a separately named reviewed version so the original evidence remains available.
- Add tests for new behavior. Inspect CI results and the specific test steps; do not treat a queued or incomplete check as a pass.
- Every workflow that can call NVIDIA must default to **no live call** and require the exact confirmation phrase `I_CONFIRM_FREE_ONLY_NO_BILLING_AND_APPROVE_UP_TO_3_NVIDIA_REQUESTS`. The remote global safety lock must also be checked before inference; GENERAL Core enforces the consent and preflight again at the API boundary.
- Blank-slate research additionally requires the user to confirm that free use and no-charge risk were independently verified. That input is an operator attestation, not proof supplied by the workflow. If free-only status is unclear, do not authorize a live request.
- Archive-only, isolation-test, coordinator-plan, and offline-test paths must not need live model consent because they do not make model requests.
- Update this README when implementation or binding rules materially change.

## 7. Workflows and what they prove

| Workflow file | Purpose | What success means—and does not mean |
|---|---|---|
| `general-agent.yml` | Refresh and validate the archive, then run the selected agent or isolation test. Live agent calls require the exact confirmation phrase. Optional report persistence is a separate `publish_memory_pr=YES` choice. | Without the phrase, the agent call is skipped; archive and isolation checks can still run. Shared memory is only proposed through a guard-validated public review PR; it is not automatically merged or bundled as an artifact. |
| `general-coordinator-execute.yml` | Build an agent plan and execute the coordinator only after explicit consent and global-lock preflight. | Defaults to plan-only and can make up to three sequential requests only when explicitly enabled; it does not prove provider billing settings. |
| `nvidia-test.yml` / `nvidia-chat-test.yml` | Check NVIDIA API access or request one test response. | Both default to no request and require the exact confirmation phrase and a clear global lock. |
| `open-research.yml` | Run blank-slate research with a separate user-confirmed free-use check. | The research job is skipped unless exact consent, the free-use attestation, and global-lock preflight all pass. |
| `general.yml` | Scheduled/manual Telegram heartbeat. | Confirms Telegram checks in that workflow; not core or archive health. |
| `method-registry-test.yml` | Test registry/evidence integration and compile/test audit components. | Confirms those listed tests only; not real-archive predictive validity. |
| `method-candidate-evaluation.yml` | Evaluate experimental candidates on isolated pre-target history. | A run must be inspected for its exact split and output; it does not automatically establish a final independent holdout. |
| `walkforward-evaluation.yml` | Run walk-forward and chronological evaluation. | Confirms the configured evaluation ran; model selection, baseline comparison, and split integrity still need review. |
| Other `*-test.yml` workflows | Test specific archive, security, target-isolation, or evaluation behavior. | Each result is limited to the assertions and fixtures used by that workflow. |

## 8. Development roadmap

1. **Finish legacy-source audit.** Review the recovered V8.6 and V1 engines and the NEAR_STATE v5.0 engine. Keep originals unchanged; record exact defects and fixes.
2. **Establish canonical method definitions.** Compare historical source implementations with current candidates for A→B→C, A→T, and MOD5→T. Keep definitions open until evidence and approval justify promotion.
3. **Verify archive provenance and integrity.** Reconcile cross-source differences and record exact covered dates and counts; do not silently resolve conflicting draws.
4. **Strengthen evaluation.** Verify forward walk-forward, chronological cross-validation, nested selection/final separation, baseline comparisons, and the separately labelled reverse-time stress test.
5. **Reproduce historical results.** Revisit V19 and V0.3.1 only when source versions, data boundaries, configurations, and logs are sufficient to reproduce them.
6. **Improve orchestration only after the evidence path is reliable.** Confirm each agent's actual permissions, inputs, outputs, persistence, and execution behavior before describing it as autonomous.
7. **Review remote uptime and costs separately.** Verify any external AlwaysData task and ensure no disallowed card verification or uncontrolled billing is introduced.

## 9. How to read project claims

Use these labels consistently:

- **Implemented:** Code exists in the current branch.
- **Unit-tested:** The named automated tests passed on the stated commit.
- **Synthetic-tested:** Tests used artificial fixtures, not the real draw archive.
- **Archive-checked:** The named archive validation ran and its scope is recorded.
- **Experimentally evaluated:** A reproducible, chronological experiment ran with documented splits and baselines.
- **Verified result:** Independent review can reproduce the reported result under the documented protocol.
- **Planned / unverified:** Implementation or evidence is incomplete.

A source file, successful syntax check, green heartbeat, or model-generated report alone is not a verified research result.
