# PRD — routerescalation: first live labeled bracket on a real task

Status: draft (pre-spec-audit)
Date: 2026-07-06
Scope: Milestone 1 of the data engine. See `docs/adr/0001`–`0006` and `CONTEXT.md`.

## Problem Statement

I want to know, for a real programming task, the **smallest model that can actually do it** —
verified by a real pass/fail gate, not human preference or a leaderboard. Standard benchmarks
are easily gamed and don't tell me the *minimum viable* model per task, which is the signal I
need to eventually route work to the cheapest model that will succeed. Today I have a pass-1
scaffold that proves the loop in mock on a toy task, but it can't yet run a real task from one
of my repos, live across a capability ladder, and record a trustworthy label.

## Solution

routerescalation runs one real task across a single-family tier ladder (fast/standard/reasoning)
using live models on Fireworks, applies each tier's output into a hermetic sandbox pinned to the
task's repository state, judges it with the repo's own test suite, and records the outcome as a
labeled dataset row. The output is one **minimum viable tier** label per task, derived from an
append-only log of per-measurement records, with model application failures (ERROR) kept
distinct from genuine task failures (FAIL) so cheap tiers are labeled fairly.

The mechanism is reused, not rebuilt: routerescalation imports killhouse's gate-replay harness
for record validation, model resolution, the sandbox, and the real-gate contract, and supplies
the one piece killhouse omits — the executor.

## User Stories

1. As a researcher, I want to run a single real task across all three tiers with one command, so that I get its minimum viable tier without orchestrating each tier by hand.
2. As a researcher, I want each tier's candidate change applied into a sandbox pinned to the task's exact repository state, so that a verdict reflects the model's output and nothing else.
3. As a researcher, I want the gate to be the repo's own test suite, so that correctness is judged by an oracle I already trust rather than one I invented.
4. As a researcher, I want a tier that fails to escalate meaning captured (a lower tier fails, a higher tier passes), so that the task is informative about where capability runs out.
5. As a researcher, I want the harness to record PASS, FAIL, and ERROR distinctly per tier, so that a model whose output couldn't be applied is not mislabeled as having failed the task.
6. As a researcher, I want the chosen (top) tier measured directly even though killhouse's gate-replay only replays lower tiers, so that I get a complete three-tier bracket.
7. As a researcher, I want a task expressed as a reverted-commit fixture (implementation reverted, tests kept), so that the suite fails at baseline and passing it requires reconstructing real work.
8. As a researcher, I want the harness to refuse a task whose gate already passes at baseline, so that I never record a vacuous label.
9. As a researcher, I want the three tier models to come from a single family at ascending sizes, so that the minimum viable tier is interpretable as "how small of this lineage suffices."
10. As a researcher, I want the concrete model ids and the tier ladder recorded in every dataset row, so that datasets from different ladders stay distinguishable and reproducible.
11. As a researcher, I want each measurement appended to a raw JSONL log as it completes, so that partial, failed, or retried sweeps are recorded honestly rather than lost.
12. As a researcher, I want the raw log to be killhouse delegation records enriched with routerescalation fields, so that I reuse the existing schema (prompt, gate, pinned SHA, price, outcome) instead of forking one.
13. As a researcher, I want a derived labels view (minimum viable tier + per-tier verdict map) computed from the raw log, so that I have a clean per-task training contract without hand-maintaining it.
14. As a researcher, I want the labels view to define what happens when a tier ERRORs (unmeasured) or no tier passes (label NONE), so that downstream consumers interpret edge cases consistently.
15. As a downstream router-training consumer, I want to read only the labels view, so that I depend on a stable contract and not on the harness internals.
16. As a downstream consumer, I want the raw log retained alongside the labels view, so that I can re-derive labels or audit a measurement later.
17. As a researcher, I want the executor to return each changed file's full contents tagged by path, so that multi-file changes apply robustly without diff-matching failures.
18. As a researcher, I want the executor to signal application failure with a non-zero exit, so that the harness records ERROR rather than running the gate on an unchanged tree.
19. As a researcher, I want the executor to be provider-generic (any OpenAI-compatible base_url), so that it works with Fireworks now and could be upstreamed into killhouse later.
20. As a researcher, I want to supply the three Fireworks model ids and my API key via config and environment, so that no secret is committed and the ladder is easy to change.
21. As a researcher, I want an offline mock mode, so that I can prove the whole loop (sandbox, executor, gate, bracket, dataset write) without spending on live calls.
22. As a researcher, I want to keep one or two toy tasks as smoke tests, so that I can validate plumbing changes cheaply without a real repo.
23. As a maintainer, I want routerescalation to invoke killhouse as an external dependency (import) rather than copy its logic, so that the gate/sandbox/schema stay single-sourced in killhouse.
24. As a maintainer, I want the harness's behavior covered by tests that inject a fake executor and sandbox, so that I can verify verdicts, label derivation, and the ERROR/FAIL split without live models.
25. As a researcher, I want a per-run echo of the resolved tier→model map, so that I can see exactly which models produced a dataset before trusting it.

