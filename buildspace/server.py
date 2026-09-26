# Copyright 2026 Reality Checkpoint
# SPDX-License-Identifier: Apache-2.0
import asyncio
import contextvars
import hashlib
import ipaddress
import json
import os
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import buildspace.diff as diff_mod
from buildspace import auth, db, render, responder

STATIC_DIR = Path(__file__).parent / "static"
TOKEN = auth.load_or_create_token()

# Reachable without the token: the open-source app shell (so an unpaired
# browser can load, find out it's unpaired, and go to /pair) and the pairing
# flow itself. Everything else, including user images dropped in /static,
# needs the Bearer token or a paired-browser cookie.
PUBLIC_PATHS = {
    "/", "/sw.js", "/manifest.json", "/pair", "/api/pair",
    "/static/app.css", "/static/app.js", "/static/index.html",
    "/static/manifest.json", "/static/sw.js",
}


class CodeRequest(BaseModel):
    path: str | None = None
    content: str | None = None
    language: str | None = None
    title: str | None = None
    highlights: list[int] | None = None
    annotations: list[dict] | None = None


class GraphNode(BaseModel):
    id: str
    label: str | None = None
    group: str | None = None
    color: str | None = None
    shape: str | None = None
    size: int | None = None
    title: str | None = None


class GraphEdge(BaseModel):
    source: str
    target: str
    label: str | None = None
    kind: str | None = None
    dashes: bool | None = None


class GraphAnnotation(BaseModel):
    node: str | None = None
    text: str
    kind: str | None = None  # note | warn | ok


class GraphRequest(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge] = []
    title: str | None = None
    highlights: list[str] | None = None
    layout: str | None = None
    annotations: list[GraphAnnotation] | None = None


class GraphPatchRequest(BaseModel):
    add_nodes: list[GraphNode] | None = None
    update_nodes: list[GraphNode] | None = None
    remove_nodes: list[str] | None = None
    add_edges: list[GraphEdge] | None = None
    remove_edges: list[dict] | None = None  # {source, target}
    highlights: list[str] | None = None
    focus: str | None = None


class DiffRequest(BaseModel):
    path: str | None = None
    before: str
    after: str | None = None
    language: str | None = None
    title: str | None = None
    highlights: list[int] | None = None
    annotations: list[dict] | None = None


class NoteRequest(BaseModel):
    markdown: str
    title: str | None = None


class UmlAnnotation(BaseModel):
    selector: str | None = None
    text_match: str | None = None  # convenience: find SVG <text> with this content
    text: str
    kind: str | None = None  # note | warn | ok
    offset_x: int | None = None
    offset_y: int | None = None


class UmlRequest(BaseModel):
    mermaid: str
    title: str | None = None
    kind: str | None = None  # sequence, class, state, flowchart, er, etc.
    annotations: list[UmlAnnotation] | None = None


class ZoomRequest(BaseModel):
    mode: str  # "selector" | "reset" | "point"
    selector: str | None = None
    x: float | None = None
    y: float | None = None
    scale: float | str | None = None  # number or "fit"
    padding: float | None = None


class VideoRequest(BaseModel):
    path: str            # absolute path on the server filesystem
    title: str | None = None
    notes: str | None = None  # markdown shown above the player
    poster: str | None = None  # optional path to a still frame


class NoteAnnotationRequest(BaseModel):
    entry_id: int
    comment: str
    block_index: int | None = None  # note entries: top-level block
    block_preview: str | None = None
    kind: str | None = None  # note | warn | ok
    line_index: int | None = None  # sub-block anchor (table row, list item)
    # uml / graph entries: where on the diagram the comment points. uml:
    # {label, nth, x, y} (label text + which occurrence, with a scene-space
    # point as fallback); graph: {node} or {x, y} in canvas space.
    anchor: dict | None = None
    ask: bool = False  # have the responder answer it


class AnnotationMessageRequest(BaseModel):
    text: str
    ask: bool = False


class TabRequest(BaseModel):
    tab: str


class AutofollowRequest(BaseModel):
    value: bool


