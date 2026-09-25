# Copyright 2026 Reality Checkpoint
# SPDX-License-Identifier: Apache-2.0
import os
from pathlib import Path
from typing import Any, Union
from urllib.parse import quote

import httpx

from buildspace import auth

BASE_URL = os.environ.get("BUILDSPACE_URL", "http://127.0.0.1:8097")
TIMEOUT = 5.0


def _headers() -> dict:
    # Same machine as the server: read its token file. Elsewhere: the user
    # hands over the token via BUILDSPACE_TOKEN.
    token = auth.read_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _post(path: str, payload: dict) -> dict:
    r = httpx.post(f"{BASE_URL}{path}", json=payload, headers=_headers(), timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _get(path: str) -> Any:
    r = httpx.get(f"{BASE_URL}{path}", headers=_headers(), timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def code(
    path: str | Path | None = None,
    *,
    content: str | None = None,
    language: str | None = None,
    title: str | None = None,
    highlights: list[int] | None = None,
    annotations: list[dict] | None = None,
) -> dict:
    """Show a source file. Pass path (server reads it) or content directly.

    annotations: list of {"line": int, "text": str, "kind": "note"|"warn"|"ok"}
    highlights: list of 1-based line numbers to emphasize.
    """
    body: dict = {}
    if path is not None:
        body["path"] = str(path)
    if content is not None:
        body["content"] = content
    if language:
        body["language"] = language
    if title:
        body["title"] = title
    if highlights:
        body["highlights"] = list(highlights)
    if annotations:
        body["annotations"] = annotations
    return _post("/api/code", body)


def diff(
    path: str | Path | None = None,
    *,
    before: str,
    after: str | None = None,
    language: str | None = None,
    title: str | None = None,
    highlights: list[int] | None = None,
    annotations: list[dict] | None = None,
) -> dict:
    """Show a unified diff.

    Provide `before` (required); `after` is either the new content or
    omitted, in which case the server reads `path` from disk.
    `highlights` and `annotations` reference *after*-file line numbers.
    """
    body: dict = {"before": before}
    if path is not None:
        body["path"] = str(path)
    if after is not None:
        body["after"] = after
    if language:
        body["language"] = language
    if title:
        body["title"] = title
    if highlights:
        body["highlights"] = list(highlights)
    if annotations:
        body["annotations"] = annotations
    return _post("/api/diff", body)


def graph(
    nodes: list[dict],
    edges: list[dict] | None = None,
    *,
    title: str | None = None,
    highlights: list[str] | None = None,
    layout: str | None = None,
    annotations: list[dict] | None = None,
) -> dict:
    """Show a force-directed relationship graph.

    nodes: list of {"id": str, "label": str, "group"?, "color"?, "shape"?, "size"?, "title"?}
    edges: list of {"source": str, "target": str, "label"?, "kind"?, "dashes"?}
    highlights: list of node ids to emphasize.
    annotations: list of {"node": str, "text": str, "kind"?} — bubbles anchored to nodes.
    """
    body: dict = {"nodes": nodes, "edges": edges or []}
    if title:
        body["title"] = title
    if highlights:
        body["highlights"] = list(highlights)
    if layout:
        body["layout"] = layout
    if annotations:
        body["annotations"] = annotations
    return _post("/api/graph", body)


def graph_patch(
    *,
    add_nodes: list[dict] | None = None,
    update_nodes: list[dict] | None = None,
    remove_nodes: list[str] | None = None,
    add_edges: list[dict] | None = None,
    remove_edges: list[dict] | None = None,
    highlights: list[str] | None = None,
    focus: str | None = None,
) -> dict:
    """Apply an incremental patch to the most-recent graph entry.

    Every change animates in the existing vis-network layout instead of
    re-rendering, so you can build or evolve a graph live.
    """
    body: dict = {}
    if add_nodes is not None:
        body["add_nodes"] = add_nodes
    if update_nodes is not None:
        body["update_nodes"] = update_nodes
    if remove_nodes is not None:
        body["remove_nodes"] = remove_nodes
    if add_edges is not None:
        body["add_edges"] = add_edges
    if remove_edges is not None:
        body["remove_edges"] = remove_edges
    if highlights is not None:
        body["highlights"] = highlights
    if focus is not None:
        body["focus"] = focus
    return _post("/api/graph/patch", body)


def add_node(
    node_id: str,
    *,
    label: str | None = None,
    group: str | None = None,
    color: str | None = None,
    shape: str | None = None,
    size: int | None = None,
    title: str | None = None,
) -> dict:
    node: dict = {"id": node_id}
    for k, v in (
        ("label", label),
        ("group", group),
        ("color", color),
        ("shape", shape),
        ("size", size),
        ("title", title),
    ):
        if v is not None:
            node[k] = v
    return graph_patch(add_nodes=[node])


def add_edge(
    source: str,
    target: str,
    *,
    label: str | None = None,
    kind: str | None = None,
    dashes: bool | None = None,
) -> dict:
    edge: dict = {"source": source, "target": target}
    for k, v in (("label", label), ("kind", kind), ("dashes", dashes)):
        if v is not None:
            edge[k] = v
    return graph_patch(add_edges=[edge])


def remove_node(node_id: str) -> dict:
    return graph_patch(remove_nodes=[node_id])


def highlight(node_ids: list[str]) -> dict:
    return graph_patch(highlights=node_ids)


def focus(node_id: str) -> dict:
    return graph_patch(focus=node_id)


# ----- Markdown link builders for note() ------------------------------------
#
# Raw `[label](#entry-N)` inside note markdown is fragile:
#   * whitespace around the URL (`[x]( #entry-N )`) breaks the link
#   * spaces inside a `#title:...` destination break the link unless the URL
#     is %-encoded or wrapped in angle brackets
#   * closing `]` in the label breaks the link unless escaped
# These helpers produce correctly-escaped fragments so callers can drop them
# into f-string markdown without caring about the pitfalls.


def link_entry(entry_or_id: Union[dict, int]) -> str:
    """Return the URL fragment pointing to a specific entry id."""
    if isinstance(entry_or_id, dict):
        eid = entry_or_id.get("id")
    else:
        eid = entry_or_id
    if eid is None:
        raise ValueError("entry_or_id must be an entry dict with 'id' or an int")
    return f"#entry-{int(eid)}"


def link_latest(kind: Union[str, list[str]]) -> str:
    """Return the URL fragment pointing to the most-recent entry of a kind.

    `kind` can be a single kind name ("code", "diff", "uml", "graph", "note")
    or a list for a multi-kind fallback (e.g. ["code", "diff"]).
    """
    if isinstance(kind, str):
        kinds = kind
    else:
        kinds = ",".join(kind)
    return f"#latest:{kinds}"


def link_title(needle: str) -> str:
    """Return the URL fragment that matches an entry by a substring of its title.

    The needle is %-encoded so it can safely contain spaces and punctuation.
    """
    return f"#title:{quote(needle, safe=':/,')}"


def link(
    label: str,
    *,
    entry: Union[dict, int, None] = None,
    latest: Union[str, list[str], None] = None,
    title: Union[str, None] = None,
) -> str:
    """Build a ready-to-embed markdown link fragment for a Buildspace note.

    Exactly one of `entry`, `latest`, or `title` must be supplied. Example::

        md = f\"\"\"
        # Summary
        See {buildspace.link("the code", entry=code_entry)} for the details,
        and {buildspace.link("the latest diagram", latest="uml")} for context.
        Or jump to {buildspace.link("the API report", title="API report")}.
        \"\"\"

    The returned string is a `[label](url)` markdown fragment with the
    destination URL properly escaped — safe to embed directly in an
    f-string regardless of whether `label` or `title` contain spaces,
    brackets, or other punctuation.
    """
    specified = [entry is not None, latest is not None, title is not None]
    if sum(specified) != 1:
        raise ValueError(
            "link() needs exactly one of: entry=, latest=, title="
        )
    if entry is not None:
        url = link_entry(entry)
    elif latest is not None:
        url = link_latest(latest)
    else:
        url = link_title(title)  # type: ignore[arg-type]
    # Escape backslashes and closing bracket in the label so markdown stays valid
    safe_label = label.replace("\\", "\\\\").replace("]", "\\]")
    return f"[{safe_label}]({url})"


def video(
    path: str | Path,
    *,
    title: str | None = None,
    notes: str | None = None,
    poster: str | Path | None = None,
) -> dict:
    """Show a video in the Video tab. The file stays where it is on disk;
    Buildspace serves bytes from /media/<entry-id> with HTTP range support
    so HTML5 <video> scrubbing works.

    `notes` is markdown shown under the player — useful for a summary of
    issues, a link back to the source timeline, or a "what changed since
    v3" note. `poster` is an optional still-frame image path.

    For phone / remote / bad-internet viewing, render a modest H.264 MP4
    (720p at ~2.5 Mbps is plenty) and put the moov atom at the head of the
    file (`-movflags +faststart`) so playback starts before the download
    finishes. The `/media/<id>` route serves with HTTP range and immutable
    cache headers, so iOS will resume / re-watch without re-fetching.
    """
    body: dict = {"path": str(Path(path).expanduser())}
    if title:
        body["title"] = title
    if notes:
        body["notes"] = notes
    if poster:
        body["poster"] = str(Path(poster).expanduser())
    return _post("/api/video", body)


def note(
    markdown: str,
    *,
    title: str | None = None,
) -> dict:
    """Show a markdown note — prose, lists, headings, inline/block code.

    Use when the explanation is paragraphs, not a code walkthrough. Fenced
    code blocks get syntax highlighted client-side by highlight.js.
    """
    body: dict = {"markdown": markdown}
    if title:
        body["title"] = title
    return _post("/api/note", body)


def uml(
    mermaid: str,
    *,
    title: str | None = None,
    kind: str | None = None,
    annotations: list[dict] | None = None,
) -> dict:
    """Show a UML diagram authored in Mermaid syntax.

    Any Mermaid diagram type works — sequenceDiagram, classDiagram,
    stateDiagram-v2, flowchart, erDiagram, and so on.

    annotations: bubbles anchored to SVG elements. Each entry is a dict with:
      - `text` (required): the bubble body
      - `selector` (optional): CSS selector inside the rendered SVG
      - `text_match` (optional): shortcut — finds an SVG <text> whose
        content matches and uses its bounding element
      - `kind` (optional): "note" | "warn" | "ok"
      - `offset_x`, `offset_y` (optional): pixel offsets from anchor center
    """
    body: dict = {"mermaid": mermaid}
    if title:
        body["title"] = title
    if kind:
        body["kind"] = kind
    if annotations:
        body["annotations"] = annotations
    return _post("/api/uml", body)


def zoom_to(
    selector: str | None = None,
    *,
    text_match: str | None = None,
    scale: float | str = 2.2,
    padding: float = 0.7,
) -> dict:
    """Spotlight an element in the currently-visible UML diagram.

    Pass either a CSS selector or `text_match` (searches SVG text content).
    Use `scale="fit"` to auto-size so the element fills ~`padding` of the
    viewport.
    """
    if not selector and not text_match:
        raise ValueError("must supply selector or text_match")
    sel = selector
    if not sel and text_match:
        # Let the client resolve this — pass text_match as a pseudo-selector.
        sel = f"text_match:{text_match}"
    return _post(
        "/api/diagram/focus",
        {"mode": "selector", "selector": sel, "scale": scale, "padding": padding},
    )


def zoom_point(x: float, y: float, scale: float = 2.0) -> dict:
    """Zoom to an absolute point in the current UML diagram (container coordinates)."""
    return _post("/api/diagram/focus", {"mode": "point", "x": x, "y": y, "scale": scale})


def zoom_reset() -> dict:
    """Reset zoom/pan on the current UML diagram to fit."""
    return _post("/api/diagram/focus", {"mode": "reset"})


def tab(name: str) -> dict:
    return _post("/api/tab", {"tab": name})


def autofollow(value: bool) -> dict:
    return _post("/api/autofollow", {"value": bool(value)})


def clear() -> dict:
    return _post("/api/clear", {})


def status() -> dict:
    return _get("/api/history")


def get_annotations(entry_or_id: Union[dict, int]) -> list[dict]:
    """Read the viewer's comments on a note, uml or graph entry.

    Each annotation is {id, entry_id, block_index, block_preview, comment,
    kind, created_at, line_index, anchor}. `block_preview` is the first ~80
    chars of what was annotated (block/line text, diagram label or node id).

    Notes: `block_index` is the top-level block; `line_index` is None for
    whole-block annotations and the 0-based row/item index for sub-block
    annotations on table rows or list items. `anchor` is None.

    Diagrams: `block_index` is -1 and `anchor` says where the comment points:
    uml -> {"label": str|None, "nth": int, "x": float, "y": float} (the
    clicked element's label text, which occurrence of it, and the click
    point in diagram space); graph -> {"node": id} or {"x", "y"} for a click
    on empty canvas.
    """
    if isinstance(entry_or_id, dict):
        eid = entry_or_id.get("id")
    else:
        eid = entry_or_id
    if eid is None:
        raise ValueError("entry_or_id must be an entry dict with 'id' or an int")
    data = _get(f"/api/annotations/{int(eid)}")
    return data.get("annotations", [])


# Original name, from when only notes took comments.
get_note_annotations = get_annotations