## Implementation Decisions

- **Language & reuse (ADR-0001).** Python. The harness imports `killhouse_gate_replay` and uses
  its record validation, `resolve_model`, `command_executor`, sandbox factory, and gate execution
  directly. No reimplementation of killhouse logic.
- **Scope (ADR-0002).** This repo is the data engine: harness + corpus + emitted dataset. Router
  training and serving are out of scope and live downstream. The dataset is the interface.
- **Corpus & gate (ADR-0003).** A task is a reverted-commit fixture: a real commit's non-test
  change reverted, its tests kept, so the repo suite fails at baseline. The gate is the repo's
  real test command; the pinned repo state is the reverted commit. For Milestone 1 the task is
  hand-picked/hand-prepared; automated mining is out of scope (Milestone 2).
- **Executor (ADR-0004).** The executor prompts an OpenAI-compatible model and expects, per
  changed file, the complete new file contents in a fenced block tagged by path; it overwrites
  each named file inside the sandbox. It exits non-zero when it cannot parse or apply output, and
  the harness maps that to ERROR (distinct from a gate FAIL). Whole-file now; search/replace
  deferred until file sizes force it.

  The M1 executor must satisfy three label-integrity requirements (from the pass-1 code review):

  - **R-EXEC-1 — split ERROR semantics (review #1).** A non-zero executor exit is not a single
    bucket. The executor must distinguish *model-fault* (the model produced no applicable output:
    unparseable response, empty fence, missing file tag — a real signal about that tier's
    capability, recorded as ERROR / unmeasured) from *infra-fault* (provider/transport/config
    failure: HTTP 401 bad key, 429 rate-limit, 5xx, network error, missing base_url). Infra-faults
    must NOT be recorded as measurements of the tier; the runner retries them or fails the whole
    run loud. Conflating the two lets a single auth failure silently mark every tier ERROR and
    look like "unmeasured" when the run is invalid. `call_model` must inspect HTTP status before
    raising, and the executor must emit a distinct exit/signal for the two classes.
  - **R-EXEC-2 — attach failure diagnostics (review #2).** On any non-PASS verdict the per-tier
    result dict must carry a `diagnostics` field with the tail of the gate's stderr and the
    executor's stderr (captured, not discarded). Without this, a FAIL or ERROR on a 200-task sweep
    is opaque. Capture is cheap to build in now and painful to retrofit later.
  - **R-EXEC-3 — validate live config loud (review #4).** In live mode the runner must validate
    that `base_url` is non-empty (not just that the API key is set) before invoking any tier. An
    empty/missing base_url currently becomes ERROR-for-all-tiers, which feeds R-EXEC-1's
    conflation. Fail loud at startup instead. (Implemented in `run_bracket.py main()`; survives the
    executor rewrite because it lives in the config-validation path, not in `executor.py`.)
- **Tier ladder (ADR-0005).** `.killhouse/config.json` `model_tiers` holds three ids from one
  model family at ascending sizes. The map is echoed per run and recorded per dataset row.
- **Three-tier bracket.** killhouse's gate-replay replays only tiers below `chosen_tier`. The
  runner therefore records the top tier by running it directly through the same injected sandbox +
  executor + gate path, and replays the two lower tiers via the harness. All three share one
  executor and one sandbox factory so measurements are apples-to-apples.
- **Sandbox.** Real-repo tasks use killhouse's hermetic `git_worktree_sandbox` pinned to the
  record's `repository_state.head` (requires the fixture committed). The copy-based sandbox is
  retained only for toy smoke tests.
- **Dataset, two layers (ADR-0006).**
  - *Raw measurement log:* append-only JSONL of killhouse delegation records, one per
    task × tier × attempt, enriched via the schema's open `additionalProperties` with `task_id`,
    concrete `model_id`, latency, token counts, and the PASS/FAIL/ERROR outcome.
  - *Labels view:* derived per-task record `{ task_id, minimum_viable_tier, per_tier_verdicts,
    tier_model_map }`, regenerable from the raw log. `minimum_viable_tier` = lowest tier with
    PASS; a tier that ERRORed is "unmeasured" at that tier; if no tier passes, the label is NONE.
- **Config.** A single `.killhouse/config.json` carries the experiment ladder in `model_tiers`;
  no decoupling from killhouse pipeline routing for now, since killhouse routing is unavailable in
  this runtime and ignores the map. `api_key_env` names the token env var; the token stays in the
  environment.
- **Schemas owned here.** Two small schemas: the enrichment fields added to the raw record, and
  the labels-view schema (the training contract). The raw record otherwise conforms to killhouse's
  `delegation_record.schema.json`.

## Testing Decisions

- **What a good test is:** it asserts external behavior — the per-tier verdicts, the derived
  minimum viable tier, the raw-log rows written, and the ERROR-vs-FAIL classification — never the
  harness's internal steps. No live network in the test suite.
- **Single highest seam:** the executor (and sandbox factory) injection point, exactly as
  killhouse's own gate-replay is designed and tested. Tests pass a fake executor that writes known
  per-tier outputs and a temp/copy sandbox factory, then assert on the runner's returned bracket
  and the emitted dataset. This is the one seam; no new seams are introduced.
- **Prior art:** killhouse `tests/` gate-replay tests already inject `executor` and
  `sandbox_factory` into `gr.replay(...)` and assert verdicts (e.g. "executor writes nothing ⇒ gate
  fails", "no routing ⇒ executor never called"). routerescalation's tests mirror this pattern.
- **Modules tested:** the bracket runner (verdict aggregation, top-tier direct run, label
  derivation, ERROR/NONE edge cases) and the executor (whole-file parse/apply; non-zero exit on
  unparseable/inapplicable output). The dataset writer/labels-deriver is tested via the runner's
  observable output.
- **Cases that must be covered:** all-pass ⇒ min tier = fast; split ⇒ min tier = the lowest
  passing tier; no-pass ⇒ NONE; a tier whose executor fails ⇒ ERROR (not FAIL) and excluded from
  the label; a vacuous gate (passes at baseline) ⇒ refused. The existing mock already exercises the
  split case end-to-end.

## Out of Scope

- Automated task mining from git history (Milestone 2). Milestone 1 uses a hand-prepared task.
- Feature extraction, router training, and router serving (separate downstream repo per ADR-0002).
- Cross-family / best-in-class tier selection (ADR-0005 defers it).
- Search/replace or unified-diff executor formats (ADR-0004 defers them).
- Speculative-decoding / draft-model pairing, cost/burn-rate dashboards, provider comparison across
  proprietary platforms — all later-vision items from the founding notes, not this milestone.
- Decoupling the experiment tier map from killhouse pipeline routing.

## Further Notes

- The pass-1 Python scaffold (`bin/run_bracket.py`, `bin/executor.py`, `tasks/add_two/`) already
  runs green in mock and serves as the executable spec for this milestone; this PRD hardens it from
  a single-file toy to a multi-file real task with a persisted dataset.
- Upstream opportunity: a provider-generic executor is the piece killhouse's gate-replay ships
  without. If kept generic, it is a clean contribution back to killhouse.
- Operational prerequisites the researcher supplies before the live run: three real Fireworks model
  ids, `FIREWORKS_API_KEY`, and the first repo to prepare a task from.

## Assumptions and Open Questions

- **ASSUMPTION:** killhouse remains importable at `$KILLHOUSE_ROOT` (default `~/git_home/killhouse`)
  and its `delegation_record.schema.json` continues to allow `additionalProperties` for enrichment.
- **ASSUMPTION:** capability is monotonic within the chosen family (fast ≤ standard ≤ reasoning);
  this is spot-checked, not guaranteed, per ADR-0005.
- **ASSUMPTION:** the single-seam executor/sandbox injection is sufficient to test all target
  behavior offline; no live-model integration test is required to pass the milestone.
- **OPEN QUESTION (material):** how are a real repo's **test dependencies** made available inside
  the hermetic `git_worktree_sandbox`? Running the repo's suite needs its toolchain/deps (cargo,
  pip env, etc.). PLAN must decide: rely on the ambient environment, provision per-repo, or restrict
  Milestone 1 to repos whose suite runs with no extra setup.
- **OPEN QUESTION:** exact Fireworks model ids for the ladder, and the first source repo — supplied
  by the researcher; do they meet the "gate runs with available deps" bar above?
- **OPEN QUESTION:** where does the raw log live and how is it keyed (per-repo? global JSONL?), and
  how are re-runs/attempts de-duplicated when deriving the labels view? PLAN to specify.
