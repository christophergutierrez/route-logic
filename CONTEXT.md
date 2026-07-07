# CONTEXT — routerescalation glossary

The ubiquitous language for routerescalation. Glossary only — no implementation details.
See `docs/adr/` for decisions.

## Terms

- **Tier** — an abstract capability level (`fast` | `standard` | `reasoning`) that maps to
  one concrete model id. Inherited from killhouse.

- **Minimum viable tier** — for a given task, the smallest-capability tier whose output
  passes that task's real gate. The core label routerescalation produces. A task with no
  passing tier has an undefined (or "NONE") minimum viable tier.

- **Gate** — a falsifiable, binary check (a command with a pass/fail exit) that judges
  whether a candidate change is correct. The correctness oracle. Must be able to fail at
  baseline (`baseline_polarity: fail`) or the label is meaningless. Inherited from killhouse.

- **Delegation record** — killhouse's per-delegation schema (`record.json`) capturing the
  prompt, chosen tier, gate, pinned repository state, and outcome. routerescalation's tasks
  carry one.

- **Bracket** — the set of per-tier pass/fail verdicts for one task, run across all tiers.
  The minimum viable tier is read off the bracket (the lowest tier that PASSed).

- **Executor** — the component that turns a prompt + a model id into a candidate change
  applied inside a sandbox. The piece killhouse's gate-replay harness does not ship.

- **Task** — a self-contained unit: a prompt, a starting repository state, and a gate.

- **Corpus** — a collection of tasks.

- **Data engine** — the harness that runs a corpus across tiers and emits labeled records
  (`task features -> minimum viable tier`). routerescalation's product for pass 1.

- **Raw measurement log** — the dataset's source of truth: an append-only JSONL of
  (extended) killhouse delegation records, one per measurement (task × tier × attempt). See
  ADR-0006.

- **Labels view** — the derived, per-task training contract: `minimum viable tier` + the
  per-tier verdict map, computed from the raw measurement log. What downstream consumers read.

- **Escalation** — killhouse's live "guessed-too-low" signal (a chosen tier failed and the
  run moved up). The offline counterpart, "guessed-too-high," comes from replaying a lower
  tier against the same gate.

- **Reverted-commit task** — the canonical real task: a real commit's non-test change is
  reverted (its tests kept), so the repo's own suite fails at baseline; the task is to make
  it pass. The gate is the repo's real test command. See ADR-0003.

- **Executor-ERROR vs tier-FAIL** — ERROR means the harness could not apply the model's
  output (unmeasured); FAIL means the applied output did not pass the gate (the tier
  genuinely failed the task). They are recorded distinctly; conflating them corrupts labels.
