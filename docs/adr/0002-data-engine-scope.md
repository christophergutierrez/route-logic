# 0002 — routerescalation is the data engine; training is downstream

Status: accepted
Date: 2026-07-06

## Context

The end goal is a learned model router. That spans data generation (run tasks across tiers,
label the minimum viable tier), feature extraction, model training (LoRA/classifier), and
eventually low-latency serving. These have very different lifecycles and dependency weights.

## Decision

routerescalation is scoped to the **data engine**: the harness, the task corpus, and the
emitted **dataset** (`task features -> minimum viable tier`, plus per-tier bracket verdicts
and cost). Router *training* and *serving* are separate downstream projects.

## Rationale

- **The dataset is a clean interface.** A file/schema boundary lets training iterate
  independently of the harness, and lets the harness keep running to grow the corpus.
- **Dependency hygiene.** The harness is HTTP + subprocess + JSON + files. Training pulls in
  GPU/PEFT/sklearn. Keeping them apart stops the heavy ML stack from colonizing the harness.
- **Focus.** The scarce, defensible asset is the ground-truth labeled corpus. Build the
  thing that produces it well before building consumers of it.

## Consequences

- routerescalation must define and version the **dataset schema** as a first-class artifact
  (it is the public contract), even though nothing in-repo consumes it yet.
- Feature extraction's home is deferred (it could sit either side of the boundary); revisit
  when the first consumer exists. Default lean: emit raw labeled records here, extract
  features in the training repo.
- Nothing in this repo carries a training/GPU dependency.
