#!/usr/bin/env python3
"""Generic OpenAI-compatible executor for killhouse's gate-replay harness.

killhouse's gate-replay harness ships *without* an executor: it reads a shell template
(`replay_executor` in .killhouse/config.json, or $KILLHOUSE_REPLAY_EXECUTOR) with
{model} {workdir} {prompt_file} placeholders, but leaves the actual model call to the
user. This is that missing piece — deliberately dumb for pass 1.

Contract (invoked by the harness with cwd == workdir):
  read the prompt file -> call an OpenAI-compatible /chat/completions endpoint ->
  extract a single fenced code block -> OVERWRITE one target file inside the sandbox.

No diff parsing: the whole point of pass 1 is to isolate "can this tier pass the gate"
from "can I apply a patch". Harden later.

Env (live mode):
  RE_TARGET_FILE  sandbox-relative path to overwrite (required)
  RE_BASE_URL     OpenAI-compatible base url, e.g. https://api.fireworks.ai/inference/v1
  RE_API_KEY      bearer token (resolved by the runner from config.api_key_env)

Env (mock mode, --mock): no network. Writes a fixture so the whole loop can be proven
offline, the way killhouse proves its own plumbing with --mock.
  RE_TIER         current tier ("fast" | "standard" | "reasoning")
  RE_GOLDEN       path to a correct implementation (used for standard/reasoning)
  RE_BUGGY        path to a wrong implementation (used for fast, to show a realistic bracket)

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

_FENCE = re.compile(r"```(?:[\w+.-]*)\n(.*?)```", re.S)


def extract_code(text: str) -> str:
    """Return the first fenced code block, or the whole response if unfenced."""
    m = _FENCE.search(text)
    return m.group(1) if m else text


def call_model(model: str, prompt: str) -> str:
    url = os.environ["RE_BASE_URL"].rstrip("/") + "/chat/completions"
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }
    ).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {os.environ['RE_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


def resolve_target(workdir: str) -> Path:
    rel = os.environ.get("RE_TARGET_FILE")
    if not rel:
        sys.exit("executor: RE_TARGET_FILE is not set")
    target = Path(workdir) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", required=True)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--prompt-file", required=True)
    ap.add_argument("--mock", action="store_true", help="write a fixture instead of calling a model")
    args = ap.parse_args(argv)

    target = resolve_target(args.workdir)

    if args.mock:
        tier = os.environ.get("RE_TIER", "")
        # fast writes a wrong answer, higher tiers write the correct one — so the mock
        # produces a realistic bracket (fast FAIL, standard/reasoning PASS).
        fixture = os.environ["RE_BUGGY"] if tier == "fast" else os.environ["RE_GOLDEN"]
        target.write_text(Path(fixture).read_text())
        return 0

    prompt = Path(args.prompt_file).read_text()
    content = call_model(args.model, prompt)
    target.write_text(extract_code(content).rstrip() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
