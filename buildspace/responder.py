# Copyright 2026 Reality Checkpoint
# SPDX-License-Identifier: Apache-2.0
"""Runs the configured responder: the program that answers a viewer's
comment when they tick "ask".

BUILDSPACE_RESPONDER is a command line. The job goes to its stdin as JSON:

    {"entry": {id, kind, title, payload},
     "annotation": {id, comment, block_preview, block_index, line_index, anchor},
     "thread": [{"author": "user"|"agent", "text": ...}, ...],   # before this question
     "question": "...",          # the comment, or the latest follow-up
     "origin": {...} | null,     # what the pushing agent recorded
     "resume_session": "..." | null}   # the responder's own session from earlier in the thread

and it prints one JSON object: {"text": markdown, "session_id"?: str,
"cost_usd"?: float}. A non-zero exit fails the answer with its stderr.

The value "claude" is shorthand for the bundled Claude Code responder.
"""
import asyncio
import json
import os
import shlex
import sys

DEFAULT_TIMEOUT = 600.0


def command_from_env() -> list[str] | None:
    raw = os.environ.get("BUILDSPACE_RESPONDER", "").strip()
    if not raw:
        return None
    if raw == "claude":
        return [sys.executable, "-m", "buildspace.responders.claude_code"]
    return shlex.split(raw)


def display_name() -> str:
    name = os.environ.get("BUILDSPACE_RESPONDER_NAME", "").strip()
    if name:
        return name
    return "Claude" if os.environ.get("BUILDSPACE_RESPONDER", "").strip() == "claude" else "agent"


def timeout_from_env() -> float:
    try:
        return float(os.environ.get("BUILDSPACE_RESPONDER_TIMEOUT", DEFAULT_TIMEOUT))
    except ValueError:
        return DEFAULT_TIMEOUT


class ResponderError(Exception):
    pass


async def run(cmd: list[str], job: dict, timeout: float) -> dict:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(json.dumps(job).encode()), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise ResponderError(f"No answer after {int(timeout)}s; gave up.")
    if proc.returncode != 0:
        tail = err.decode(errors="replace").strip()[-600:]
        raise ResponderError(tail or f"responder exited with {proc.returncode}")
    try:
        result = json.loads(out.decode())
    except ValueError:
        raise ResponderError("responder printed something that isn't JSON")
    if not isinstance(result, dict) or not str(result.get("text", "")).strip():
        raise ResponderError("responder returned an empty answer")
    return result
