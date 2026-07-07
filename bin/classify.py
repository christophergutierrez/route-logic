#!/usr/bin/env python3
"""Classify a bracket result into the minimum viable tier label.

This is routerescalation's core labeling step. Per CONTEXT.md, the *minimum
viable tier* for a task is "the smallest-capability tier whose output passes
that task's real gate"; a task with no passing tier is labeled "NONE". The
tier ladder (ADR-0005, single family) in increasing capability is:

    fast  <  standard  <  reasoning

`bin/run_bracket.py` produces a bracket -- one pass/fail result per tier --
by replaying a delegation record through killhouse's gate-replay harness.
This module turns that bracket into the single label routerescalation exists
to produce. It is pure: no model calls, no network, no gate execution. It
only reduces a bracket to a label, so it can be unit-tested and replayed
freely.

Input shape (one canonical shape). A list of objects each carrying `tier`
and `passed` keys, exactly as `run_bracket.py --schema` emits:

    [{"tier": "fast", "passed": false},
     {"tier": "standard", "passed": true},
     {"tier": "reasoning", "passed": true}]

Extra keys per entry (model, verdict, gate_exit, ...) are ignored, so the
runner's richer per-tier rows are accepted unchanged. The older tier->bool
object shape is deliberately not accepted: a single input contract keeps the
runner and the labeler from silently disagreeing (the pass-1 schema-mismatch
bug).

Output: the minimum viable tier label on stdout (one of the tier names, or
"NONE"). With --json, emits {"minimum_viable_tier": <label>}.

Usage:
    classify.py bracket.json
    classify.py --json bracket.json
    cat bracket.json | classify.py -
"""

import argparse
import json
import sys
from typing import Any, Iterable

# ADR-0005: single-family tier ladder, smallest capability first.
TIER_LADDER: tuple[str, ...] = ("fast", "standard", "reasoning")
NONE_LABEL = "NONE"


def _is_passed(val: Any) -> bool:
    """Strict truthiness: only JSON ``true`` / ``1`` count as passed.

    ``bool()`` would treat non-empty strings like ``"false"`` or ``"0"`` as
    ``True``, silently flipping a FAIL tier to PASS and corrupting the
    minimum_viable_tier label. Accept only the canonical JSON truthy values;
    everything else (including ``None``, ``0``, ``"false"``, ``"no"``) is
    treated as not passed.

    The numeric check is restricted to ``int`` on purpose. In Python
    ``1.0 == 1`` is ``True``, so a float ``1.0`` (a common JSON artifact when a
    float column backs the flag) would otherwise slip through and flip a FAIL
    tier to PASS. Only a real ``bool`` True or an ``int`` 1 counts.
    """
    return val is True or (type(val) is int and val == 1)


def _normalize(bracket: Any) -> dict[str, bool]:
    """Coerce the canonical list shape into {tier: passed_bool}.

    Accepts a list of objects each with `tier` and `passed` keys. Tiers not on
    the ladder are kept but never selected. Missing ladder tiers are treated as
    not passed. A non-list input is a contract violation and raises.
    """
    if not isinstance(bracket, list):
        raise ValueError(
            "bracket must be a JSON array of {tier, passed} objects "
            f"(the run_bracket --schema shape), got {type(bracket).__name__}"
        )
    result: dict[str, bool] = {tier: False for tier in TIER_LADDER}
    for entry in bracket:
        if not isinstance(entry, dict):
            continue
        tier = entry.get("tier")
        if tier is None:
            continue
        result[str(tier)] = _is_passed(entry.get("passed", False))
    return result


def minimum_viable_tier(bracket: Any) -> str:
    """Return the smallest-capability passing tier, or NONE_LABEL.

    Iterates the ladder from smallest to largest capability and returns the
    first tier whose gate passed. Tiers not on the ladder are ignored. If no
    ladder tier passed, returns NONE_LABEL (CONTEXT.md: "A task with no
    passing tier has an undefined (or 'NONE') minimum viable tier").
    """
    passed = _normalize(bracket)
    for tier in TIER_LADDER:
        if passed.get(tier, False):
            return tier
    return NONE_LABEL


def _load_bracket(path: str) -> Any:
    if path == "-":
        return json.load(sys.stdin)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Classify a bracket result into the minimum viable tier label.",
    )
    parser.add_argument(
        "bracket",
        help="Path to a bracket JSON file, or '-' to read from stdin.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help='Emit {"minimum_viable_tier": <label>} as JSON instead of the bare label.',
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    bracket = _load_bracket(args.bracket)
    label = minimum_viable_tier(bracket)

    if args.json:
        json.dump({"minimum_viable_tier": label}, sys.stdout)
        sys.stdout.write("\n")
    else:
        print(label)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
