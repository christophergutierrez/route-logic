# 0004 — Executor applies whole-file blocks, not diffs

Status: accepted
Date: 2026-07-06

## Context

The real-repo corpus (ADR-0003) requires applying multi-file model output. The application
method affects **label validity**: a failed application is the harness failing, not the model
failing the task, and it disproportionately penalizes weak tiers (which struggle most to emit
clean diffs) — corrupting exactly the cheap-tier labels the project exists to measure.

## Decision

The model returns, for each changed file, its path plus the **complete new file contents** in
a tagged fenced block; the executor overwrites each named file. No diff/patch application for
now. Migrate to search/replace blocks (aider-style) only when file sizes make whole-file
output token-prohibitive or truncation-prone.

Regardless of format, **executor-ERROR is recorded as a distinct outcome from tier-FAIL.**

## Rationale

- **Most robust application** — no context lines or offsets to mismatch, so the lowest
  spurious-error rate and the fairest treatment of weak tiers.
- **Simplest parser**, consistent with keeping the executor dumb until complexity earns itself.
- Fits the corpus: reverted single commits are usually localized to a few small files.

## Consequences

- Large files are the known weak spot (token cost, truncation risk) — the trigger to add
  search/replace application.
- The prompt must instruct the model to return each changed file in full, tagged by path.
- The harness reports three outcomes per tier — PASS / FAIL / ERROR — and dataset consumers
  must treat ERROR as "unmeasured," not "failed."
