#!/usr/bin/env python3
"""Contract tests for real-source task fixtures.

Runnable without pytest: python3 tests/test_real_fixtures.py.
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
REAL_RECORDS = sorted((REPO / "tasks").glob("killhouse_probe_*/record.json"))

os.environ.setdefault("KILLHOUSE_ROOT", str(Path.home() / "git_home" / "killhouse"))
sys.path.insert(0, str(REPO / "bin"))
import run_bracket  # noqa: E402

KH_ROOT = Path(os.environ.get("KILLHOUSE_ROOT", Path.home() / "git_home" / "killhouse"))
sys.path.insert(0, str(KH_ROOT / "bin"))
import killhouse_delegation_log as kdl  # noqa: E402


def _load_record(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_real_fixture_records_schema_valid():
    assert REAL_RECORDS, "expected at least one real fixture record"
    for record_path in REAL_RECORDS:
        record = _load_record(record_path)
        errors = kdl.validate_record(record)
        assert not errors, f"{record_path}: {errors}"
        assert "path:bin/" in record["resolved_prompt"], record_path


def test_real_fixture_source_repos_and_pins_resolve():
    for record_path in REAL_RECORDS:
        record = _load_record(record_path)
        source = run_bracket._resolve_source_repo(record, record_path, None)
        assert source.name == "killhouse", source
        head = run_bracket._pinned_head(record)
        subprocess.run(
            ["git", "-C", str(source), "cat-file", "-e", f"{head}^{{commit}}"],
            check=True,
            capture_output=True,
            text=True,
        )


def test_real_fixture_baselines_fail_without_worktree_write():
    """Prove baseline polarity without mutating the source repo's git metadata.

    Production live runs use git_worktree_sandbox. This test uses git archive
    because the execution sandbox exposes sibling repo .git dirs as read-only.
    The invariant is the same: at the pinned source tree, before model edits,
    the gate exits non-zero after pinned artifacts are materialized.
    """
    for record_path in REAL_RECORDS:
        record = _load_record(record_path)
        source = run_bracket._resolve_source_repo(record, record_path, None)
        head = run_bracket._pinned_head(record)
        tmp = Path(tempfile.mkdtemp(prefix="re-real-fixture-"))
        try:
            archive = subprocess.Popen(
                ["git", "-C", str(source), "archive", "--format=tar", head],
                stdout=subprocess.PIPE,
            )
            subprocess.run(["tar", "-xf", "-", "-C", str(tmp)], stdin=archive.stdout, check=True)
            if archive.stdout is not None:
                archive.stdout.close()
            if archive.wait() != 0:
                raise AssertionError("git archive failed")
            run_bracket.gr._materialize_pinned_artifacts(record, tmp)
            proc = subprocess.run(
                record["gate"]["command"],
                shell=True,
                cwd=str(run_bracket.gr._resolve_cwd(tmp, record["gate"]["cwd"])),
                capture_output=True,
                text=True,
                timeout=run_bracket.GATE_TIMEOUT,
            )
            assert proc.returncode != 0, f"{record_path}: baseline gate must fail before model edits"
            assert "_probe_" in (proc.stderr + proc.stdout), record_path
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def _main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"  ok  {test.__name__}")
        except Exception as exc:  # noqa: BLE001 - report any failure, keep going
            failures += 1
            print(f"FAIL  {test.__name__}: {exc!r}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
