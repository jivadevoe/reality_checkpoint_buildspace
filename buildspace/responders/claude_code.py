# Copyright 2026 Reality Checkpoint
# SPDX-License-Identifier: Apache-2.0
"""Answer a Buildspace comment with Claude Code, from inside the session
that pushed the entry.

Enable with BUILDSPACE_RESPONDER=claude. Reads a job on stdin (see
buildspace/responder.py), prints {"text", "session_id", "cost_usd"}.

When the entry recorded its origin session and that session's transcript
is still on disk, this runs `claude -p --resume <session> --fork-session`
from the session's working directory: a copy of the whole conversation
answers, and the original session is never written to. Follow-ups in the
same thread resume that copy, so the side conversation keeps its own
memory. Without a usable origin it falls back to a fresh session that
gets the entry's content in the prompt.

Either way the answering session is read-only: only Read, Grep and Glob
exist in it, anything needing approval is denied, and no MCP servers load.

Environment:
  BUILDSPACE_CLAUDE_BIN      path to the claude executable (default: PATH,
                             then ~/.local/bin, /opt/homebrew/bin, /usr/local/bin)
  BUILDSPACE_RESPONDER_MODEL model to answer with (default: Claude Code's)
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
PAYLOAD_LIMIT = 30_000  # chars of entry content in a fresh-session prompt
TIMEOUT = 540  # a little under the server's default, so we report our own timeout

LOCKDOWN = [
    "--tools", "Read,Grep,Glob",
    "--permission-mode", "dontAsk",
    "--strict-mcp-config",
    "--output-format", "json",
]


def find_claude() -> str:
    explicit = os.environ.get("BUILDSPACE_CLAUDE_BIN")
    if explicit:
        return explicit
    found = shutil.which("claude")
    if found:
        return found
    for cand in ("~/.local/bin/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude"):
        p = Path(cand).expanduser()
        if p.exists():
            return str(p)
    raise SystemExit("can't find the claude executable; set BUILDSPACE_CLAUDE_BIN")


def base_env(config_dir: Path | None) -> dict:
    # Drop the variables of whatever Claude session started the server, so
    # the child doesn't think it's nested inside one.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("CLAUDE_CODE_") and k not in ("CLAUDECODE", "CLAUDE_PID", "CLAUDE_CONFIG_DIR")}
    # Only point at a non-default config dir. Setting it to the default
    # explicitly can make Claude Code look for its login somewhere else.
    if config_dir and config_dir.resolve() != (Path.home() / ".claude").resolve():
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    return env


def usable_origin(origin: dict | None) -> tuple[str, Path, Path] | None:
    """(session_id, cwd, config_dir) if the origin session can be resumed."""
    if not origin:
        return None
    sid = str(origin.get("session_id") or "")
    cwd = Path(str(origin.get("cwd") or "")).expanduser()
    transcript = Path(str(origin.get("transcript") or "")).expanduser()
    if not SESSION_ID_RE.match(sid) or not cwd.is_dir():
        return None
    # <config>/projects/<cwd-slug>/<session>.jsonl
    if transcript.name != f"{sid}.jsonl" or transcript.parent.parent.name != "projects":
        return None
    if not transcript.is_file():
        return None
    return sid, session_home(transcript, cwd), transcript.parent.parent.parent


def _slug(path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def session_home(transcript: Path, fallback: Path) -> Path:
    """The directory the session was started in: the transcript's project
    folder is that path slugged. The push may have come from a subdirectory
    the agent had cd'd into; answering from the session's own directory keeps
    relative paths meaning what they meant in the conversation."""
    want = transcript.parent.name
    candidates = [fallback]
    try:
        with transcript.open() as f:
            for _, line in zip(range(50), f):
                try:
                    cwd = json.loads(line).get("cwd")
                except ValueError:
                    continue
                if cwd:
                    candidates.append(Path(cwd))
    except OSError:
        pass
    for c in candidates:
        if _slug(c) == want and c.is_dir():
            return c
    return fallback


def describe_target(entry: dict, a: dict) -> str:
    kind = entry.get("kind")
    anchor = a.get("anchor") or {}
    if kind == "note":
        what = f"block {a.get('block_index')}"
        if a.get("line_index") is not None:
            what += f", row/item {a.get('line_index')}"
        return f'{what} of the note, which reads: "{a.get("block_preview") or ""}"'
    if kind == "uml":
        if anchor.get("label"):
            return f'the diagram element labelled "{anchor["label"]}"'
        return "an unlabelled spot on the diagram (an arrow, lifeline or empty space)"
    if kind == "graph":
        if anchor.get("node") is not None:
            return f'the graph node "{a.get("block_preview") or anchor["node"]}"'
        return "an empty spot on the graph"
    return "the entry"


def build_prompt(job: dict, has_session: bool, continuing: bool) -> str:
    entry, a = job["entry"], job["annotation"]
    parts = []
    if continuing:
        parts.append(f"[Buildspace follow-up on the same comment]\n\n{job['question']}")
    else:
        parts.append(
            "[Buildspace comment]\n\n"
            "The user left a comment in Buildspace, the viewer where this session's "
            "explanations are shown, and asked you to answer it. This is a read-only side "
            "conversation. Answer from what you know"
            + (" from this session" if has_session else "")
            + ", reading files if you need to. Don't edit anything, run commands, or push to "
            "Buildspace. If the comment asks for a change, say what you'd change; the main "
            "session will do it."
        )
        parts.append(
            f'Entry #{entry["id"]} ({entry["kind"]}): "{entry.get("title") or ""}"\n'
            f"They commented on {describe_target(entry, a)}"
        )
        parts.append(f"Their comment:\n{a.get('comment', '')}")
        earlier = [m for m in job.get("thread") or [] if m.get("text")]
        if earlier:
            lines = [f"{'User' if m['author'] == 'user' else 'You'}: {m['text']}" for m in earlier]
            parts.append("Earlier in this thread:\n" + "\n\n".join(lines))
        if job.get("question") and job["question"] != a.get("comment"):
            parts.append(f"Their latest message:\n{job['question']}")
        if not has_session:
            payload = json.dumps(entry.get("payload"), indent=1)[:PAYLOAD_LIMIT]
            origin = job.get("origin") or {}
            where = f" It was pushed from {origin['cwd']}." if origin.get("cwd") else ""
            parts.append(
                "You don't have the conversation that produced this entry, only the entry "
                f"itself.{where} Its content:\n```json\n{payload}\n```"
            )
    parts.append("Reply in concise markdown. It shows in a narrow side panel.")
    return "\n\n".join(parts)


def run_claude(args: list[str], cwd: Path, env: dict) -> dict:
    proc = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=TIMEOUT)
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        raise RuntimeError((proc.stderr or proc.stdout or "claude printed nothing").strip()[-600:])
    if data.get("is_error") or proc.returncode != 0:
        raise RuntimeError(str(data.get("result") or proc.stderr or "claude failed").strip()[-600:])
    return data


def main() -> int:
    job = json.load(sys.stdin)
    claude = find_claude()
    model = os.environ.get("BUILDSPACE_RESPONDER_MODEL")
    common = LOCKDOWN + (["--model", model] if model else [])
    origin = usable_origin(job.get("origin"))

    fresh_cwd = Path(str((job.get("origin") or {}).get("cwd") or "")).expanduser()
    if not fresh_cwd.is_dir():
        fresh_cwd = Path.home()

    # Tried in order until one answers. Each: (flag, session, extra args,
    # cwd, config dir, has the origin conversation, continuing this thread).
    attempts = []
    resume = str(job.get("resume_session") or "")
    if SESSION_ID_RE.match(resume):
        # This thread's own session from an earlier answer: continue it
        # directly. It lives under the same cwd it was started in.
        if origin:
            attempts.append(("--resume", resume, [], origin[1], origin[2], True, True))
        else:
            attempts.append(("--resume", resume, [], fresh_cwd, None, False, True))
    if origin:
        sid, cwd, config = origin
        attempts.append(("--resume", sid, ["--fork-session"], cwd, config, True, False))
    attempts.append((None, None, [], fresh_cwd, None, False, False))

    last_error = None
    for flag, session, extra, cwd, config, has_session, continuing in attempts:
        prompt = build_prompt(job, has_session, continuing)
        args = [claude, "-p", prompt] + ([flag, session] if flag else []) + extra + common
        try:
            data = run_claude(args, cwd, base_env(config))
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            last_error = e
            continue
        print(json.dumps({
            "text": data.get("result", ""),
            "session_id": data.get("session_id"),
            "cost_usd": data.get("total_cost_usd"),
            "mode": "continued" if continuing else ("forked" if has_session else "fresh"),
        }))
        return 0
    print(f"Couldn't get an answer: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
