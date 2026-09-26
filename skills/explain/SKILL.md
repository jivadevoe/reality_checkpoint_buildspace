---
name: explain
description: Explain code, changes, architecture, or concepts visually by pushing to Buildspace (the local show-and-tell web app, default http://127.0.0.1:8097 or $BUILDSPACE_URL) instead of quoting in chat. Use whenever the explanation would benefit from syntax-highlighted code with margin annotations, a side-by-side diff, a Mermaid UML diagram (sequence/class/state/flowchart), a force-directed relationship graph, or a formatted markdown walkthrough — rather than plain prose in the terminal.
argument-hint: "[what you want to explain]"
---

# /explain — drive Buildspace to show the user what you mean

When you want to explain something visually instead of describing it in chat, push to Buildspace. The user has it open in a browser (desktop, or as a PWA on a tablet or phone); entries appear in the sidebar timeline and auto-follow takes them to each new push.

## When to use

- You're walking through a piece of code — don't quote it inline, push it as a `code` entry with highlights on the lines you want to discuss and bubble annotations explaining the subtle bits.
- You made a change — push as a `diff` with annotations on the lines that matter.
- You're explaining a class hierarchy, a sequence of messages, a state machine, a flowchart, or an entity-relationship — push as a `uml` Mermaid diagram.
- You're showing architecture or how components connect — push as a force-directed `graph`.
- You're explaining a concept that's paragraphs of prose, lists, tables, or fenced code blocks — push as a `note` (markdown).
- You want to spotlight a specific participant/class/state WHILE narrating — push the diagram, then call `zoom_to(text_match=...)` between explanatory messages.

## How to drive it

Buildspace needs its token. On the machine running the server, the client reads `~/.buildspace/token` by itself, so there's nothing to do. Driving a server on another machine needs `BUILDSPACE_TOKEN` (plus `BUILDSPACE_URL`), and only the user can give you that: ask them to run `buildspace token` there. A 401 means the token is missing or stale. Never print the token back into the chat or into a pushed entry.

```python
import buildspace

# Code view — syntax highlighted, line highlights, bubble annotations in the margin
buildspace.code(
    path="/path/to/file.py",                 # server reads from disk
    # OR content="literal string",           # for snippets
    title="optional title",
    language="python",                       # auto-detected from extension if omitted
    highlights=[10, 11, 12],                 # 1-based line numbers
    annotations=[
        {"line": 10, "kind": "note",  "text": "what this line does"},
        {"line": 11, "kind": "warn",  "text": "subtle caveat"},
        {"line": 12, "kind": "ok",    "text": "why it's right now"},
    ],
)

# Diff — before/after with +/- highlighting, same annotation machinery
buildspace.diff(
    path="file.py",                          # for display/language
    before="old source string",
    after="new source string",               # or omit and server reads from path
    title="what changed",
    highlights=[3, 4],                       # after-line numbers
    annotations=[{"line": 3, "kind": "note", "text": "..."}],
)

# UML via Mermaid — any diagram type: sequenceDiagram, classDiagram,
# stateDiagram-v2, flowchart, erDiagram, gantt, journey, mindmap
buildspace.uml(
    title="How a request flows",
    mermaid="""sequenceDiagram
    participant Claude
    participant Server
    Claude->>Server: POST /foo
    Server-->>Claude: 200 OK
""",
    annotations=[
        # text_match searches both <text> and <foreignObject> in the rendered SVG
        {"text_match": "Claude", "text": "starts here", "kind": "note"},
        # or use CSS selector directly: {"selector": "g#some-id", "text": "..."}
    ],
)

# Force-directed physics graph (vis-network)
buildspace.graph(
    title="Module relationships",
    nodes=[
        {"id": "a", "label": "module A", "group": "class"},    # groups: class, service, interface, db, external
        {"id": "b", "label": "module B", "group": "db"},
    ],
    edges=[
        {"source": "a", "target": "b", "label": "reads"},
    ],
    highlights=["a"],                        # ids that glow amber
    annotations=[{"node": "a", "text": "entry point", "kind": "note"}],
)

# Markdown note — prose, lists, tables, fenced code blocks, blockquotes
buildspace.note(
    title="What this means",
    markdown="""# Heading
Paragraph with **emphasis** and `inline code`.

## Steps
1. First
2. Second

```python
def example():
    pass
```
""",
)
```

## Live control while narrating

```python
# Grow a force graph step-by-step (animates via vis.DataSet in place)
buildspace.add_node("cache", label="Redis", group="db")
buildspace.add_edge("server", "cache", label="writes")
buildspace.remove_node("cache")
buildspace.highlight(["server", "hub"])        # emphasise specific ids

# Drive mermaid diagram viewport remotely — the view glides over
buildspace.zoom_to(text_match="Hub", scale=2.6)   # spotlight an element
buildspace.zoom_reset()                            # back to full view
buildspace.zoom_point(x=200, y=300, scale=2.5)    # explicit container-local point

# Force tab/auto-follow state
buildspace.tab("code" | "diff" | "graph" | "note")
buildspace.autofollow(True)    # force the user's view to follow
buildspace.autofollow(False)   # let them browse freely; pushes stack in timeline
buildspace.clear()             # wipe everything
```


## Reading the user's annotations

The user can tap any paragraph in a `note`, or any element of a `uml` or `graph` diagram (including empty space), and leave a comment. It pins as a bubble tinted with the accent colour, distinct from your own pushed annotations. You fetch their comments back via the Python API:

```python
annots = buildspace.get_annotations(entry)   # pass entry dict or int id
# each annotation:
# {
#   "id": int,
#   "entry_id": int,
#   "comment": str,              # what they wrote
#   "block_preview": str | None, # what they tapped: paragraph text, diagram label, or node label
#   "block_index": int,          # notes: 0-based index of the paragraph; diagrams: -1
#   "line_index": int | None,    # notes: table row / list item within the block
#   "anchor": dict | None,       # diagrams only, see below
#   "kind": str | None,
#   "created_at": float,
# }
```

**Notes:** `block_index` is positional within the rendered note. Block 0 is the first top-level element, block 1 the next, and so on. Notes are immutable entries, so indices are stable for the life of the entry. `block_preview` is the first ~80 chars of the paragraph they annotated. Use it to check you're matching the right block.

**Diagrams:** `anchor` says where the comment points.
- `uml`: `{"label", "nth", "x", "y"}`. `label` is the text of the element they tapped (a participant, class, node or message), and `nth` says which occurrence of that text it was. `label` is None when they tapped something unlabeled (an arrow, a lifeline, empty space). Then `x`/`y`, the tap point in diagram coordinates, is the only locator. Read those comments as "about this area of the diagram".
- `graph`: `{"node": id}` for a node, or `{"x", "y"}` (canvas coordinates) for a tap elsewhere.

**Threads:** each annotation has `messages`, the conversation under it: the user's follow-ups (`author: "user"`) and the responder's answers (`author: "agent"`, `status` pending/done/error). If the server runs a responder, a copy of your session may already have answered the user in the thread. Read it before replying, so you don't contradict it or repeat it. The copy can't change anything, so requests for changes it relayed are yours to do.

**When to check:**
- After pushing a note or diagram and handing back to the user. Check again on your next turn in case they left feedback while you were waiting.
- When they reference something they "wrote on" or "marked up". Fetch and read before replying.
- Nothing notifies you when the user annotates. Make checking a habit: on any turn after you pushed a note or diagram, call `get_annotations()` before assuming there's no feedback.

Code and diff annotations are still one-way (you push, they read). (`get_note_annotations` is the old name and still works.)

## Linking prose to supporting diagrams and code

A markdown `note` can link to other timeline entries so the reader can click through. Links render as amber pill buttons with a ↗ glyph; clicking one jumps to that entry (and disables auto-follow so reading doesn't get interrupted).

### Use the Python link helpers — don't hand-write the URL fragments

There are three specific markdown pitfalls that break these links silently:
1. **Whitespace around the URL** — `[x]( #entry-42 )` is treated as literal text by marked.js, not a link.
2. **Spaces inside a `#title:` destination** — CommonMark disallows them; marked.js splits the URL at the first space.
3. **Closing `]` in the label** — breaks the link syntax.

Don't try to remember those. Use `buildspace.link(...)`, which escapes the label and URL-encodes the destination for you:

```python
import buildspace

# 1. Push supporting material first — each call returns the entry dict
arch = buildspace.uml(
    title="Request flow",
    mermaid="sequenceDiagram\n    participant User\n    participant Server\n    User->>Server: GET /\n",
)
hot_path = buildspace.code(path="server.py", title="Request handler", highlights=[12, 13, 14])

# 2. Then push the note, using `buildspace.link()` for every cross-reference
buildspace.note(
    title="How the request flow works",
    markdown=f"""# How the request flow works

The request lifecycle is {buildspace.link("shown in this sequence diagram", entry=arch)}.
The dispatcher lives in {buildspace.link("server.py", entry=hot_path)}.

You can also link generically without knowing the id:
- {buildspace.link("the most recent diagram", latest="uml")}
- {buildspace.link("the analysis output", title="Request flow")}
""",
)
```

`buildspace.link(label, *, entry=..., latest=..., title=...)` returns a complete `[label](url)` markdown fragment. Exactly one of `entry`, `latest`, or `title` must be supplied.

**Lower-level URL-only helpers** (if you want to build the markdown yourself):
- `buildspace.link_entry(entry_or_id)` → `#entry-42`
- `buildspace.link_latest(kind)` → `#latest:uml` (or `#latest:code,diff` if you pass a list)
- `buildspace.link_title(needle)` → `#title:Request%20flow` (%-encoded)

**Destination forms resolved by the client (what these helpers produce):**

| Form | Meaning |
|---|---|
| `#entry-42` | Jump to numeric entry id 42 |
| `#latest:uml` | Most recent UML entry (excluding the current note) |
| `#latest:code,diff` | Most recent entry of any listed kind |
| `#title:foo%20bar` | First entry whose title (case-insensitive substring) contains "foo bar". %-encoded spaces are decoded client-side. |

**Raw strings work too, but only if you get the escaping exactly right.** When in doubt, use the helpers.

**Use linking when:**
- You're writing an executive-summary note that refers to several supporting visualisations — add pill links so the user can drill in
- You're building a multi-part walkthrough and the prose is the outline
- You want to point the user at a specific piece of code or diagram while narrating in prose

**Don't use linking when** the diagram is already the main thing — just push it directly. Over-linking adds clutter. A note with 10 pill links is a sign you should've used a different content type.

## Notes on style

- **Bubble annotations** float in a dedicated side/margin area. Mermaid sequence diagrams get above-the-participant bubbles; class/state/flow diagrams get side-of-box bubbles. Both track pan/zoom. Keep annotation text short — aim for 6–12 words.
- **Highlights** on code or diffs draw the eye; use them sparingly for the lines you actually want to discuss.
- **Diagram kind detection** on Mermaid is automatic from the first keyword. Mermaid's `sequenceDiagram`, `classDiagram { }`, `stateDiagram-v2`, `flowchart LR`, `erDiagram`, etc. all work.
- **Force graphs** use groups for colour: `class` (amber), `service` (pink), `interface` (green), `db` (blue), `external` (gray). Pick the group that fits the semantics.
- **auto-follow** is on by default. If you're pushing many entries in rapid succession, consider `autofollow(False)` first so the user can browse at their own pace; re-enable before the payoff.

## ⚠️ `buildspace.clear()` wipes LIVE user data

The timeline belongs to the user — they are watching it. `buildspace.clear()` nukes every entry currently in it, and SQLite persistence doesn't save you because the deletes hit the DB immediately. **Do not call `clear()` from smoke tests, debug scripts, or exploratory probes against the live server.** If you need a clean slate for testing, snapshot the DB file first:

```python
import shutil
from pathlib import Path

DB = Path.home() / ".buildspace" / "buildspace.db"
BACKUP = Path("/tmp/buildspace-db-backup.sqlite")

shutil.copy2(DB, BACKUP)
try:
    # ... your test that calls clear() and pushes fixtures ...
finally:
    shutil.copy2(BACKUP, DB)
```

Or better: run against a second Buildspace instance on a different port bound to a different DB file (`BUILDSPACE_PORT=8098 BUILDSPACE_DB=/tmp/bs-test.db BUILDSPACE_TOKEN_FILE=/tmp/bs-test.token scripts/run.sh`, then `BUILDSPACE_URL=http://127.0.0.1:8098 BUILDSPACE_TOKEN_FILE=/tmp/bs-test.token`).

## Don't use /explain for

- Quick one-liner answers — just say them in chat.
- Acknowledgments, short status updates, or questions back to the user.
- Anything the user is already looking at (duplication, not insight).

## Project source

FastAPI + Pydantic + SQLite + vanilla JS frontend (highlight.js, marked, vis-network, mermaid, panzoom). See the README in the Buildspace repository for the full HTTP API, configuration and security notes.
