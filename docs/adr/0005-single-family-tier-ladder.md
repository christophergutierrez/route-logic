# 0005 — Tiers are a single-family capability ladder

Status: accepted
Date: 2026-07-06

## Context

The three tiers map to three concrete models. "Minimum viable tier" is only interpretable if
the tiers form a genuine capability ordering (fast < standard < reasoning). Two selection
principles compete: a single model family at ascending sizes, or best-in-class-per-cost-band
across families.

## Decision

For the initial datasets, tiers are a **single-family capability ladder** — the same model
family at ascending sizes (for the coding corpus, e.g. Qwen2.5-Coder 7B / 14B / 32B on
Fireworks; exact ids confirmed operationally). Cross-family / best-in-class selection is
deferred to a later expansion when building the router's training set.

## Rationale

- **Monotonic capability.** Same family, more parameters ⇒ a clean capability ordering, so a
  task's minimum viable tier means "how small a model of this lineage suffices" without
  family/training-data confounds.
- **Answers the founding question** — "how much can you squeeze out of smaller models" — with
  the fewest confounds.
- **Interpretable first dataset.** Mixed families break monotonicity (a 32B can beat a 72B on
  a given task), which muddies the label before we even have baseline signal.

## Consequences

- External validity for a *deployed* router (which chooses across families) is intentionally
  traded away for now; add cross-family columns later.
- The concrete model ids live in `.killhouse/config.json` `model_tiers` and are recorded per
  run in the dataset, so datasets from different ladders stay distinguishable.
- Capability monotonicity is an assumption to spot-check, not a guarantee, even within a family.
