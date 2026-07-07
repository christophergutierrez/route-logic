#!/usr/bin/env python3
"""Contract + integration tests for bin/run_bracket.py.

Covers the mock bracket + label, the run_bracket -> classify wiring via the
canonical --schema shape (C1), non-PASS diagnostics (R-EXEC-2), the gate
timeout (H2), loud live-config validation (R-EXEC-3), and the infra/model
fault split (R-EXEC-1). Runnable without pytest: python3 tests/test_run_bracket.py.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TASK = REPO / "tasks" / "add_two"
RECORD = TASK / "record.json"

os.environ.setdefault("KILLHOUSE_ROOT", str(Path.home() / "git_home" / "killhouse"))
sys.path.insert(0, str(REPO / "bin"))
import run_bracket  # noqa: E402


def _init_source_repo(base: Path):
    source = base / "source"
    source.mkdir()
    subprocess.run(["git", "-C", str(source), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Test User"], check=True)
    return source


def _commit_all(repo: Path, message: str):
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", message], check=True, capture_output=True)
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _run(extra_args, extra_env=None):
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(REPO / "bin" / "run_bracket.py"),
         "--record", str(RECORD), "--mock", *extra_args],
        capture_output=True, text=True, env=env,
    )


def test_mock_exit_and_label():
    proc = _run([])
    assert proc.returncode == 0, proc.stderr
    assert "minimum viable tier: standard" in proc.stdout


def test_schema_wiring_c1():
    # C1: run_bracket emits the exact shape classify consumes, and the derived
    # label matches. A passing bracket must never come back NONE.
    proc = _run(["--schema"])
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert isinstance(data["bracket"], list), "bracket must be the canonical list shape"
    by_tier = {r["tier"]: r for r in data["bracket"]}
    assert by_tier["fast"]["passed"] is False
    assert by_tier["standard"]["passed"] is True
    assert data["minimum_viable_tier"] == "standard"
    # Feeding the emitted bracket straight back through classify must agree.
    from classify import minimum_viable_tier
    assert minimum_viable_tier(data["bracket"]) == "standard"


def test_schema_diagnostics_on_fail():
    # R-EXEC-2: a non-PASS row must carry captured diagnostics, not be opaque.
    data = json.loads(_run(["--schema"]).stdout)
    fast = next(r for r in data["bracket"] if r["tier"] == "fast")
    assert fast.get("diagnostics"), "FAIL row must carry gate diagnostics"


def test_gate_timeout():
    # H2: a gate that hangs must be killed and recorded ERROR (unmeasured), not FAIL.
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    record["gate"]["command"] = "python3 -c \"import time; time.sleep(5)\""
    tmp_record = TASK / "_timeout_record.json"
    tmp_record.write_text(json.dumps(record), encoding="utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, str(REPO / "bin" / "run_bracket.py"),
             "--record", str(tmp_record), "--mock", "--schema"],
            capture_output=True, text=True,
            env={**os.environ, "RE_GATE_TIMEOUT": "1"},
        )
        assert proc.returncode == 0, proc.stderr
        data = json.loads(proc.stdout)
        verdicts = {r["verdict"] for r in data["bracket"]}
        assert verdicts == {"ERROR"}, f"expected all ERROR on timeout, got {verdicts}"
        assert data["minimum_viable_tier"] == "NONE"
    finally:
        tmp_record.unlink(missing_ok=True)


def test_loud_config_empty_base_url():
    # R-EXEC-3: empty base_url must fail loud, not silently ERROR every tier.
    try:
        run_bracket._require_live_config({"base_url": ""}, {})
    except SystemExit:
        return
    raise AssertionError("empty base_url must raise SystemExit")


def test_loud_config_missing_key():
    try:
        run_bracket._require_live_config(
            {"base_url": "https://x/v1", "api_key_env": "FIREWORKS_API_KEY"}, {})
    except SystemExit:
        return
    raise AssertionError("missing api key must raise SystemExit")


def test_loud_config_placeholder_model_id():
    try:
        run_bracket._require_live_config(
            {
                "base_url": "https://x/v1",
                "api_key_env": "FIREWORKS_API_KEY",
                "model_tiers": {
                    "fast": "accounts/fireworks/models/FILL_ME_fast",
                    "standard": "real-standard",
                    "reasoning": "real-reasoning",
                },
            },
            {"FIREWORKS_API_KEY": "secret"},
        )
    except SystemExit as exc:
        assert "placeholder" in str(exc)
        return
    raise AssertionError("placeholder model ids must raise SystemExit")


def test_error_split_infra_vs_model():
    # R-EXEC-1: exit 2 -> infra-fault, exit 1 -> model-fault, recorded distinctly.
    class _Exc(Exception):
        def __init__(self, rc):
            self.returncode = rc
            self.stderr = "boom"

    infra = run_bracket._error_result("fast", "m", _Exc(2))
    model = run_bracket._error_result("fast", "m", _Exc(1))
    assert infra["verdict"] == "ERROR" and infra["fault"] == "infra"
    assert model["verdict"] == "ERROR" and model["fault"] == "model"
    assert infra["diagnostics"] and model["diagnostics"]


def test_infra_fault_tiers_detected_before_emit():
    results = [
        {"tier": "fast", "verdict": "ERROR", "fault": "infra"},
        {"tier": "standard", "verdict": "ERROR", "fault": "model"},
        {"tier": "reasoning", "verdict": "PASS"},
    ]
    assert run_bracket.infra_fault_tiers(results) == ["fast"]


def test_worktree_factory_uses_source_repo_and_pinned_head():
    # inv-hermetic-pinned: worktree sandbox must be of the task source repo,
    # not route-logic or killhouse.
    base = Path(tempfile.mkdtemp(prefix="re-source-repo-"))
    try:
        source = _init_source_repo(base)
        (source / "marker.txt").write_text("source\n", encoding="utf-8")
        head = _commit_all(source, "fixture")
        record = json.loads(RECORD.read_text(encoding="utf-8"))
        record["upstream_artifacts"] = [{
            "kind": "repository_state",
            "pinned": {"head": head, "repo_root": str(source)},
        }]
        repo_root = run_bracket._resolve_source_repo(record, RECORD, None)
        assert repo_root == source.resolve()
        with run_bracket.worktree_sandbox_factory(repo_root)(record) as sandbox:
            run_bracket._assert_pinned_worktree(record, Path(sandbox))
            assert (Path(sandbox) / "marker.txt").read_text(encoding="utf-8") == "source\n"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_baseline_pass_refused_as_vacuous():
    # inv-baseline-fails: a gate that passes before model edits must not be measured.
    base = Path(tempfile.mkdtemp(prefix="re-vacuous-repo-"))
    try:
        source = _init_source_repo(base)
        (source / "marker.txt").write_text("source\n", encoding="utf-8")
        head = _commit_all(source, "fixture")
        record = json.loads(RECORD.read_text(encoding="utf-8"))
        record["upstream_artifacts"] = [{
            "kind": "repository_state",
            "pinned": {"head": head, "repo_root": str(source)},
        }]
        record["gate"]["cwd"] = "REPO_ROOT"
        record["gate"]["command"] = f"{sys.executable} -c 'raise SystemExit(0)'"
        try:
            run_bracket.verify_baseline_fails(record, run_bracket.worktree_sandbox_factory(source))
        except SystemExit as exc:
            assert "vacuous" in str(exc)
            return
        raise AssertionError("passing baseline gate must be refused")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"  ok  {t.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {t.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
