#!/usr/bin/env python3
"""Run one delegation record across all three tiers and print the min-viable-tier bracket.

This is routerescalation's pass-1 runner. It does NOT reimplement killhouse; it reuses
killhouse's gate-replay harness (bin/killhouse_gate_replay.py) for record validation,
model resolution, the executor template, and the real-gate contract. The single thing it
adds is running *all three* tiers against the same sandbox + gate, because the harness on
its own only replays tiers *below* the record's chosen_tier (its offline "guessed-too-high"
calibration test). The full bracket needs the chosen tier measured too.

Output schema (invariant C1): every row in ``results`` is a dict with keys
``tier``, ``model``, ``verdict``, ``gate_exit``; verdicts are one of
``PASS``, ``FAIL``, ``ERROR``, ``SKIPPED_NO_ROUTING``.  The minimum viable
tier is computed by ``classify.minimum_viable_tier`` which guarantees
strict-true pass semantics and a ``NONE`` fallback.

Sandbox: copy-based (a throwaway temp dir with the task subtree copied in), so pass 1 runs
on a dirty working tree with no commit required. Once fixtures are committed and you want
hermetic pinned-SHA replay, swap in killhouse's git_worktree_sandbox; the record already
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

# Wires classify.py into the runner (C1 unification).
sys.path.insert(0, str(RR / "bin"))
try:
    from classify import minimum_viable_tier as mvt  # noqa: E402
except ModuleNotFoundError:
    sys.exit("[fail] bin/classify.py not found under repo root")

KH_ROOT = Path(os.environ.get("KILLHOUSE_ROOT", Path.home() / "git_home" / "killhouse"))
sys.path.insert(0, str(KH_ROOT / "bin"))
try:
    import killhouse_gate_replay as gr  # noqa: E402
except ModuleNotFoundError:
    sys.exit(f"cannot import killhouse gate-replay harness under {KH_ROOT}; set KILLHOUSE_ROOT")

TIERS = ["fast", "standard", "reasoning"]  # ascending capability (ADR-0005)

# H2: the gate runs code the model just wrote; without a bound an infinite loop
# or blocking read hangs the whole run. Configurable so tests can force a trip.
GATE_TIMEOUT = int(os.environ.get("RE_GATE_TIMEOUT", "120"))

_DIAG_TAIL = 500  # chars of captured stderr kept on a non-PASS result (R-EXEC-2)

# BR-1: the raw measurement log is a single append-only JSONL keyed by
# (task_id, tier, model_id, attempt_ordinal). The default lives under the
# gitignored runs/ dir, but is overridable (--out / $RE_MEASUREMENT_LOG) so
# tests write to temp files and never pollute the default log.
DEFAULT_MEASUREMENT_LOG = RR / "runs" / "measurements.jsonl"


def _resolve_measurement_log(out):
    """Resolve the raw-log path: --out, then $RE_MEASUREMENT_LOG, then default."""
    if out is not None:
        return Path(out)
    env = os.environ.get("RE_MEASUREMENT_LOG")
    if env:
        return Path(env)
    return DEFAULT_MEASUREMENT_LOG


def _load_log_records(log_path):
    """Parse an existing measurement log into records, empty if absent."""
    log_path = Path(log_path)
    if not log_path.is_file():
        return []
    records = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def _next_attempt_ordinal(records, task_id, tier):
    """BR-1: the next attempt_ordinal for (task_id, tier), 0 on a fresh key."""
    ordinals = [
        r["attempt_ordinal"] for r in records
        if r.get("task_id") == task_id and r.get("tier") == tier
        and isinstance(r.get("attempt_ordinal"), int) and not isinstance(r["attempt_ordinal"], bool)
    ]
    return (max(ordinals) + 1) if ordinals else 0


def _enriched_record(base_record, task_id, tier, result, attempt_ordinal):
    """Enrich the task's delegation record with one tier x attempt measurement.

    The killhouse schema declares additionalProperties:true, so we extend the
    record rather than fork it (ADR-0006). The tri-state PASS/FAIL/ERROR lives
    in the ``re_outcome`` enrichment field; ``outcome.status`` is kept in the
    schema's ``pass``/``fail`` enum so every appended record still validates.
    """
    rec = json.loads(json.dumps(base_record))  # deep copy; never mutate the input
    verdict = result.get("verdict")
    rec["chosen_tier"] = tier
    rec["outcome"] = {"status": "pass" if verdict == "PASS" else "fail", "escalated": False}
    rec["task_id"] = task_id
    rec["tier"] = tier
    rec["model_id"] = result.get("model")
    rec["attempt_ordinal"] = attempt_ordinal
    rec["re_outcome"] = verdict
    rec["gate_exit"] = result.get("gate_exit")
    for key in ("fault", "reason", "diagnostics"):
        if result.get(key):
            rec[key] = result[key]
    return rec


def emit_measurements(log_path, base_record, task_id, results):
    """Append one enriched, schema-valid measurement per tier to the raw log.

    BR-1: append-only JSONL, one row per (task_id, tier, model_id,
    attempt_ordinal). Every row is validated with killhouse's validator before
    write; an invalid row aborts loudly rather than corrupting the log.
    """
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    known = _load_log_records(log_path)
    appended = []
    for result in results:
        tier = result["tier"]
        ordinal = _next_attempt_ordinal(known + appended, task_id, tier)
        rec = _enriched_record(base_record, task_id, tier, result, ordinal)
        errors = gr.dl.validate_record(rec)
        if errors:
            raise SystemExit(f"[fail] enriched measurement record invalid: {errors[0]}")
        appended.append(rec)
    with log_path.open("a", encoding="utf-8") as fh:
        for rec in appended:
            fh.write(json.dumps(rec) + "\n")
    return appended


def _error_result(tier, model, exc):
    """Map an executor CalledProcessError to a distinct-fault ERROR result.

    R-EXEC-1: exit 2 is an infra-fault (auth/rate-limit/5xx/network: the tier
    was never measured), exit 1 is a model-fault (the model produced no
    applicable output: a real signal about that tier). They are recorded
    distinctly so an infra blip is not mistaken for a capability failure.
    """
    fault = "infra" if getattr(exc, "returncode", None) == 2 else "model"
    stderr = getattr(exc, "stderr", None) or getattr(exc, "output", None) or ""
    diag = str(stderr)[-_DIAG_TAIL:] or f"executor exit {getattr(exc, 'returncode', '?')}"
    return {"tier": tier, "model": model, "verdict": "ERROR", "gate_exit": None,
            "fault": fault, "reason": f"{fault}-fault: executor exit {getattr(exc, 'returncode', '?')}",
            "diagnostics": diag}


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
                return _error_result(tier, model, exc)
            try:
                proc = subprocess.run(
                    gate["command"], shell=True,
                    cwd=str(gr._resolve_cwd(sandbox, gate["cwd"])),
                    capture_output=True, text=True, timeout=GATE_TIMEOUT,
                )
            except subprocess.TimeoutExpired:
                # H2: the model's code hung the gate. Unmeasured, not a FAIL.
                return {"tier": tier, "model": model, "verdict": "ERROR", "gate_exit": None,
                        "fault": "gate", "reason": f"gate timeout ({GATE_TIMEOUT}s)",
                        "diagnostics": f"gate exceeded {GATE_TIMEOUT}s and was killed"}
    except Exception as exc:  # sandbox/gate infra failure, not a gate result
        return {"tier": tier, "model": model, "verdict": "ERROR", "gate_exit": None,
                "fault": "infra", "reason": str(exc), "diagnostics": str(exc)[-_DIAG_TAIL:]}
    passed = proc.returncode == 0
    result = {"tier": tier, "model": model,
              "verdict": "PASS" if passed else "FAIL", "gate_exit": proc.returncode}
    if not passed:
        # R-EXEC-2: keep the gate's own diagnostics so a FAIL on a big sweep is
        # not opaque. Prefer stderr; fall back to stdout tail.
        tail = (proc.stderr or proc.stdout or "")[-_DIAG_TAIL:]
        result["diagnostics"] = tail or "gate exited non-zero with no output"
    return result


def _require_live_config(routing, env):
    """R-EXEC-3: validate live config loud before any tier runs.

    An empty base_url or a missing API key would otherwise make every tier's
    model call fail at the transport layer and be recorded as ERROR
    (unmeasured) when the run is actually invalid. Fail loud instead. Returns
    ``(base_url, api_key_env, api_key)``; raises ``SystemExit`` on bad config.
    """
    base_url = routing.get("base_url", "")
    if not base_url:
        raise SystemExit("[fail] routing.base_url is empty (needed for live model calls)")
    key_env = routing.get("api_key_env", "FIREWORKS_API_KEY")
    key = env.get(key_env)
    if not key:
        raise SystemExit(f"[fail] ${key_env} is not set (needed for live model calls)")
    return base_url, key_env, key


def _to_bracket(results):
    """Project the runner's per-tier results into the canonical --schema shape.

    One list of ``{tier, model, verdict, passed, gate_exit}`` objects. ``passed``
    is the single derived truth field the labeler reads; only ``PASS`` sets it
    true, so ERROR/SKIPPED count as not-passing (unmeasured is not incapable).
    """
    return [{"tier": r["tier"], "model": r["model"], "verdict": r["verdict"],
             "passed": r["verdict"] == "PASS", "gate_exit": r["gate_exit"]}
            for r in results]


def bracket_to_label(results):
    """Reduce results to a minimum viable tier label using classify.

    Feeds the canonical list shape into ``classify.minimum_viable_tier`` so the
    runner and labeler share one input contract (the pass-1 schema-mismatch fix).
    Returns a str label (one of the tier names or "NONE").
    """
    return mvt(_to_bracket(results))


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
    print(f"  minimum viable tier: {min_tier or 'NONE'}\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--record", type=Path, required=True)
    ap.add_argument("--mock", action="store_true", help="offline: use fixtures instead of a live model")
    ap.add_argument("--schema", action="store_true",
                    help="print the bracket as JSON (the unified schema from classify) to stdout")
    ap.add_argument("--emit", action="store_true",
                    help="append one enriched measurement record per tier to the raw log (BR-1)")
    ap.add_argument("--out", "--measurement-log", dest="out", type=Path, default=None,
                    help="raw measurement-log path (JSONL); implies --emit. Overrides "
                         "$RE_MEASUREMENT_LOG and the default runs/measurements.jsonl")
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
        base_url, _key_env, key = _require_live_config(routing, os.environ)
        os.environ["RE_BASE_URL"] = base_url
        os.environ["RE_API_KEY"] = key
        template = routing.get("replay_executor") or (
            'python3 "$RE_EXECUTOR" --model {model} --workdir {workdir} --prompt-file {prompt_file}')

    executor = gr.command_executor(template)
    factory = copy_sandbox_factory(RR, rel)

    results = [run_tier(record, t, routing, executor, factory) for t in TIERS]

    # C1 unification: use classify.minimum_viable_tier instead of ad-hoc logic.
    min_tier = bracket_to_label(results)

    # BR-1: persist the raw measurement layer only when explicitly asked, so the
    # pure --mock / --schema behaviors are unchanged by default.
    if args.emit or args.out is not None:
        log_path = _resolve_measurement_log(args.out)
        task_id = record.get("task_id") or record["delegation_id"]
        emit_measurements(log_path, record, task_id, results)

    if args.schema:
        # Output the one canonical schema: the list of per-tier rows classify
        # consumes, plus the derived label. Diagnostics ride along on non-PASS.
        bracket = _to_bracket(results)
        for row, r in zip(bracket, results):
            if r.get("diagnostics"):
                row["diagnostics"] = r["diagnostics"]
        json.dump({"bracket": bracket, "minimum_viable_tier": min_tier}, sys.stdout)
        sys.stdout.write("\n")
    else:
        print_bracket(record, results, min_tier)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())