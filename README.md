# routerescalation

A data engine that labels coding tasks with the **minimum model tier** required to pass a
real test gate. Those labels — *task features → cheapest tier that actually works* — are
ground-truth training data for capability-aware routers, sourced from a falsifiable gate
rather than human preference.

## Why

Model routers are trained on preference signals: a human (or a stronger model) liked one
output over another. Preference tells you what *looked* good, not whether the model
actually *solved the problem*. A router optimized on preference can systematically
over-provision — sending easy tasks to expensive models — because it never measured
correctness.

routerescalation flips the signal source. Each task carries a **gate**: a real command with
a binary pass/fail exit (a test suite, a type check, a build). Run the task across a ladder
of model tiers — `fast` → `standard` → `reasoning` — and the **minimum viable tier** (the
cheapest tier whose output passes the gate) is a ground-truth capability label. Collect
enough of them and you have a corpus to train a router that routes on *whether the model can
do the task*, not whether a rater liked the answer.

## How it works

```
                    ┌──────────────────────────────────────────────┐
                    │  bin/run_bracket.py                          │
  record.json ───▶  │  for tier in [fast, standard, reasoning]:    │  ──▶  bracket
  (prompt + gate)   │    sandbox ─▶ executor ─▶ model ─▶ src.py    │       (per-tier
                    │    run gate ─▶ PASS / FAIL                   │        pass/fail)
                    └──────────────────────────────────────────────┘
                                                                    │
                                                                    ▼
                                                    bin/classify.py (pure)
                                                    lowest PASS tier = label
```

- **killhouse** provides the tier-routing and gate-replay machinery. This repo is the
  *consumer*: the runner puts `$KILLHOUSE_ROOT/bin` on `sys.path` and **imports**
  `killhouse_gate_replay` as a Python module, then calls its functions directly
  (`resolve_model`, `load_routing`, `command_executor`, the gate path). It runs in-process:
  no subprocess into killhouse and no external-tool flags; the experiment (config,
  fixtures, results) stays here and nothing is written into killhouse.
- The one piece killhouse ships *without* is an **executor**: the component that turns a
  prompt + model id into a candidate change. `bin/executor.py` is that piece: it calls an
  OpenAI-compatible endpoint and overwrites the target file(s). Deliberately dumb (no diff
  parsing) so pass 1 isolates "can this tier pass the gate" from "can I apply a patch."
- killhouse's gate-replay only runs tiers *below* a record's `chosen_tier` (its offline
  "guessed-too-high" calibration test). A full bracket needs all three, so the runner
  measures the chosen tier through the same sandbox + gate itself.

## Quick start

The offline mock proves the whole loop with no network and no API key — `fast` writes a
wrong answer (FAIL), `standard`/`reasoning` write the right one (PASS):

```bash
bin/run_bracket.py --record tasks/add_two/record.json --mock
```

Expected output (model ids come from `.killhouse/config.json`; the `FILL_ME_*` placeholders are the unfilled defaults):

```
  delegation : toy-add-two-001
  gate       : python3 test_src.py  (cwd tasks/add_two)

  tier        verdict             exit  model
  ------------------------------------------------------------
  fast        FAIL                1     accounts/fireworks/models/FILL_ME_fast
  standard    PASS                0     accounts/fireworks/models/FILL_ME_standard
  reasoning   PASS                0     accounts/fireworks/models/FILL_ME_reasoning
  ------------------------------------------------------------
  minimum viable tier: standard
```

## Live runs

Against Fireworks (or any OpenAI-compatible `base_url`):

```bash
# 1. Fill the three model ids in .killhouse/config.json (accounts/fireworks/models/...)
# 2. Export your key or put `export FIREWORKS_API_KEY=...` in a local .env
export FIREWORKS_API_KEY=fw_...
# 3. Use the first real source-repo fixture
bin/run_bracket.py --record tasks/killhouse_probe_slugify/record.json --emit
```

Live runs default to killhouse's pinned `git_worktree_sandbox`, and refuse a task whose gate
already passes at baseline. The `killhouse_probe_slugify` fixture points at the sibling
`killhouse` checkout through its `repository_state.repo_root`; use `--repo-root /path/to/repo`
to override that if your checkout lives elsewhere. `KILLHOUSE_ROOT` defaults to
`~/git_home/killhouse`; override it if killhouse lives elsewhere.

## Project structure

```
bin/
  run_bracket.py    runner: replays one record across all three tiers, prints the bracket
  executor.py       the piece killhouse lacks: prompt -> model -> overwrite one file
  classify.py       pure reducer: bracket -> minimum viable tier label (no I/O, no network)
tasks/
  add_two/          a toy gated task
    src.py            failing stub (baseline: gate fails)
    test_src.py       the gate (zero-dep; `python3 test_src.py`)
    golden.py         correct impl (mock fixture for standard/reasoning)
    buggy.py          wrong impl (mock fixture for fast)
    record.json       killhouse delegation record: prompt + gate + pinned repo state
  killhouse_probe_slugify/
    record.json       first real source-repo fixture; baseline gate fails at pinned killhouse SHA
.killhouse/
  config.json       TRACKED tier map: which concrete model sits at fast/standard/reasoning
docs/               PRD and ADRs (design rationale)
CONTEXT.md          glossary — the ubiquitous language for the project
```

## Key concepts

| Term | Meaning |
|------|---------|
| **Tier** | An abstract capability level (`fast` \| `standard` \| `reasoning`) mapping to one concrete model. |
| **Minimum viable tier** | The smallest-capability tier whose output passes the gate. The core label. |
| **Gate** | A falsifiable, binary check (command + exit code) that judges correctness. Must fail at baseline or the label is meaningless. |
| **Bracket** | The set of per-tier pass/fail verdicts for one task. The label is read off the bracket. |
| **Delegation record** | killhouse's `record.json`: prompt, chosen tier, gate, pinned repo state. |
| **Executor** | prompt + model id → candidate change applied in a sandbox. The piece killhouse doesn't ship. |

Full glossary in [`CONTEXT.md`](CONTEXT.md); design rationale in [`docs/adr/`](docs/adr/).

## Design decisions

- **Config is tracked here, not in killhouse.** The tier → model map *is* the experimental
  variable, so it belongs with the experiment. It carries no secrets (`api_key_env` names
  the env var; the token stays in your environment). Personal overrides go in the
  git-ignored `.killhouse/config.local.json`.
- **The executor is deliberately dumb.** It overwrites whole files from path-tagged fenced
  code blocks — no diff parsing. Pass 1 isolates "can this tier pass the gate" from
  "can I apply a patch." Written provider-generic, it's a clean candidate to upstream into
  killhouse later.
- **Live tasks use a pinned git worktree.** The runner keeps the copy sandbox for the toy
  mock, but live runs use killhouse's `git_worktree_sandbox` at the recorded
  `repository_state.head`, with the source repo supplied by `--repo-root` or the record.
- **`record.json.outcome` is inert scaffolding.** killhouse's schema requires an `outcome`,
  but gate-replay never trusts it — it computes its own verdict from the real gate. The
  committed value only satisfies validation.
- **ERROR ≠ FAIL.** ERROR means the harness couldn't apply the model's output (unmeasured);
  FAIL means the applied output didn't pass the gate (the tier genuinely failed). They are
  recorded distinctly — conflating them corrupts labels.

## Roadmap

1. Fill real Fireworks model ids and prepare the first real reverted-commit fixture.
2. Run the live tracer with `--emit` to write `runs/measurements.jsonl`.
3. Add more real reverted-commit tasks.
4. Feature extraction + a learned router.

## License

MIT
