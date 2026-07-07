# 0006 — Two-layer dataset: raw KH records + derived labels

Status: accepted
Date: 2026-07-06

## Context

The dataset is routerescalation's deliverable and the interface to the downstream training
repo (ADR-0002), so its schema is a public contract. The harness already emits killhouse
delegation records per run, and that schema declares `additionalProperties: true`.

## Decision

The dataset has **two layers**:

1. **Raw measurement log** — an append-only JSONL of killhouse delegation records, one per
   measurement (task × tier × attempt), enriched with routerescalation fields (`task_id`,
   concrete `model_id`, latency, token counts, and PASS/FAIL/ERROR outcome) carried in the
   record's open `additionalProperties`. This is the source of truth.
2. **Labels view** — a derived, per-task table: `minimum_viable_tier` plus the per-tier
   verdict map, computed from the raw log. Not stored as primary data; regenerable.

## Rationale

- **Maximum reuse.** The raw layer is killhouse's record extended, not a fork — the prompt,
  gate, pinned SHA, price, and outcome are already modeled.
- **Append-friendly, partial-run safe.** One row per measurement means failed, retried, or
  incomplete tier sweeps are recorded honestly; the label is computed only over what exists.
- **Clean separation.** Immutable raw measurements + regenerable derived labels is standard
  data-engineering hygiene and mirrors killhouse's own `delegations.jsonl` philosophy.

## Consequences

- routerescalation owns and versions two small schemas: the enrichment fields on the raw
  record, and the labels-view schema (the actual training contract).
- The labels view must define how ERROR and "no passing tier" map to `minimum_viable_tier`
  (ERROR at a tier ⇒ that tier unmeasured; no tier passes ⇒ label is NONE/undefined).
- Consumers read the labels view; the raw log stays available for re-derivation and audit.
