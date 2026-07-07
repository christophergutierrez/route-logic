# HANDOFF — routerescalation (killhouse pipeline in progress)

Date: 2026-07-06
Reason: session low on tokens; resume from here.

## TL;DR — where we are

Developing `routerescalation` through the **killhouse pipeline** (`/ask-kh`). We are at the
**post-PRD checkpoint** in **Checkpoint mode**. Grilling is done, all decisions captured as
ADRs, the PRD is written. The next stage is the spec audit. Nothing is committed to git yet.

routerescalation = a **data engine**: run real, gated coding tasks across model tiers (Fireworks)
and record the **minimum viable tier** per task, to feed a downstream router-training repo. It
reuses killhouse's gate-replay harness by importing it; it supplies the one piece killhouse omits
(the executor).

## ask-kh resumable state

```yaml
classification: major
stage: to-prd COMPLETE  -> next: loops/REVIEW_DOCUMENT (9-subagent spec audit)
autonomy: checkpoint
execution_policy: cost_optimized
model_routing: unavailable   # KH pipeline subagents run on the current model;
                             # the Fireworks model_tiers map is the EXPERIMENT's, not KH routing
artifacts:
  glossary: CONTEXT.md
  adrs: docs/adr/0001..0006
  prd: docs/PRD.md
  scaffold: bin/run_bracket.py, bin/executor.py, tasks/add_two/
budget: getting low on session tokens (reason for this handoff)
```

## How to resume

1. Read this file, then `docs/PRD.md`, `CONTEXT.md`, and `docs/adr/0001..0006`.
2. Re-enter the pipeline via `/ask-kh` (or read
   `~/.claude/plugins/cache/killhouse/killhouse/0.1.1/skills/ask-kh/SKILL.md`). We are past
   triage/grill/to-prd; the pending action is the **post-PRD checkpoint decision**.
3. The last question put to the user was: **Continue** (spawn the spec-audit loop on the PRD) /
   **Autopilot** / **Revise PRD** / **Abort**. Resume by re-offering that choice, then on
   "Continue" run `loops/REVIEW_DOCUMENT` as a delegated subagent against `docs/PRD.md`,
   returning only the converged-PRD path + verdict (context hygiene: do not inline the audit).

## Decisions already made (ADR index)

- **0001** Python orchestrator — performance is LLM-bound (~100x), so language perf is moot;
  decisive reason is import-level reuse of killhouse's Python harness. (Rust-first was considered
  and rejected; that whole branch is closed.)
- **0002** routerescalation is the **data engine only**; router training + serving are a separate
  downstream repo; the **dataset is the interface**.
- **0003** Corpus = **real reverted-commit tasks** (revert a commit's impl, keep its tests, so the
  suite fails at baseline; task = make it pass), gated by the repo's **own test suite**, own repos
  first (shipsim / question2crux / killhouse / ...).
- **0004** Executor applies **whole-file blocks** (per changed file: path + full contents), not
  diffs — fairest to weak tiers. **ERROR (couldn't apply) is recorded distinctly from FAIL
  (applied but gate failed).**
- **0005** Tiers = **single-family capability ladder** (e.g. Qwen2.5-Coder 7B/14B/32B) for clean
  monotonic, interpretable "minimum viable tier".
- **0006** **Two-layer dataset**: raw append-only JSONL of (extended) killhouse delegation records
  per measurement + a derived **labels view** (min viable tier + per-tier verdict map).

## Repository state (IMPORTANT: nothing committed)

Everything below is **new, uncommitted** files in `/mnt/storage/git_home/routerescalation`
(`git status` will show them untracked). If resuming cleanly, consider committing the scaffold +
docs as the pass-1 baseline first (the user has not yet asked to commit).

Pass-1 scaffold (Python; already runs GREEN in mock):
- `bin/run_bracket.py` — runs one delegation record across all 3 tiers, prints the min-viable-tier
  bracket; imports `killhouse_gate_replay`; copy-based sandbox; runs the chosen (top) tier directly
  because gate-replay only replays LOWER tiers.
- `bin/executor.py` — generic OpenAI-compatible executor (stdlib only); overwrites one file from a
  fenced block; `--mock` uses fixtures. **PRD hardens this to multi-file whole-file for M1.**
- `tasks/add_two/` — toy gated task (src stub, `test_src.py` gate, golden/buggy mock fixtures,
  `record.json` killhouse delegation record). **Kept only as a smoke test per ADR-0003.**
- `.killhouse/config.json` — tracked tier map; **Fireworks ids are still FILL_ME placeholders.**
- `CONTEXT.md`, `docs/adr/0001..0006`, `docs/PRD.md`, `.gitignore`, `README.md`.

Prove the loop offline any time:
```bash
KILLHOUSE_ROOT=/home/chris/git_home/killhouse \
  bin/run_bracket.py --record tasks/add_two/record.json --mock
# expect: fast FAIL, standard PASS, reasoning PASS ; minimum viable tier: standard
```

## Milestone 1 (what the PRD scopes)

"First live bracket on a real task": one hand-prepared reverted-commit task, run live across the
Fireworks single-family ladder, hermetic `git_worktree_sandbox` pinned to the task SHA, gated by
the repo's real test suite, emitting a two-layer dataset row with ERROR != FAIL. Mining automation
= Milestone 2 (out of scope).

## Open questions / prerequisites (must resolve in PLAN or before the live run)

- **MATERIAL:** how do a real repo's **test dependencies** get into the hermetic worktree sandbox?
  De-risk M1 by choosing a first repo whose suite runs with no extra setup.
- Operational (user supplies): three real **Fireworks model ids** for the ladder + `FIREWORKS_API_KEY`;
  the first **source repo** to prepare a task from.
- Raw log location/keying and how re-run attempts are de-duplicated when deriving the labels view.
- Switch runner sandbox from copy-based to killhouse `git_worktree_sandbox` for real repos (needs
  the fixture committed so its SHA exists).

## Pointers

- killhouse repo: `/home/chris/git_home/killhouse` (Python harness reused).
  - `bin/killhouse_gate_replay.py` — replay harness (Executor + SandboxFactory are injected deps;
    replays only tiers BELOW `chosen_tier`).
  - `bin/killhouse_delegation_log.py` — stdlib schema validator (`validate_record`).
  - `schemas/delegation_record.schema.json` — record schema (`additionalProperties: true`, so we
    enrich rather than fork). Test prior art in `tests/` injects executor + sandbox_factory.
