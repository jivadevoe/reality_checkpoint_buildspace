# AGENTS.md

Instructions for coding agents (Claude Code, Codex, Aider, or anything
else that can run shell commands) on setting up Buildspace and using it to
show the user things instead of quoting them in chat.

Buildspace is a local web page the user keeps open. You push code, diffs,
diagrams, notes and video to it over HTTP. Every push appears in the
user's browser immediately.

## 1. Set it up

Do this once per machine. Ask before doing it if you are not sure the user
wants Buildspace on this machine.

```bash
cd <path to this repo>   # reality_checkpoint_buildspace
python3 -m venv venv && source venv/bin/activate
pip install -e .
scripts/run.sh &            # foreground server on http://127.0.0.1:8097
```

On macOS, prefer a launchd agent so it survives reboots and terminal
closes:

```bash
scripts/install-launchd.sh
```

Verify it is up before pushing anything:

```bash
curl -s http://127.0.0.1:8097/api/history | head -c 200
```

An empty history is `{"entries":[]}`. Tell the user to open
http://127.0.0.1:8097 in a browser. If they want it on a phone or tablet,
that requires a private network such as a Tailscale tailnet and the
hostname added to `BUILDSPACE_ALLOWED_HOSTS`. Read SECURITY.md before
suggesting anything beyond loopback. Never expose it publicly.

### Claude Code: install the skill

`skills/explain/SKILL.md` teaches Claude Code when and how to push. Copy
it into the user's skills directory and start a new session:

```bash
mkdir -p ~/.claude/skills/explain
cp skills/explain/SKILL.md ~/.claude/skills/explain/SKILL.md
```

After that, `/explain <thing>` in Claude Code drives Buildspace. Other
agents can read the same file for the usage patterns; it is plain
markdown.

## 2. Push things

Use the Python client from the repo's venv. It honours `BUILDSPACE_URL`
(default `http://127.0.0.1:8097`).

```python
import buildspace

buildspace.code("path/to/file.py", highlights=[12, 13],
                annotations=[{"line": 12, "kind": "warn", "text": "not thread safe"}])
buildspace.diff("path/to/file.py", before=old, after=new,
                annotations=[{"line": 40, "kind": "ok", "text": "lock added"}])
buildspace.uml("sequenceDiagram\n  A->>B: hello\n", title="Handshake")
buildspace.graph(nodes=[{"id": "a", "label": "api"}], edges=[])
buildspace.note("# Summary\n...", title="What changed")
buildspace.video("~/renders/cut.mp4", notes="- 00:12 audio pop")
```

Or from the shell:

```bash
buildspace code path/to/file.py -H 10-15 -a "12:look here"
```

Or from anything that can POST JSON. The full endpoint list is in the
README under "HTTP API".

Annotation `kind` is `note`, `warn` or `ok`. Graph node `group` is one of
`class`, `service`, `interface`, `db`, `external`.

## 3. Rules

- **Never call `buildspace.clear()` or `POST /api/clear` unless the user
  asks.** It deletes the user's entire timeline immediately and there is
  no undo. Tests and probes must run against a second instance:
  `BUILDSPACE_PORT=8098 BUILDSPACE_DB=/tmp/bs-test.db scripts/run.sh`.
- **Check for the user's comments.** The user can tap any paragraph of a
  note and leave a comment. Nothing notifies you. After you push a note,
  call `buildspace.get_note_annotations(entry)` on your next turn before
  assuming there is no feedback.
- **Files you push by path must be inside `BUILDSPACE_ROOTS`.** When that
  is unset the only root is the directory the server was started from
  (never the home directory). Anything outside the roots, and anything
  that looks like a credential file or personal data, comes back as a 403
  saying so; files over 5 MB come back as a 413. If a push is refused,
  send the text as `content` instead, or ask the user to widen
  `BUILDSPACE_ROOTS`. On
  macOS a launchd-managed server additionally cannot read external or
  network volumes; that read fails silently. Keep pushed files under the
  home directory.
- **Use `buildspace.link()` to cross-reference entries in a note.** Hand-
  written `#entry-N` fragments break silently on spaces and brackets.
- **Tell the user where to look.** After a push, give them the entry link.
  The client returns the entry dict; `buildspace.link_entry(entry)` gives
  the URL fragment. If the user reaches Buildspace through a hostname
  rather than loopback, use that hostname in the link.
- **Prefer a push over a wall of text** when the explanation is a code
  walkthrough, a diff, a diagram, or more than a few paragraphs. Prefer
  chat for one-liners, questions, and acknowledgements.
- **Do not push secrets.** The timeline is stored unencrypted and shown to
  any browser on the allowed network. Redact tokens and credentials
  before pushing a file that contains them.

## 4. Where things live

| Thing | Location |
|-------|----------|
| Server, client, CLI | `buildspace/` |
| Frontend | `buildspace/static/` (no build step) |
| Timeline database | `~/.buildspace/buildspace.db` or `BUILDSPACE_DB` |
| Images for notes | Drop in `buildspace/static/`, reference as `/static/name.jpg`. Git ignores them |
| Logs (launchd) | `buildspace.log`, `buildspace.err.log` in the repo root |
| Restart after code changes | `launchctl kickstart -k gui/$(id -u)/org.realitycheckpoint.buildspace` |
