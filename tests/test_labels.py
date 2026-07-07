#!/usr/bin/env python3
"""Tests for the two-layer dataset: raw measurement log -> labels view.

Runnable without pytest: python3 tests/test_labels.py. ASCII only, plain
assert, nonzero exit on failure.

Invariant coverage:
  - inv-labels-contract  : the labels-view field set matches the schema file
                           (test_field_set_contract) plus all-pass/mid-ladder/
                           no-pass edge cases.
  - inv-error-unmeasured : an ERROR/SKIPPED-only tier is 'unmeasured' and
                           excluded from the label; only measured FAIL is a
                           non-pass (test_error_only_tier_unmeasured,
                           test_skipped_only_tier_unmeasured, test_all_error_none).
  - BR-2                 : latest-attempt-wins (test_latest_attempt_wins) and a
                           flaky marker on disagreeing measured attempts
                           (test_flaky_marker), with ERROR attempts not counted
                           as disagreement (test_error_attempt_not_disagreement).
  - inv-two-layer        : write raw JSONL rows, load, derive, assert; and
                           re-derivation is deterministic (test_roundtrip_rederive);
                           run_bracket --emit appends 3 schema-valid records that
                           derive to minimum_viable_tier 'standard'
                           (test_emit_and_derive_integration).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "bin"))
import derive_labels  # noqa: E402

KH_ROOT = Path(os.environ.get("KILLHOUSE_ROOT", Path.home() / "git_home" / "killhouse"))
sys.path.insert(0, str(KH_ROOT / "bin"))
import killhouse_delegation_log as kdl  # noqa: E402


def _raw(task_id, tier, outcome, ordinal=0, model=None):
    """A minimal raw measurement row (the fields the deriver reads)."""
    return {"task_id": task_id, "tier": tier, "re_outcome": outcome,
            "attempt_ordinal": ordinal, "model_id": model or ("model-" + tier)}


def _one(labels, task_id):
    return next(lab for lab in labels if lab["task_id"] == task_id)


def _derive(records):
    return derive_labels.derive_labels(records)


def test_field_set_contract():
    # inv-labels-contract: the label carries exactly the schema's required
    # fields (plus at most the optional 'flaky'), and no stray keys.
    recs = [_raw("t", "fast", "FAIL"), _raw("t", "standard", "PASS"),
            _raw("t", "reasoning", "PASS")]
    label = _one(_derive(recs), "t")
    schema = json.loads((REPO / "schemas" / "labels_view.schema.json").read_text(encoding="utf-8"))
    required = set(schema["required"])
    allowed = set(schema["properties"].keys())
    assert required == {"task_id", "minimum_viable_tier", "per_tier_verdicts", "tier_model_map"}, required
    assert required.issubset(label.keys()), label
    assert set(label.keys()).issubset(allowed), label


def test_all_pass_is_fast():
    recs = [_raw("t", "fast", "PASS"), _raw("t", "standard", "PASS"),
            _raw("t", "reasoning", "PASS")]
    label = _one(_derive(recs), "t")
    assert label["minimum_viable_tier"] == "fast"
    assert label["per_tier_verdicts"] == {"fast": "PASS", "standard": "PASS", "reasoning": "PASS"}


def test_mid_ladder_pass():
    recs = [_raw("t", "fast", "FAIL"), _raw("t", "standard", "PASS"),
            _raw("t", "reasoning", "PASS")]
    label = _one(_derive(recs), "t")
    assert label["minimum_viable_tier"] == "standard"
    assert label["per_tier_verdicts"]["fast"] == "FAIL"


def test_no_pass_is_none():
    recs = [_raw("t", "fast", "FAIL"), _raw("t", "standard", "FAIL"),
            _raw("t", "reasoning", "FAIL")]
    label = _one(_derive(recs), "t")
    assert label["minimum_viable_tier"] == "NONE"


def test_error_only_tier_unmeasured():
    # inv-error-unmeasured: fast is ERROR-only -> unmeasured, excluded from the
    # label (not a measured non-pass); standard PASS still wins.
    recs = [_raw("t", "fast", "ERROR"), _raw("t", "standard", "PASS"),
            _raw("t", "reasoning", "PASS")]
    label = _one(_derive(recs), "t")
    assert label["minimum_viable_tier"] == "standard"
    assert label["per_tier_verdicts"]["fast"] == "unmeasured"
    assert label["per_tier_verdicts"]["fast"] != "FAIL"


def test_skipped_only_tier_unmeasured():
    # SKIPPED_NO_ROUTING is unmeasured just like ERROR.
    recs = [_raw("t", "fast", "SKIPPED_NO_ROUTING"), _raw("t", "standard", "PASS"),
            _raw("t", "reasoning", "PASS")]
    label = _one(_derive(recs), "t")
    assert label["minimum_viable_tier"] == "standard"
    assert label["per_tier_verdicts"]["fast"] == "unmeasured"


def test_all_error_none():
    # A task whose only outcomes are ERROR has no measured pass -> NONE, and
    # every tier is unmeasured (none recorded as an incapable FAIL).
    recs = [_raw("t", "fast", "ERROR"), _raw("t", "standard", "ERROR"),
            _raw("t", "reasoning", "ERROR")]
    label = _one(_derive(recs), "t")
    assert label["minimum_viable_tier"] == "NONE"
    assert set(label["per_tier_verdicts"].values()) == {"unmeasured"}


def test_latest_attempt_wins():
    # BR-2: an earlier PASS followed by a later FAIL resolves to FAIL, proving
    # the rule is latest-wins, not any-PASS-wins. So standard drops out and the
    # label falls to reasoning.
    recs = [_raw("t", "standard", "PASS", ordinal=0),
            _raw("t", "standard", "FAIL", ordinal=1),
            _raw("t", "fast", "FAIL"), _raw("t", "reasoning", "PASS")]
    label = _one(_derive(recs), "t")
    assert label["per_tier_verdicts"]["standard"] == "FAIL"
    assert label["minimum_viable_tier"] == "reasoning"


def test_flaky_marker():
    # BR-2: disagreeing measured attempts surface a flaky marker; the resolved
    # verdict is still latest-wins (PASS here).
    recs = [_raw("t", "standard", "FAIL", ordinal=0),
            _raw("t", "standard", "PASS", ordinal=1)]
    label = _one(_derive(recs), "t")
    assert label["per_tier_verdicts"]["standard"] == "PASS"
    assert label.get("flaky", {}).get("standard") is True


def test_error_attempt_not_disagreement():
    # BR-2 / inv-error-unmeasured: an ERROR attempt is unmeasured, not a
    # disagreement. PASS then ERROR stays PASS and is NOT flaky.
    recs = [_raw("t", "standard", "PASS", ordinal=0),
            _raw("t", "standard", "ERROR", ordinal=1)]
    label = _one(_derive(recs), "t")
    assert label["per_tier_verdicts"]["standard"] == "PASS"
    assert "standard" not in label.get("flaky", {})


def test_roundtrip_rederive():
    # inv-two-layer: write raw rows to a JSONL log, load and derive, and confirm
    # re-derivation is deterministic (same log in -> same labels out).
    recs = [_raw("t", "fast", "ERROR"),
            _raw("t", "standard", "FAIL", ordinal=0),
            _raw("t", "standard", "PASS", ordinal=1),
            _raw("t", "reasoning", "PASS")]
    fh = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    try:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
        fh.close()
        path = Path(fh.name)
        labels_a = _derive(derive_labels.load_log(path))
        labels_b = _derive(derive_labels.load_log(path))
        assert labels_a == labels_b, "derivation must be pure/re-derivable"
        label = _one(labels_a, "t")
        assert label["minimum_viable_tier"] == "standard"
        assert label["per_tier_verdicts"]["fast"] == "unmeasured"
        assert label.get("flaky", {}).get("standard") is True
    finally:
        Path(fh.name).unlink(missing_ok=True)


def test_emit_and_derive_integration():
    # inv-two-layer end to end: run_bracket --mock --emit appends 3 enriched,
    # schema-valid records to a temp log; deriving from that log yields
    # minimum_viable_tier 'standard'. Uses a temp path so the default
    # runs/measurements.jsonl is never touched.
    fh = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
    fh.close()
    log = Path(fh.name)
    log.unlink(missing_ok=True)  # start from a clean, absent log
    try:
        env = dict(os.environ)
        env["KILLHOUSE_ROOT"] = str(KH_ROOT)
        proc = subprocess.run(
            [sys.executable, str(REPO / "bin" / "run_bracket.py"),
             "--record", str(REPO / "tasks" / "add_two" / "record.json"),
             "--mock", "--emit", "--out", str(log)],
            capture_output=True, text=True, env=env,
        )
        assert proc.returncode == 0, proc.stderr
        records = kdl.load_records(log)
        assert len(records) == 3, f"expected 3 measurement rows, got {len(records)}"
        for rec in records:
            errors = kdl.validate_record(rec)
            assert not errors, f"enriched record failed killhouse schema: {errors}"
            assert rec["outcome"]["status"] in ("pass", "fail")
            assert rec["re_outcome"] in ("PASS", "FAIL", "ERROR", "SKIPPED_NO_ROUTING")
            assert "task_id" in rec and "tier" in rec and "attempt_ordinal" in rec
        labels = _derive(records)
        assert len(labels) == 1, f"one task expected, got {len(labels)}"
        assert labels[0]["minimum_viable_tier"] == "standard"
        assert labels[0]["per_tier_verdicts"]["fast"] == "FAIL"
    finally:
        log.unlink(missing_ok=True)


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"  ok  {t.__name__}")
        except Exception as exc:  # noqa: BLE001 - report any failure, keep going
            failures += 1
            print(f"FAIL  {t.__name__}: {exc!r}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run())
