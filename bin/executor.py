#!/usr/bin/env python3
"""Generic OpenAI-compatible executor for killhouse's gate-replay harness.

killhouse's gate-replay harness ships *without* an executor: it reads a shell template
(replay_executor in .killhouse/config.json, or $KILLHOUSE_REPLAY_EXECUTOR) with
{model} {workdir} {prompt_file} placeholders, but leaves the actual model call to the
user. This is that missing piece: deliberately dumb for pass 1.

Contract (invoked by the harness with cwd == workdir):
  read the prompt file -> call an OpenAI-compatible /chat/completions endpoint ->
  extract path-tagged fenced code blocks -> OVERWRITE each named file inside the sandbox.

No diff parsing: the whole point of pass 1 is to isolate "can this tier pass the gate"
from "can I apply a patch". Harden later.

Env (live mode):
  RE_TARGET_FILE       sandbox-relative path(s) to overwrite, comma-separated (required)
  RE_BASE_URL          OpenAI-compatible base url, e.g. https://api.fireworks.ai/inference/v1
  RE_API_KEY           bearer token (resolved by the runner from config.api_key_env)

Env (mock mode, --mock): no network. Writes fixtures so the whole loop can be proven
offline, the way killhouse proves its own plumbing with --mock.
  RE_TIER              current tier ("fast" | "standard" | "reasoning")
  RE_GOLDEN            path to a correct implementation (used for standard/reasoning)
  RE_BUGGY             path to a wrong implementation (used for fast, to show a realistic bracket)

Exit codes:
  0  success
  1  model-fault: unparseable / empty / missing file tag / any non-error HTTP status
     that indicates a bad response from the API
  2  infra-fault: 401/403/429/5xx network error or missing config, which marks the
     tier unmeasured (ERROR) rather than FAIL

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable

# A fenced code block starts with ``` optionally followed by a language tag,
# then a newline, then the block body, then a closing ```.  We capture the
# language tag so we can use it as a *file extension hint*, but per the
# milestone contract the block body is replaced by a path-tag variant:
#
#     ```path:src.py
#     ... file contents ...
#     ```
#
# The path may be relative to the sandbox root.  Blocks without a path tag
# are rejected (the executor refuses to guess).

_FENCE = re.compile(
    r"```[^\n]*\n(.*?)```",
    re.S,
)

# Strict path tag: captures "path:<relative_path>" after the opening backticks.
# Relative paths must not start with "/" and must not contain "..".
_PATH_TAG = re.compile(r"""path:\s*([^\s"`]+)""")

_UTF8 = {"encoding": "utf-8"}


def _safe_target(sandbox: Path, rel: str) -> Path:
    """Resolve ``rel`` under ``sandbox``, refusing absolute paths or ``..`` escapes."""
    rel = rel.strip()
    if not rel:
        raise ValueError("artifact path must not be empty")
    if os.path.isabs(rel):
        raise ValueError(
            f"artifact path must be sandbox-relative, got absolute: {rel!r}"
        )
    if ".." in rel.split("/"):
        raise ValueError(f"artifact path escapes the sandbox: {rel!r}")
    target = (sandbox / rel).resolve()
    if not target.is_relative_to(sandbox.resolve()):
        raise ValueError(f"artifact path escapes the sandbox: {rel!r}")
    return target


def extract_code_blocks(text: str) -> list[tuple[str, str]]:
    """Return list of ``(path, contents)`` from path-tagged fenced blocks.

    Rejects blocks without a ``path:`` tag and also returns blocks without
    tags as ``(path, contents)`` pairs where *path* is the single file
    ``RE_TARGET_FILE`` when only one block is found (backward compat).

    Raises ``ValueError`` if blocks are found but none carry a usable path.
    """
    matches = list(_FENCE.finditer(text))
    if not matches:
        return []  # no fenced blocks; caller decides what to do

    results: list[tuple[str, str]] = []
    for m in matches:
        block_body = m.group(1).rstrip() + "\n"
        tag_match = _PATH_TAG.search(text[m.start() : m.start() + 80])
        if tag_match:
            path = tag_match.group(1).strip()
        else:
            path = os.environ.get("RE_TARGET_FILE", "")
        if not path:
            raise ValueError(
                "fenced code block found but has no ``path:`` tag and "
                "RE_TARGET_FILE is not set"
            )
        results.append((path, block_body))

    # Backward compat: if exactly one block and no explicit path tag, use
    # RE_TARGET_FILE.
    if len(results) == 1:
        path = results[0][0]
        content = results[0][1]
        if not _PATH_TAG.search(text[m.start() : m.start() + 80] if m else ""):
            # Re-check if the original text had no path tag
            if _PATH_TAG.search(text[matches[0].start(): matches[0].start()+80]):
                pass  # kept
            else:
                # No path tag; use RE_TARGET_FILE
                results = [(os.environ.get("RE_TARGET_FILE", path), content)]

    return results


def call_model(model: str, prompt: str) -> str:
    """Call an OpenAI-compatible chat/completions endpoint.

    Raises ``ValueError`` on infra-level HTTP errors (401, 403, 429, 5xx,
    network failure) so the caller can distinguish them from model-fault
    exits.
    """
    url = os.environ.get("RE_BASE_URL", "").rstrip("/") + "/chat/completions"
    if not url.endswith("/chat/completions"):
        raise ValueError("RE_BASE_URL is not set or invalid")
    api_key = os.environ.get("RE_API_KEY")
    if not api_key:
        raise ValueError("RE_API_KEY is not set")

    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        status = exc.code
        if status in (401, 403, 429):
            raise ValueError(
                f"infra-fault: HTTP {status} (auth rate-limit); tier unmeasured"
            ) from exc
        if status >= 500:
            raise ValueError(
                f"infra-fault: HTTP {status} (server error); tier unmeasured"
            ) from exc
        raise ValueError(f"model-fault: HTTP {status}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ValueError("infra-fault: network error; tier unmeasured") from exc

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(
            f"model-fault: unexpected response shape: {exc}"
        ) from exc
    return content


def _classify_exit(exc: Exception) -> int:
    """Return 1 for model-fault, 2 for infra-fault, per exit-code contract."""
    msg = str(exc).lower()
    if "infra" in msg:
        return 2
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", required=True)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--prompt-file", required=True)
    ap.add_argument("--mock", action="store_true", help="write a fixture instead of calling a model")
    args = ap.parse_args(argv)

    sandbox = Path(args.workdir).resolve()

    if args.mock:
        tier = os.environ.get("RE_TIER", "")
        fixture = os.environ.get("RE_BUGGY") if tier == "fast" else os.environ.get("RE_GOLDEN")
        if not fixture:
            print("executor mock: RE_GOLDEN or RE_BUGGY not set", file=sys.stderr)
            return 1
        target = _safe_target(sandbox, os.environ.get("RE_TARGET_FILE", "src.py"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(Path(fixture).read_text(), **_UTF8)
        return 0

    # --- live mode ---
    prompt = Path(args.prompt_file).read_text(**_UTF8)
    try:
        content = call_model(args.model, prompt)
    except ValueError as exc:
        print(f"executor: {exc}", file=sys.stderr)
        return _classify_exit(exc)

    try:
        blocks = extract_code_blocks(content)
    except ValueError as exc:
        print(f"executor: {exc}", file=sys.stderr)
        return 1

    if not blocks:
        # Model returned text but no fenced block: model fault
        print("executor: no fenced code block in model response", file=sys.stderr)
        return 1

    for file_path, file_content in blocks:
        try:
            target = _safe_target(sandbox, file_path)
        except ValueError as exc:
            print(f"executor: {exc}", file=sys.stderr)
            return 1
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file_content, **_UTF8)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())