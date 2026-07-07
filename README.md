# routerescalation

Turn [killhouse](https://github.com/christophergutierrez/killhouse)'s tier-routing +
gate-replay machinery into a **data engine**: run real, gated coding tasks across model
tiers and record the **minimum viable tier** for each. That labeled corpus
(`task features -> smallest tier that passed a real gate`) is the substrate for a learned
model router — a signal preference-trained routers don't have, because our labels come
from a real pass/fail gate, not human preference.

killhouse is the mechanism; this repo is the consumer. **Nothing here is written into
killhouse** — the runner invokes killhouse's `bin/killhouse_gate_replay.py` as an external
tool via `--repo-root` pointing here, so the experiment (config, fixtures, results) stays
self-contained and reproducible.

## Pass 1 (this repo, now)

One toy task, three tiers, one PASS/FAIL bracket — the smallest thing that produces the asset.

```
bin/run_bracket.py         # runs all three tiers, prints the min-viable-tier bracket
bin/executor.py            # the piece killhouse's gate-replay harness ships WITHOUT:
                           #   prompt -> OpenAI-compatible model -> overwrite one file
tasks/add_two/             # a toy gated task
  src.py                   #   failing stub (baseline: gate fails)
  test_src.py              #   the gate (zero-dep; `python3 test_src.py`)
  golden.py / buggy.py     #   mock fixtures (offline proof of the loop)
  record.json              #   killhouse delegation record: prompt + gate + pinned repo state
.killhouse/config.json     # TRACKED tier map: which Fireworks model is fast/standard/reasoning
```

### Run it

Offline plumbing proof (no network, no key) — should show fast FAIL, standard/reasoning PASS:

```bash
bin/run_bracket.py --record tasks/add_two/record.json --mock
```

Live, against Fireworks:

```bash
# 1. Fill the three model ids in .killhouse/config.json (accounts/fireworks/models/...)
# 2. Export your key (its NAME is in config.api_key_env; the token never lands in git)
export FIREWORKS_API_KEY=fw_...
bin/run_bracket.py --record tasks/add_two/record.json
```

`KILLHOUSE_ROOT` defaults to `~/git_home/killhouse`; override it if killhouse lives elsewhere.

## Design decisions worth knowing

- **Config is tracked here, not in killhouse.** The tier -> concrete-model map is *the
  experimental variable*, so it belongs with the experiment. It carries no secrets
  (`api_key_env` names the env var; the token stays in your environment). A personal
  override goes in the git-ignored `.killhouse/config.local.json`.
- **The executor is deliberately dumb.** It overwrites a single target file with the
  model's fenced code block — no diff parsing. Pass 1 isolates "can this tier pass the
  gate" from "can I apply a patch". If written provider-generic (any OpenAI-compatible
  `base_url`), it's a clean thing to upstream into killhouse later, which needs one.
- **Sandbox is copy-based, not git.** The runner copies the task subtree into a temp dir,
  so pass 1 runs on a dirty tree with no commit. `record.json` still pins a
  `repository_state` head (`"HEAD"`) so that switching to killhouse's hermetic
  pinned-SHA `git_worktree_sandbox` is a one-line change once fixtures are committed.
- **`record.json.outcome` is inert scaffolding.** killhouse's schema requires an
  `outcome`, but gate-replay never trusts it — it computes its own verdict from the real
  gate. The committed value only satisfies validation.
- **The chosen tier is measured directly.** killhouse's gate-replay only runs tiers
  *below* `chosen_tier` (its offline "guessed-too-high" test). The bracket needs all three,
  so the runner runs the chosen tier through the same sandbox + gate itself.

## Next steps (not pass 1)

1. Swap the mock for real Fireworks ids and confirm a live bracket.
2. Add more tasks; a task is a dir with `src.py` + `test_src.py` + `record.json`.
3. Persist brackets to `runs/` and start the `(features -> min tier)` table.
4. Only then: feature extraction + a learned router.
