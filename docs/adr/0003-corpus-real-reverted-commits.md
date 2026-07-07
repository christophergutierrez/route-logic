# 0003 — Corpus: real reverted-commit tasks gated by real test suites

Status: accepted
Date: 2026-07-06

## Context

The dataset's value depends entirely on the corpus. A task is only informative if tiers
*disagree* on it (weak tier fails, stronger passes); and each task needs a trustworthy
correctness oracle. The original project thesis was to benchmark on real-world programming
tasks, not standardized, easily-gamed benchmarks.

## Decision

The primary corpus is **real tasks mined from real repositories with existing test suites**,
starting with the author's own repos (shipsim, question2crux, killhouse, ...). The canonical
task shape:

- Pick a commit `C` whose tests pass and whose non-test change is covered by tests.
- Revert `C`'s non-test change (keep its tests). The suite now fails.
- **Task** = make the suite pass again. **Gate** = the repo's real test command. **Pinned
  repo state** = `C` with the implementation reverted.

Hand-authored toy tasks (e.g. `add_two`) are retained ONLY as plumbing smoke tests, not as
dataset material.

## Rationale

- **External validity.** The router trained on this generalizes to real work, which the
  toy/benchmark alternatives do not.
- **The oracle already exists.** The repo's test suite is the gate — no oracle to invent or
  calibrate.
- **Natural tier discrimination.** Real changes span a wide difficulty range, so tiers split
  organically instead of by hand-tuning.
- **No licensing friction** starting with self-owned repos; a path to partner repos later.

## Consequences

- **Forces multi-file executor** (see ADR-0004). Whole-file overwrite of a single target is
  insufficient.
- **Executor-ERROR must be tracked separately from tier-FAIL.** A failed patch application is
  the harness failing, not the model failing the task; conflating them corrupts labels.
- **Task mining needs selection criteria**: the reverted change must make the suite fail at
  baseline (else VACUOUS_GATE), and the suite must actually cover the change.
- Test suites must be runnable in the sandbox (deps, runtime). Repo onboarding cost is real
  and per-repo.
