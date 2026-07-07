#!/usr/bin/env python3
"""Derive the labels view (ADR-0006 layer 2) from the raw measurement log.

The raw measurement log is an append-only JSONL of enriched killhouse
delegation records, one per measurement (task x tier x attempt), written by
``bin/run_bracket.py --emit``. This module reduces that log into the derived,
per-task training contract: for each task, the ``minimum_viable_tier`` plus the
per-tier verdict map. It is pure and re-derivable: the same log in always
yields the same labels out; no state is kept between runs.

Resolution rules (BR-2 and inv-error-unmeasured):

  - A measurement whose ``re_outcome`` is PASS or FAIL is *measured*. An
    ``ERROR`` or ``SKIPPED_NO_ROUTING`` measurement is *unmeasured*.
  - A tier with no measured attempt is labeled ``unmeasured`` and excluded from
    the label (it is not a measured non-pass).
  - A tier with measured attempts resolves to the verdict of its latest attempt
    by ``attempt_ordinal`` (latest-attempt-wins). When measured attempts
    disagree (both a PASS and a FAIL were observed), the tier is also marked
    ``flaky`` rather than silently choosing PASS.
  - ``minimum_viable_tier`` is the lowest-capability tier whose resolved verdict
    is PASS, or ``NONE`` when no tier passes. The ladder is classify's.

Usage:
    derive_labels.py runs/measurements.jsonl
    derive_labels.py runs/measurements.jsonl --task-id toy-add-two-001
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

RR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RR / "bin"))
try:
    from classify import TIER_LADDER, minimum_viable_tier  # noqa: E402
except ModuleNotFoundError:
    sys.exit("[fail] bin/classify.py not found under repo root")

# Outcomes that count as a real measurement of a tier's capability. Everything
# else (ERROR, SKIPPED_NO_ROUTING, or anything unknown) is unmeasured.
MEASURED_OUTCOMES = ("PASS", "FAIL")
UNMEASURED = "unmeasured"


def load_log(log_path: Path) -> list[dict[str, Any]]:
    """Parse a raw measurement JSONL log into records, skipping blank lines."""
    log_path = Path(log_path)
    records: list[dict[str, Any]] = []
    for lineno, line in enumerate(log_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{log_path}:{lineno}: invalid JSON: {exc}") from exc
    return records


def _resolve_tier(tier_records: list[dict[str, Any]]) -> tuple[str, str | None, bool]:
    """Resolve one tier's attempts to (verdict, model_id, flaky).

    ``verdict`` is PASS/FAIL for a measured tier or ``unmeasured`` otherwise.
    ``model_id`` is taken from the latest attempt (measured or not) so the
    tier->model map reflects what backs the tier. ``flaky`` is True when the
    measured attempts disagree (BR-2).
    """
    ordered = sorted(tier_records, key=lambda r: r.get("attempt_ordinal", 0))
    model_id = ordered[-1].get("model_id") if ordered else None
    measured = [r for r in ordered if r.get("re_outcome") in MEASURED_OUTCOMES]
    if not measured:
        return UNMEASURED, model_id, False
    verdict = measured[-1].get("re_outcome")  # latest-attempt-wins
    outcomes = {r.get("re_outcome") for r in measured}
    flaky = "PASS" in outcomes and "FAIL" in outcomes
    return str(verdict), model_id, flaky


def _derive_one(task_id: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive one task's labels-view record from its raw measurements."""
    by_tier: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        tier = rec.get("tier")
        if tier is None:
            continue
        by_tier.setdefault(str(tier), []).append(rec)

    per_tier_verdicts: dict[str, str] = {}
    tier_model_map: dict[str, str | None] = {}
    flaky: dict[str, bool] = {}
    for tier, tier_records in by_tier.items():
        verdict, model_id, is_flaky = _resolve_tier(tier_records)
        per_tier_verdicts[tier] = verdict
        tier_model_map[tier] = model_id
        if is_flaky:
            flaky[tier] = True

    # Reuse the single labeler: only a resolved PASS makes a tier passable, so
    # unmeasured/FAIL tiers are skipped and the lowest measured PASS wins.
    bracket = [{"tier": t, "passed": per_tier_verdicts.get(t) == "PASS"} for t in TIER_LADDER]
    label: dict[str, Any] = {
        "task_id": task_id,
        "minimum_viable_tier": minimum_viable_tier(bracket),
        "per_tier_verdicts": per_tier_verdicts,
        "tier_model_map": tier_model_map,
    }
    if flaky:
        label["flaky"] = flaky
    return label


def derive_labels(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reduce raw measurement records into one labels-view record per task.

    Pure and re-derivable. Tasks are emitted in first-seen order so the output
    is deterministic for a given log.
    """
    by_task: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        task_id = rec.get("task_id")
        if task_id is None:
            continue
        by_task.setdefault(str(task_id), []).append(rec)
    return [_derive_one(task_id, task_records) for task_id, task_records in by_task.items()]


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Derive the labels view from a raw measurement JSONL log.",
    )
    parser.add_argument("log", type=Path, help="path to the raw measurement JSONL log")
    parser.add_argument("--task-id", default=None, help="emit only the label for this task_id")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.log.is_file():
        sys.exit(f"[fail] no such measurement log: {args.log}")

    labels = derive_labels(load_log(args.log))
    if args.task_id is not None:
        labels = [lab for lab in labels if lab["task_id"] == args.task_id]

    json.dump(labels, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
