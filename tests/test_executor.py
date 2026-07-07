#!/usr/bin/env python3
"""Unit + subprocess tests for bin/executor.py.

Covers sandbox containment (H3), multi-file path-tagged extraction, the
no-fence model-fault path (M7), and explicit-utf-8 writes under a C locale
(M6). Runnable without pytest: python3 tests/test_executor.py.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "bin"))
import executor  # noqa: E402
from executor import _safe_target, extract_code_blocks  # noqa: E402


def test_safe_target_accepts_relative():
    with tempfile.TemporaryDirectory() as d:
        sandbox = Path(d)
        target = _safe_target(sandbox, "sub/src.py")
        assert target == (sandbox / "sub" / "src.py").resolve()


def test_safe_target_rejects_dotdot():
    with tempfile.TemporaryDirectory() as d:
        try:
            _safe_target(Path(d), "../../etc/passwd")
        except ValueError:
            return
    raise AssertionError("path with .. must be rejected (H3)")


def test_safe_target_rejects_absolute():
    with tempfile.TemporaryDirectory() as d:
        try:
            _safe_target(Path(d), "/etc/passwd")
        except ValueError:
            return
    raise AssertionError("absolute path must be rejected (H3)")


def test_extract_two_tagged_blocks():
    text = (
        "Here you go:\n"
        "```path:a.py\nprint('a')\n```\n"
        "and the second:\n"
        "```path:sub/b.py\nprint('b')\n```\n"
    )
    blocks = extract_code_blocks(text)
    paths = {p for p, _ in blocks}
    assert paths == {"a.py", "sub/b.py"}, f"expected 2 tagged files, got {paths}"


def test_extract_no_fence_returns_empty():
    # M7: no fenced block -> [] so the caller records a model-fault, not a FAIL.
    assert extract_code_blocks("Sure! def add(a, b): return a + b") == []


def test_mock_write_utf8_under_c_locale():
    # M6: executor must write utf-8 regardless of the ambient locale.
    with tempfile.TemporaryDirectory() as d:
        sandbox = Path(d)
        golden = sandbox / "golden.py"
        golden.write_text("# accented: cafe\ndef add(a, b):\n    return a + b\n", encoding="utf-8")
        env = dict(os.environ)
        env["LC_ALL"] = "C"
        env["LANG"] = "C"
        env["RE_TIER"] = "standard"
        env["RE_GOLDEN"] = str(golden)
        env["RE_TARGET_FILE"] = "out/src.py"
        proc = subprocess.run(
            [sys.executable, str(REPO / "bin" / "executor.py"), "--mock",
             "--model", "m", "--workdir", str(sandbox), "--prompt-file", str(golden)],
            capture_output=True, text=True, env=env,
        )
        assert proc.returncode == 0, f"mock write failed under C locale: {proc.stderr}"
        written = (sandbox / "out" / "src.py").read_text(encoding="utf-8")
        assert "def add" in written


def test_mock_rejects_escaping_target():
    # H3 end-to-end: an escaping RE_TARGET_FILE must not write outside the sandbox.
    with tempfile.TemporaryDirectory() as d:
        sandbox = Path(d)
        golden = sandbox / "golden.py"
        golden.write_text("x = 1\n", encoding="utf-8")
        env = dict(os.environ)
        env["RE_TIER"] = "standard"
        env["RE_GOLDEN"] = str(golden)
        env["RE_TARGET_FILE"] = "../escape.py"
        proc = subprocess.run(
            [sys.executable, str(REPO / "bin" / "executor.py"), "--mock",
             "--model", "m", "--workdir", str(sandbox), "--prompt-file", str(golden)],
            capture_output=True, text=True, env=env,
        )
        assert proc.returncode != 0, "escaping target must fail"
        assert not (sandbox.parent / "escape.py").exists(), "wrote outside the sandbox!"


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
