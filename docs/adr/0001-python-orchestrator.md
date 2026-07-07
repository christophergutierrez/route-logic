# 0001 — Python for the orchestrator

Status: accepted
Date: 2026-07-06

## Context

routerescalation is a data-collection harness: for each task it calls an LLM across model
tiers, applies the output in a sandbox, runs a real gate, and records the minimum viable
tier. killhouse — whose gate-replay machinery this project reuses — is written in Python.

The author's other active projects (shipsim, question2crux) are Rust, and a Rust-first
orchestrator was seriously considered for its concurrency ergonomics and compile-time
robustness. The decision hinged on one question: under heavy load, is the language a
performance factor?

## Decision

Use **Python** for the orchestrator (harness, executor, and near-term analysis).

## Rationale

- **Performance is LLM-bound, not code-bound (~100×).** Per unit of work (one task × one
  tier), wall-clock is dominated by LLM generation (5–120 s) and the gate subprocess
  (ms–30 s); the orchestrator's own work is < 5 ms. Both heavy steps run *outside* the
  Python GIL — LLM calls are network I/O (GIL released on await), gates are separate OS
  processes. Under heavy load, throughput is set by *concurrency*, and concurrency is
  capped by the provider's rate limit, not by the event loop. Rust's speed advantage lands
  entirely in the < 1% slice, so language performance is moot here.
- **Import-level reuse of killhouse.** The runner does `import killhouse_gate_replay` and
  calls its schema validator, model resolution, executor template, and gate contract
  directly. Rust would demote this to shelling out to a CLI or reimplementing (and
  maintaining a fork of) logic the author already owns in killhouse. Python keeps one
  implementation.
- **Least code, fastest iteration** for a glue project (HTTP + subprocess + JSON + files).
  The pass-1 scaffold was ~200 lines *because* of the reuse.
- **The ML endgame stays reachable.** Feature extraction and any router training
  (sklearn / pandas / PEFT) are Python-native.

## Consequences

- Concurrent Fireworks sweeps use `asyncio`; gates fan out to a process pool. The hot loop
  must stay pure I/O — no CPU-bound work in-process; bulk log analysis goes to numpy/polars
  off the critical path.
- Revisit a Rust/Go split ONLY for a future low-latency router-*serving* component, behind
  a subprocess boundary, and only when a profiled hotspot justifies it. Not now.
- This supersedes the earlier "Rust-first" exploration and the dependent "how does Rust
  touch killhouse" boundary question, which is moot under import-level reuse.
