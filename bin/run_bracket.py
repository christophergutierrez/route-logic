#!/usr/bin/env python3
"""Run one delegation record across all three tiers and print the min-viable-tier bracket.

This is routerescalation's pass-1 runner. It does NOT reimplement killhouse; it reuses
killhouse's gate-replay harness (bin/killhouse_gate_replay.py) for record validation,
model resolution, the executor template, and the real-gate contract. The single thing it
adds is running *all three* tiers against the same sandbox + gate, because the harness on
its own only replays tiers *below* the record's chosen_tier (its offline "guessed-too-high"
calibration test). The full bracket needs the chosen tier measured too.

Sandbox: copy-based (a throwaway temp dir with the task subtree copied in), so pass 1 runs
on a dirty working tree with no commit required. Once fixtures are committed and you want
hermetic pinned-SHA replay, swap in killhouse's git_worktree_sandbox — the record already
pins a repository_state head for exactly that.

Usage:
  bin/run_bracket.py --record tasks/add_two/record.json --mock   # offline plumbing proof
  FIREWORKS_API_KEY=... bin/run_bracket.py --record tasks/add_two/record.json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

RR = Path(__file__).resolve().parents[1]
KH_ROOT = Path(os.environ.get("KILLHOUSE_ROOT", Path.home() / "git_home" / "killhouse"))
sys.path.insert(0, str(KH_ROOT / "bin"))
try:
    import killhouse_gate_replay as gr  # noqa: E402
except ModuleNotFoundError:
    sys.exit(f"cannot import killhouse gate-replay harness under {KH_ROOT}; set KILLHOUSE_ROOT")

TIERS = ["fast", "standard", "reasoning"]  # ascending capability; first PASS is the minimum


def copy_sandbox_factory(repo_root: Path, rel: str):
    """A gate_replay-compatible sandbox factory that copies the task subtree into a temp dir.

    Mirrors the layout the real gate expects: sandbox/<rel>/... so gate.cwd (== rel) and
    RE_TARGET_FILE (== rel/src.py) both resolve exactly as under repo_root.
    """

    @contextmanager
    def factory(_record):
        base = Path(tempfile.mkdtemp(prefix="re-bracket-"))
        try:
            shutil.copytree(repo_root / rel, base / rel)
            yield base
        finally:
            shutil.rmtree(base, ignore_errors=True)

    return factory


def run_tier(record, tier, routing, executor, factory):
    """Run one tier through the sandbox + real gate. Returns a verdict dict."""
    model = gr.resolve_model(routing, tier)
    if model is None:
        return {"tier": tier, "model": None, "verdict": "SKIPPED_NO_ROUTING", "gate_exit": None}
    os.environ["RE_TIER"] = tier  # the mock executor keys off this; harmless in live mode
    gate = record["gate"]
    try:
        with factory(record) as sandbox:
            sandbox = Path(sandbox)
            gr._materialize_pinned_artifacts(record, sandbox)
            try:
                executor(record["resolved_prompt"], model, sandbox)
            except subprocess.CalledProcessError as exc:
                return {"tier": tier, "model": model, "verdict": "ERROR",
                        "gate_exit": None, "reason": f"executor exit {exc.returncode}"}
            proc = subprocess.run(
                gate["command"], shell=True,
                cwd=str(gr._resolve_cwd(sandbox, gate["cwd"])),
                capture_output=True, text=True,
            )
    except Exception as exc:  # sandbox/gate infra failure, not a gate result
        return {"tier": tier, "model": model, "verdict": "ERROR", "gate_exit": None, "reason": str(exc)}
    return {"tier": tier, "model": model,
            "verdict": "PASS" if proc.returncode == 0 else "FAIL", "gate_exit": proc.returncode}


def print_bracket(record, results, min_tier):
    print(f"\n  delegation : {record['delegation_id']}")
    print(f"  gate       : {record['gate']['command']}  (cwd {record['gate']['cwd']})")
    print(f"\n  {'tier':<12}{'verdict':<20}{'exit':<6}model")
    print("  " + "-" * 60)
    for r in results:
        exit_s = "" if r["gate_exit"] is None else str(r["gate_exit"])
        note = f"   ({r['reason']})" if r.get("reason") else ""
        print(f"  {r['tier']:<12}{r['verdict']:<20}{exit_s:<6}{r['model'] or ''}{note}")
    print("  " + "-" * 60)
    print(f"  minimum viable tier: {min_tier or 'NONE PASSED'}\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--record", type=Path, required=True)
    ap.add_argument("--mock", action="store_true", help="offline: use fixtures instead of a live model")
    args = ap.parse_args(argv)

    record = json.loads(args.record.read_text())
    errors = gr.dl.validate_record(record)
    if errors:
        sys.exit(f"[fail] record fails killhouse schema: {errors[0]}")

    routing = gr.load_routing(RR)
    if not routing.get("model_tiers"):
        sys.exit("[fail] no model_tiers in .killhouse/config.json (fill it from config.example.json)")

    task_dir = args.record.resolve().parent
    rel = os.path.relpath(task_dir, RR)

    os.environ["RE_EXECUTOR"] = str(RR / "bin" / "executor.py")
    os.environ.setdefault("RE_TARGET_FILE", f"{rel}/src.py")

    if args.mock:
        os.environ["RE_GOLDEN"] = str(task_dir / "golden.py")
        os.environ["RE_BUGGY"] = str(task_dir / "buggy.py")
        template = ('python3 "$RE_EXECUTOR" --mock --model {model} '
                    "--workdir {workdir} --prompt-file {prompt_file}")
    else:
        base_url = routing.get("base_url", "")
        if not base_url:
            # R-EXEC-3: an empty base_url silently becomes ERROR-for-all-tiers (every
            # tier's model call fails at the transport layer), which the dataset would
            # record as "unmeasured" when the run is actually invalid. Fail loud instead.
            sys.exit("[fail] routing.base_url is empty (needed for live model calls)")
        os.environ["RE_BASE_URL"] = base_url
        key_env = routing.get("api_key_env", "FIREWORKS_API_KEY")
        key = os.environ.get(key_env)
        if not key:
            sys.exit(f"[fail] ${key_env} is not set (needed for live model calls)")
        os.environ["RE_API_KEY"] = key
        template = routing.get("replay_executor") or (
            'python3 "$RE_EXECUTOR" --model {model} --workdir {workdir} --prompt-file {prompt_file}')

    executor = gr.command_executor(template)
    factory = copy_sandbox_factory(RR, rel)

    results = [run_tier(record, t, routing, executor, factory) for t in TIERS]
    passing = {r["tier"] for r in results if r["verdict"] == "PASS"}
    min_tier = next((t for t in TIERS if t in passing), None)
    print_bracket(record, results, min_tier)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
