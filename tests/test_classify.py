#!/usr/bin/env python3
"""Unit tests for bin/classify.py, the pure minimum-viable-tier labeler.

Covers the canonical list-shape contract, ladder selection, the NONE fallback,
strict-truthiness (including the float 1.0 gap, M5), and rejection of the old
dict shape (M4). Runnable without pytest: python3 tests/test_classify.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import classify  # noqa: E402
from classify import minimum_viable_tier, _is_passed  # noqa: E402


def _row(tier, passed):
    return {"tier": tier, "passed": passed}


def test_all_pass_is_fast():
    bracket = [_row("fast", True), _row("standard", True), _row("reasoning", True)]
    assert minimum_viable_tier(bracket) == "fast"


def test_mid_ladder_pass():
    bracket = [_row("fast", False), _row("standard", True), _row("reasoning", True)]
    assert minimum_viable_tier(bracket) == "standard"


def test_only_top_pass():
    bracket = [_row("fast", False), _row("standard", False), _row("reasoning", True)]
    assert minimum_viable_tier(bracket) == "reasoning"


def test_all_fail_is_none():
    bracket = [_row("fast", False), _row("standard", False), _row("reasoning", False)]
    assert minimum_viable_tier(bracket) == "NONE"


def test_missing_tier_treated_as_not_passed():
    # standard omitted entirely -> not passable, so label falls to reasoning.
    bracket = [_row("fast", False), _row("reasoning", True)]
    assert minimum_viable_tier(bracket) == "reasoning"


def test_extra_keys_ignored():
    # The runner's richer rows carry model/verdict/gate_exit; classify ignores them.
    bracket = [
        {"tier": "fast", "passed": False, "verdict": "FAIL", "gate_exit": 1},
        {"tier": "standard", "passed": True, "verdict": "PASS", "gate_exit": 0},
    ]
    assert minimum_viable_tier(bracket) == "standard"


def test_strict_truthiness():
    # M5: only real bool True or int 1 count; float 1.0 and truthy strings do not.
    assert _is_passed(True) is True
    assert _is_passed(1) is True
    assert _is_passed(1.0) is False, "float 1.0 must NOT count as passed (M5)"
    assert _is_passed("true") is False
    assert _is_passed("1") is False
    assert _is_passed(0) is False
    assert _is_passed(None) is False
    assert _is_passed(False) is False


def test_float_one_does_not_flip_label():
    # A FAIL tier serialized as 1.0 must stay FAIL, not silently become passing.
    bracket = [_row("fast", 1.0), _row("standard", True), _row("reasoning", True)]
    assert minimum_viable_tier(bracket) == "standard"


def test_dict_shape_rejected():
    # M4: the old tier->bool object shape is no longer a valid input contract.
    try:
        minimum_viable_tier({"fast": False, "standard": True})
    except ValueError:
        return
    raise AssertionError("dict bracket shape must be rejected (M4)")


def test_non_list_scalar_rejected():
    try:
        minimum_viable_tier("standard")
    except ValueError:
        return
    raise AssertionError("scalar bracket must be rejected")


def _run():
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
    raise SystemExit(_run())