class Hub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def add(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.add(ws)

    async def remove(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message)
        async with self._lock:
            targets = list(self._clients)
        for ws in targets:
            try:
                await ws.send_text(payload)
            except Exception:
                await self.remove(ws)


hub = Hub()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    db.fail_pending_messages("Interrupted: Buildspace restarted before the answer came back. Ask again.")
    yield


app = FastAPI(lifespan=lifespan)


# ---------------------------------------------------------------------------
# Request-origin guard.
#
# Buildspace has no authentication, so the browser's same-origin policy is the
# only thing standing between "a web page you happen to have open" and your
# timeline. Two gaps need closing explicitly:
#
#   * WebSocket handshakes are not subject to same-origin policy. Without a
#     check, any page could open ws://127.0.0.1:8097/ws and read every push.
#   * DNS rebinding: a page on attacker.example can point that name at
#     127.0.0.1 and then talk to us with Host: attacker.example, which the
#     browser treats as same-origin. Body-less POSTs (/api/clear) also need
#     no CORS preflight at all.
#
# So: the Host header must name this machine (loopback, an IP literal, the
# machine's own hostname, or something in BUILDSPACE_ALLOWED_HOSTS), and if a
# browser sends an Origin header on a mutating request or a WebSocket, that
# Origin must pass the same test. Requests without an Origin (curl, the
# Python client) are unaffected.
# ---------------------------------------------------------------------------

def _configured_hosts() -> set[str]:
    names = {"localhost", "127.0.0.1", "::1"}
    try:
        hn = socket.gethostname().lower()
        names.add(hn)
        names.add(hn.split(".")[0])
        names.add(hn.split(".")[0] + ".local")
    except OSError:
        pass
    for extra in os.environ.get("BUILDSPACE_ALLOWED_HOSTS", "").split(","):
        extra = extra.strip().lower()
        if extra:
            names.add(extra)
    return names


ALLOWED_HOSTS = _configured_hosts()


def _hostname_of(value: str) -> str:
    """Strip scheme, port and brackets from a Host or Origin header value."""
    v = value.strip().lower()
    if "://" in v:
        v = v.split("://", 1)[1]
    v = v.split("/", 1)[0]
    if v.startswith("["):
        return v[1:].split("]", 1)[0]
    return v.rsplit(":", 1)[0] if v.count(":") == 1 else v


def _host_allowed(value: str | None) -> bool:
    if not value:
        return False
    h = _hostname_of(value)
    if h in ALLOWED_HOSTS:
        return True
    try:
        ipaddress.ip_address(h)
        return True  # an IP literal cannot be DNS-rebound
    except ValueError:
        return False


def _origin_allowed(origin: str | None) -> bool:
    if origin is None or origin == "null":
        return origin is None  # "null" origin (sandboxed iframe, file://) is rejected
    return _host_allowed(origin)


@app.middleware("http")
async def origin_guard(request: Request, call_next):
    if not _host_allowed(request.headers.get("host")):
        return JSONResponse(
            status_code=403,
            content={"detail": "Host header not allowed. Add it to BUILDSPACE_ALLOWED_HOSTS."},
        )
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin is not None and not _origin_allowed(origin):
            return JSONResponse(status_code=403, content={"detail": "cross-origin request refused"})
    if not _is_public(request.url.path) and not _authorized(request.headers, request.cookies):
        return JSONResponse(
            status_code=401,
            content={"detail": "Buildspace token required. Agents: set BUILDSPACE_TOKEN "
                               "(or share ~/.buildspace/token). Browsers: open /pair."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    _origin_var.set(_parse_origin(request.headers.get("x-buildspace-origin")))
    return await call_next(request)


# The pushing agent describes where it's running (session id, cwd,
# transcript path) in X-Buildspace-Origin; it's stored with the entry so a
# responder can answer comments from inside that session.
_origin_var: contextvars.ContextVar[dict | None] = contextvars.ContextVar("origin", default=None)
_ORIGIN_KEYS = {"session_id": 64, "cwd": 1024, "transcript": 1024, "host": 255, "agent": 64}


def _parse_origin(raw: str | None) -> dict | None:
    if not raw or len(raw) > 4096:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    out = {k: v for k, v in data.items()
           if k in _ORIGIN_KEYS and isinstance(v, str) and 0 < len(v) <= _ORIGIN_KEYS[k]}
    return out or None


def _is_public(path: str) -> bool:
    if ".." in path:
        return False
    if path in PUBLIC_PATHS:
        return True
    name = path.rsplit("/", 1)[-1]
    return path.startswith("/static/icons/") and name.startswith("icon-") and name.endswith(".png")


def _authorized(headers, cookies) -> bool:
    return auth.check_bearer(headers.get("authorization"), TOKEN) or auth.check_cookie(
        cookies.get(auth.COOKIE_NAME), TOKEN
    )


def _sanitize_for_json(obj):
    """Strip/replace bytes that aren't UTF-8 decodable, recursively, so
    FastAPI's default jsonable_encoder doesn't crash on binary request bodies
    (e.g. when a client mistakenly sends multipart to a JSON endpoint)."""
    if isinstance(obj, bytes):
        try:
            return obj.decode("utf-8")
        except UnicodeDecodeError:
            return f"<{len(obj)} bytes of non-utf8 data>"
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    return obj


@app.exception_handler(render.PathNotAllowed)
async def path_not_allowed_handler(request: Request, exc: render.PathNotAllowed):
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(render.FileTooLarge)
async def file_too_large_handler(request: Request, exc: render.FileTooLarge):
    return JSONResponse(status_code=413, content={"detail": str(exc)})


@app.exception_handler(render.NotAFile)
async def not_a_file_handler(request: Request, exc: render.NotAFile):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(FileNotFoundError)
async def file_not_found_handler(request: Request, exc: FileNotFoundError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    # Default handler calls jsonable_encoder on exc.errors(), which includes
    # the raw request body in the `input` field. If a client sent binary data
    # to a JSON endpoint, jsonable_encoder's bytes handler does o.decode()
    # (utf-8) and crashes, turning a 422 into a 500. Sanitize first.
    return JSONResponse(
        status_code=422,
        content={"detail": _sanitize_for_json(exc.errors())},
    )


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    # Stamp the app's CSS and JS URLs with a hash of their contents, so a
    # browser (or the service worker's cache) can never pair a new page
    # with an old stylesheet.
    html = (STATIC_DIR / "index.html").read_text()
    for name in ("app.css", "app.js"):
        digest = hashlib.sha1((STATIC_DIR / name).read_bytes()).hexdigest()[:10]
        html = html.replace(f'"/static/{name}"', f'"/static/{name}?v={digest}"')
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/sw.js")
async def service_worker() -> Response:
    return Response(
        (STATIC_DIR / "sw.js").read_text(),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@app.get("/manifest.json")
async def manifest() -> Response:
    return Response(
        (STATIC_DIR / "manifest.json").read_text(),
        media_type="application/manifest+json",
    )


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


PAIR_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pair · Buildspace</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center;
         background: #0f1117; color: #d7dbe3; font: 15px/1.5 -apple-system, system-ui, sans-serif; }
  form { width: min(420px, calc(100vw - 32px)); display: grid; gap: 12px; }
  h1 { font-size: 20px; margin: 0; }
  p { margin: 0; color: #8b93a4; }
  code { color: #e8b87d; }
  input { padding: 10px 12px; border-radius: 6px; border: 1px solid #333a4a;
          background: #161a24; color: inherit; font: 14px ui-monospace, monospace; }
  button { padding: 10px; border: 0; border-radius: 6px; background: #e8b87d;
           color: #1a1310; font-weight: 600; font-size: 14px; cursor: pointer; }
  .err { color: #ff8b8b; min-height: 1.5em; }
</style></head>
<body><form id="f">
  <h1>Pair this browser</h1>
  <p>Paste the token from the machine running Buildspace. Get it there with
     <code>buildspace token</code>, or run <code>buildspace pair</code> for a link.</p>
  <input id="t" type="password" autocomplete="off" placeholder="token" autofocus>
  <button>Pair</button>
  <div class="err" id="e"></div>
</form>
<script>
  const f = document.getElementById("f"), t = document.getElementById("t"), e = document.getElementById("e");
  async function pair(token) {
    const r = await fetch("/api/pair", { method: "POST", headers: { "Content-Type": "application/json" },
                                         body: JSON.stringify({ token }) });
    if (r.ok) { location.replace("/"); return; }
    e.textContent = "That token didn't match.";
  }
  f.addEventListener("submit", (ev) => { ev.preventDefault(); if (t.value.trim()) pair(t.value.trim()); });
  // Pairing links carry the token in the fragment, which never reaches the
  // server's logs or a proxy. Clear it from the address bar right away.
  function pairFromHash() {
    if (location.hash.length <= 1) return;
    const token = decodeURIComponent(location.hash.slice(1));
    history.replaceState(null, "", "/pair");
    pair(token);
  }
  pairFromHash();
  addEventListener("hashchange", pairFromHash);  // link opened while already on /pair
</script></body></html>
"""


class PairRequest(BaseModel):
    token: str


@app.get("/pair", response_class=HTMLResponse)
async def pair_page() -> HTMLResponse:
    return HTMLResponse(PAIR_PAGE, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@app.post("/api/pair")
async def pair(req: PairRequest, request: Request) -> Response:
    if not auth.check_bearer(f"Bearer {req.token}", TOKEN):
        raise HTTPException(401, "token mismatch")
    resp = JSONResponse({"ok": True})
    # Secure only when the browser is on HTTPS (e.g. behind Tailscale Serve);
    # a Secure cookie set over plain http would be dropped.
    https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    resp.set_cookie(
        auth.COOKIE_NAME,
        auth.session_value(TOKEN),
        max_age=400 * 24 * 3600,  # browsers cap cookie lifetime at 400 days
        httponly=True,
        samesite="lax",
        secure=https,
        path="/",
    )
    return resp


@app.get("/api/history")
async def history() -> dict:
    return {"entries": db.list_entries()}


@app.get("/api/entry/{entry_id}")
async def entry(entry_id: int) -> dict:
    e = db.get_entry(entry_id)
    if e is None:
        raise HTTPException(404, "entry not found")
    return e


@app.post("/api/code")
async def post_code(req: CodeRequest) -> dict:
    if req.content is None and req.path is None:
        raise HTTPException(400, "must supply content or path")
    content = req.content
    filename = None
    if content is None:
        content, filename = render.read_file(req.path)
    if filename is None and req.path:
        filename = Path(req.path).name

    language = req.language or render.guess_language(filename)

    payload = {
        "path": req.path,
        "filename": filename,
        "language": language,
        "content": content,
        "highlights": req.highlights or [],
        "annotations": req.annotations or [],
        "line_count": content.count("\n") + 1,
    }
    title = req.title or filename or "code"
    e = db.add_entry("code", title, payload, origin=_origin_var.get())
    await hub.broadcast({"type": "entry_added", "entry": e})
    return e


@app.post("/api/diff")
async def post_diff(req: DiffRequest) -> dict:
    after = req.after
    filename = None
    if after is None:
        if req.path is None:
            raise HTTPException(400, "must supply after content or path")
        after, filename = render.read_file(req.path)
    if filename is None and req.path:
        filename = Path(req.path).name

    language = req.language or render.guess_language(filename)
    rows = diff_mod.compute_diff(req.before, after)
    adds = sum(1 for r in rows if r["type"] == "add")
    removes = sum(1 for r in rows if r["type"] == "remove")

    payload = {
        "path": req.path,
        "filename": filename,
        "language": language,
        "rows": rows,
        "highlights": req.highlights or [],
        "annotations": req.annotations or [],
        "stats": {"add": adds, "remove": removes},
    }
    title = req.title or filename or "diff"
    e = db.add_entry("diff", title, payload, origin=_origin_var.get())
    await hub.broadcast({"type": "entry_added", "entry": e})
    return e


@app.post("/api/graph")
async def post_graph(req: GraphRequest) -> dict:
    payload = {
        "nodes": [n.model_dump(exclude_none=True) for n in req.nodes],
        "edges": [e.model_dump(exclude_none=True) for e in req.edges],
        "highlights": req.highlights or [],
        "annotations": [a.model_dump(exclude_none=True) for a in (req.annotations or [])],
        "layout": req.layout or "force",
        "stats": {"nodes": len(req.nodes), "edges": len(req.edges)},
    }
    title = req.title or f"graph ({len(req.nodes)} nodes)"
    e = db.add_entry("graph", title, payload, origin=_origin_var.get())
    await hub.broadcast({"type": "entry_added", "entry": e})
    return e


@app.post("/api/graph/patch")
async def patch_graph(req: GraphPatchRequest) -> dict:
    entry = db.latest_of_kind("graph")
    if entry is None:
        raise HTTPException(400, "no graph to patch — call /api/graph first")

    payload = entry["payload"]
    nodes: list[dict] = payload.get("nodes", [])
    edges: list[dict] = payload.get("edges", [])

    patch: dict = {"target_id": entry["id"]}

    if req.remove_nodes:
        remove = set(req.remove_nodes)
        nodes = [n for n in nodes if n["id"] not in remove]
        edges = [e for e in edges if e["source"] not in remove and e["target"] not in remove]
        patch["remove_nodes"] = list(remove)

    if req.update_nodes:
        updates = {n.id: n.model_dump(exclude_none=True) for n in req.update_nodes}
        merged = []
        for n in nodes:
            if n["id"] in updates:
                merged.append({**n, **updates[n["id"]]})
            else:
                merged.append(n)
        nodes = merged
        patch["update_nodes"] = list(updates.values())

    if req.add_nodes:
        existing_ids = {n["id"] for n in nodes}
        adds = []
        for n in req.add_nodes:
            d = n.model_dump(exclude_none=True)
            if d["id"] in existing_ids:
                # Upsert: treat as update.
                nodes = [{**x, **d} if x["id"] == d["id"] else x for x in nodes]
            else:
                nodes.append(d)
                adds.append(d)
        if adds:
            patch["add_nodes"] = adds

    if req.remove_edges:
        keys = {(e.get("source"), e.get("target")) for e in req.remove_edges}
        edges = [e for e in edges if (e["source"], e["target"]) not in keys]
        patch["remove_edges"] = [list(k) for k in keys]

    if req.add_edges:
        adds = [e.model_dump(exclude_none=True) for e in req.add_edges]
        edges.extend(adds)
        patch["add_edges"] = adds

    if req.highlights is not None:
        payload["highlights"] = list(req.highlights)
        patch["highlights"] = list(req.highlights)

    if req.focus:
        patch["focus"] = req.focus

    payload["nodes"] = nodes
    payload["edges"] = edges
    payload["stats"] = {"nodes": len(nodes), "edges": len(edges)}
    db.update_payload(entry["id"], payload)

    await hub.broadcast({"type": "graph_patch", "patch": patch})
    return {"ok": True, "entry_id": entry["id"], "stats": payload["stats"]}


@app.post("/api/uml")
async def post_uml(req: UmlRequest) -> dict:
    src = req.mermaid.strip()
    diagram_kind = req.kind
    if not diagram_kind:
        first = src.split("\n", 1)[0].strip().lower()
        for key in ("sequencediagram", "classdiagram", "statediagram",
                    "flowchart", "graph", "erdiagram", "gantt",
                    "journey", "mindmap", "timeline", "pie"):
            if first.startswith(key):
                diagram_kind = key.replace("diagram", "")
                break
    payload = {
        "mermaid": src,
        "kind": diagram_kind or "diagram",
        "annotations": [a.model_dump(exclude_none=True) for a in (req.annotations or [])],
    }
    title = req.title or f"UML — {diagram_kind}" if diagram_kind else (req.title or "UML")
    e = db.add_entry("uml", title, payload, origin=_origin_var.get())
    await hub.broadcast({"type": "entry_added", "entry": e})
    return e


@app.post("/api/video")
async def post_video(req: VideoRequest) -> dict:
    """Register a video entry. The video file stays on disk where it is —
    only its path is stored. /media/<id> serves the bytes with HTTP range
    support so HTML5 <video> scrubbing works."""
    src = render.resolve_readable(req.path)
    if not src.exists():
        raise HTTPException(404, f"video file not found: {src}")
    if not src.is_file():
        raise HTTPException(400, f"not a regular file: {src}")
    # Probe metadata so the UI can show duration / dimensions without
    # hitting the file again.
    meta = _probe_video(src)
    poster = None
    if req.poster:
        poster_p = render.resolve_readable(req.poster)
        if poster_p.exists() and poster_p.is_file():
            poster = str(poster_p)
    payload = {
        "path": str(src),
        "filename": src.name,
        "size_bytes": src.stat().st_size,
        "notes": req.notes or "",
        "poster": poster,
        "duration": meta.get("duration"),
        "width": meta.get("width"),
        "height": meta.get("height"),
        "video_codec": meta.get("video_codec"),
        "audio_codec": meta.get("audio_codec"),
    }
    title = req.title or src.name
    e = db.add_entry("video", title, payload, origin=_origin_var.get())
    await hub.broadcast({"type": "entry_added", "entry": e})
    return e


@app.get("/media/{entry_id}")
async def media(entry_id: int, request: Request):
    """Serve a video entry's underlying file with HTTP range support.
    Starlette's FileResponse handles Range headers automatically, which
    is what HTML5 <video> needs for seeking."""
    e = db.get_entry(entry_id)
    if e is None:
        raise HTTPException(404, "entry not found")
    if e["kind"] != "video":
        raise HTTPException(400, f"entry {entry_id} is not a video (kind={e['kind']})")
    path_str = e["payload"].get("path")
    if not path_str:
        raise HTTPException(404, "entry has no path")
    try:
        p = render.resolve_readable(path_str)
    except render.PathNotAllowed as exc:
        raise HTTPException(403, str(exc))
    if not p.exists():
        raise HTTPException(404, f"file no longer exists: {p}")
    # Guess media type from extension; default to video/mp4
    suffix = p.suffix.lower()
    media_type = {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".m4v": "video/mp4",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
    }.get(suffix, "video/mp4")
    # Aggressive caching: an entry's underlying file path is immutable for
    # the life of the entry, so the bytes can be cached forever. iOS Safari
    # honors this on rewatch.
    return FileResponse(
        p, media_type=media_type, filename=p.name,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/media/{entry_id}/poster")
async def media_poster(entry_id: int):
    """Serve the optional poster image for a video entry."""
    e = db.get_entry(entry_id)
    if e is None:
        raise HTTPException(404, "entry not found")
    poster = e["payload"].get("poster")
    if not poster:
        raise HTTPException(404, "no poster")
    try:
        p = render.resolve_readable(poster)
    except render.PathNotAllowed as exc:
        raise HTTPException(403, str(exc))
    if not p.exists():
        raise HTTPException(404, "poster file missing")
    return FileResponse(p)


def _find_ffprobe() -> str | None:
    """Locate ffprobe even when PATH is the bare launchd default."""
    import shutil as _shutil
    hit = _shutil.which("ffprobe")
    if hit:
        return hit
    for candidate in ("/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe", "/usr/bin/ffprobe"):
        if Path(candidate).exists():
            return candidate
    return None


def _probe_video(path: Path) -> dict:
    """Best-effort ffprobe metadata extraction. Returns {} on any failure
    so the entry still registers even if ffprobe is missing."""
    import subprocess as _sub
    ffprobe = _find_ffprobe()
    if not ffprobe:
        return {}
    try:
        out = _sub.check_output([
            ffprobe, "-v", "error",
            "-show_entries", "stream=codec_name,codec_type,width,height",
            "-show_entries", "format=duration",
            "-of", "json", str(path),
        ], text=True, timeout=10)
    except (_sub.SubprocessError, OSError):
        return {}
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return {}
    info: dict = {}
    fmt = data.get("format") or {}
    if "duration" in fmt:
        try:
            info["duration"] = float(fmt["duration"])
        except (TypeError, ValueError):
            pass
    for s in data.get("streams", []) or []:
        ctype = s.get("codec_type")
        if ctype == "video" and "video_codec" not in info:
            info["video_codec"] = s.get("codec_name")
            if "width" in s: info["width"] = s["width"]
            if "height" in s: info["height"] = s["height"]
        elif ctype == "audio" and "audio_codec" not in info:
            info["audio_codec"] = s.get("codec_name")
    return info


@app.post("/api/note")
async def post_note(req: NoteRequest) -> dict:
    # Derive a title from the first markdown heading if not supplied.
    title = req.title
    if not title:
        for line in req.markdown.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                title = stripped.lstrip("#").strip()
                break
    if not title:
        title = "note"

    payload = {
        "markdown": req.markdown,
        "length": len(req.markdown),
    }
    e = db.add_entry("note", title, payload, origin=_origin_var.get())
    await hub.broadcast({"type": "entry_added", "entry": e})
    return e


@app.post("/api/diagram/focus")
async def post_diagram_focus(req: ZoomRequest) -> dict:
    msg: dict = {"type": "diagram_focus", "mode": req.mode}
    if req.selector is not None:
        msg["selector"] = req.selector
    if req.x is not None:
        msg["x"] = req.x
    if req.y is not None:
        msg["y"] = req.y
    if req.scale is not None:
        msg["scale"] = req.scale
    if req.padding is not None:
        msg["padding"] = req.padding
    await hub.broadcast(msg)
    return {"ok": True}


@app.post("/api/tab")
async def post_tab(req: TabRequest) -> dict:
    await hub.broadcast({"type": "tab", "tab": req.tab})
    return {"ok": True, "tab": req.tab}


@app.post("/api/autofollow")
async def post_autofollow(req: AutofollowRequest) -> dict:
    await hub.broadcast({"type": "autofollow", "value": req.value, "source": "server"})
    return {"ok": True, "value": req.value}


@app.post("/api/clear")
async def post_clear() -> dict:
    db.clear_entries()
    await hub.broadcast({"type": "cleared"})
    return {"ok": True}


@app.delete("/api/entry/{entry_id}")
async def delete_entry(entry_id: int) -> dict:
    if not db.delete_entry(entry_id):
        raise HTTPException(404, "entry not found")
    await hub.broadcast({"type": "entry_deleted", "id": entry_id})
    return {"ok": True}


# ---------------------------------------------------------------------------
# Viewer annotations: comments on note paragraphs and on diagram (uml/graph)
# elements; the agent reads them back.
# ---------------------------------------------------------------------------


# Registered under both /api/note/... (original, notes only) and the generic
# /api/annotation... paths now that diagrams take comments too.
ANNOTATABLE_KINDS = {"note", "uml", "graph"}


@app.post("/api/annotation")
@app.post("/api/note/annotation")
async def post_note_annotation(req: NoteAnnotationRequest) -> dict:
    entry = db.get_entry(req.entry_id)
    if entry is None:
        raise HTTPException(404, f"entry {req.entry_id} not found")
    if entry["kind"] not in ANNOTATABLE_KINDS:
        raise HTTPException(400, f"entry {req.entry_id} can't be annotated (kind={entry['kind']})")
    if entry["kind"] == "note":
        if req.block_index is None:
            raise HTTPException(400, "note annotations need block_index")
        block_index = req.block_index
    else:
        if not req.anchor:
            raise HTTPException(400, f"{entry['kind']} annotations need an anchor")
        block_index = -1  # column is NOT NULL; unused for diagrams

    annot = db.add_note_annotation(
        entry_id=req.entry_id,
        block_index=block_index,
        comment=req.comment,
        block_preview=req.block_preview,
        kind=req.kind,
        line_index=req.line_index,
        anchor=req.anchor,
    )
    annot["messages"] = []
    if req.ask and RESPONDER_CMD:
        annot["messages"].append(_ask(annot, req.comment))
    await hub.broadcast({"type": "note_annotation_added", "annotation": annot})
    return annot


@app.get("/api/annotation/{annot_id}")
async def get_annotation(annot_id: int) -> dict:
    annot = db.get_note_annotation(annot_id)
    if annot is None:
        raise HTTPException(404, "annotation not found")
    entry = db.get_entry(annot["entry_id"])
    annot["messages"] = db.list_annotation_messages(annot_id)
    annot["entry"] = {"id": annot["entry_id"], "kind": entry["kind"] if entry else None,
                      "title": entry["title"] if entry else None}
    return annot


@app.post("/api/annotation/{annot_id}/message")
async def post_annotation_message(annot_id: int, req: AnnotationMessageRequest) -> dict:
    annot = db.get_note_annotation(annot_id)
    if annot is None:
        raise HTTPException(404, "annotation not found")
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "empty message")
    msgs = [db.add_annotation_message(annot_id, "user", text)]
    if req.ask and RESPONDER_CMD:
        msgs.append(_ask(annot, text))
    await _broadcast_thread(annot)
    return {"messages": msgs}


@app.get("/api/config")
async def config() -> dict:
    return {"responder": bool(RESPONDER_CMD), "responder_name": responder.display_name()}


# ---------------------------------------------------------------------------
# Responder: answers comments the viewer marked "ask" (see responder.py).
# ---------------------------------------------------------------------------

RESPONDER_CMD = responder.command_from_env()
RESPONDER_TIMEOUT = responder.timeout_from_env()
_responder_slots = asyncio.Semaphore(2)
_responder_tasks: set[asyncio.Task] = set()


def _ask(annot: dict, question: str) -> dict:
    """Queue an answer: a pending agent message now, filled in when the
    responder returns."""
    msg = db.add_annotation_message(annot["id"], "agent", status="pending")
    task = asyncio.create_task(_answer(annot, msg["id"], question))
    _responder_tasks.add(task)  # keep a reference so it isn't collected mid-run
    task.add_done_callback(_responder_tasks.discard)
    return msg


async def _answer(annot: dict, msg_id: int, question: str) -> None:
    entry = db.get_entry(annot["entry_id"]) or {"id": annot["entry_id"], "kind": None, "payload": {}}
    history = [m for m in db.list_annotation_messages(annot["id"]) if m["id"] != msg_id]
    resume = next((m["meta"].get("session_id") for m in reversed(history)
                   if m["author"] == "agent" and m["status"] == "done" and m["meta"].get("session_id")), None)
    # The question itself is the newest user message; don't repeat it as history.
    thread = [{"author": m["author"], "text": m["text"]} for m in history
              if m["status"] == "done" and not (m["author"] == "user" and m["text"] == question)]
    job = {
        "entry": {k: entry.get(k) for k in ("id", "kind", "title", "payload")},
        "annotation": {k: annot.get(k) for k in
                       ("id", "comment", "block_preview", "block_index", "line_index", "anchor")},
        "thread": thread,
        "question": question,
        "origin": db.get_entry_origin(annot["entry_id"]),
        "resume_session": resume,
    }
    async with _responder_slots:
        try:
            result = await responder.run(RESPONDER_CMD, job, RESPONDER_TIMEOUT)
            meta = {k: result[k] for k in ("session_id", "cost_usd", "mode") if result.get(k) is not None}
            db.update_annotation_message(msg_id, text=str(result["text"]).strip(), status="done", meta=meta)
        except responder.ResponderError as e:
            db.update_annotation_message(msg_id, text=str(e), status="error")
        except Exception as e:  # never leave it pending
            db.update_annotation_message(msg_id, text=f"{type(e).__name__}: {e}", status="error")
    await _broadcast_thread(annot, answered=True)


async def _broadcast_thread(annot: dict, answered: bool = False) -> None:
    entry = db.get_entry(annot["entry_id"])
    await hub.broadcast({
        "type": "annotation_thread",
        "annotation_id": annot["id"],
        "entry_id": annot["entry_id"],
        "entry_title": entry["title"] if entry else None,
        "answered": answered,
    })


@app.get("/api/annotations/{entry_id}")
@app.get("/api/note/annotations/{entry_id}")
async def get_note_annotations(entry_id: int) -> dict:
    return {"annotations": db.list_note_annotations(entry_id)}


@app.delete("/api/annotation/{annot_id}")
@app.delete("/api/note/annotation/{annot_id}")
async def delete_note_annotation(annot_id: int) -> dict:
    if not db.delete_note_annotation(annot_id):
        raise HTTPException(404, "annotation not found")
    await hub.broadcast({"type": "note_annotation_deleted", "id": annot_id})
    return {"ok": True}


@app.websocket("/ws")
async def ws(ws: WebSocket) -> None:
    if not _host_allowed(ws.headers.get("host")) or not _origin_allowed(ws.headers.get("origin")):
        await ws.close(code=1008)  # policy violation
        return
    if not _authorized(ws.headers, ws.cookies):
        # Accept first: a close before accept surfaces in the browser as a
        # generic failure, and app.js needs 4401 to know to go pair.
        await ws.accept()
        await ws.close(code=4401)
        return
    await ws.accept()
    await hub.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.remove(ws)
