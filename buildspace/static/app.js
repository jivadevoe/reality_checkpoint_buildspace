// Copyright 2026 Reality Checkpoint
// SPDX-License-Identifier: Apache-2.0
(() => {
  const state = {
    entries: [],
    currentId: null,
    autofollow: true,
    activeTab: "code",
  };

  const graphState = {
    network: null,
    currentId: null,
    nodes: null,  // vis.DataSet
    edges: null,  // vis.DataSet
    edgeKey: new Map(),  // "source|target" -> vis edge id, for removal by endpoints
    mermaidPanzoom: null,  // panzoom instance wrapping the current mermaid svg
    mermaidSvg: null,  // the current rendered svg (target of panzoom)
    diagramAnnotations: [],  // current annotations to reposition on every frame
    annotLayer: null,
    shownEntry: null,  // {id, kind} of the uml/graph entry in the Diagrams tab
    agentAnnots: [],  // annotations pushed with the entry
    userAnnots: [],  // the viewer's own comments, fetched from /api/annotations
    composer: null,  // open diagram comment composer, if any
  };

  const el = {
    timeline: document.getElementById("timeline"),
    graphEmpty: document.getElementById("graph-empty"),
    graphView: document.getElementById("graph-view"),
    graphTitle: document.getElementById("graph-title"),
    graphMeta: document.getElementById("graph-meta"),
    graphCanvas: document.getElementById("graph-canvas"),
    noteEmpty: document.getElementById("note-empty"),
    noteView: document.getElementById("note-view"),
    noteTitle: document.getElementById("note-title"),
    noteMeta: document.getElementById("note-meta"),
    noteBody: document.getElementById("note-body"),
    codeEmpty: document.getElementById("code-empty"),
    codeView: document.getElementById("code-view"),
    codeTitle: document.getElementById("code-title"),
    codeMeta: document.getElementById("code-meta"),
    codeBody: document.getElementById("code-body"),
    diffEmpty: document.getElementById("diff-empty"),
    diffView: document.getElementById("diff-view"),
    diffTitle: document.getElementById("diff-title"),
    diffMeta: document.getElementById("diff-meta"),
    diffBody: document.getElementById("diff-body"),
    videoEmpty: document.getElementById("video-empty"),
    videoView: document.getElementById("video-view"),
    videoTitle: document.getElementById("video-title"),
    videoMeta: document.getElementById("video-meta"),
    videoPlayer: document.getElementById("video-player"),
    videoNotes: document.getElementById("video-notes"),
    videoNotesToggle: document.getElementById("video-notes-toggle"),
    autofollow: document.getElementById("autofollow"),
    followLabel: document.querySelector(".follow"),
    tabs: document.querySelectorAll(".tab"),
    panes: {
      code: document.getElementById("pane-code"),
      diff: document.getElementById("pane-diff"),
      graph: document.getElementById("pane-graph"),
      note: document.getElementById("pane-note"),
      video: document.getElementById("pane-video"),
    },
    sidebarToggle: document.getElementById("sidebar-toggle"),
    layout: document.getElementById("app"),
    clearBtn: document.getElementById("clear-btn"),
    toast: document.getElementById("toast"),
    connDot: document.getElementById("conn-dot"),
  };

  // ---------- toast ----------
  let toastTimer = null;
  function toast(msg) {
    el.toast.textContent = msg;
    el.toast.hidden = false;
    requestAnimationFrame(() => el.toast.classList.add("visible"));
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      el.toast.classList.remove("visible");
      setTimeout(() => (el.toast.hidden = true), 300);
    }, 2000);
  }

  // ---------- timeline ----------
  function renderTimeline() {
    el.timeline.innerHTML = "";
    for (const entry of state.entries) {
      const li = document.createElement("li");
      li.dataset.id = entry.id;
      if (entry.id === state.currentId) li.classList.add("active");
      const title = document.createElement("div");
      title.className = "row-title";
      title.textContent = entry.title || entry.payload?.filename || `entry ${entry.id}`;
      const meta = document.createElement("div");
      meta.className = "row-meta";
      const kind = document.createElement("span");
      kind.className = "kind-tag";
      kind.textContent = entry.kind;
      const when = document.createElement("span");
      when.textContent = relTime(entry.created_at);
      meta.appendChild(kind);
      meta.appendChild(when);
      li.appendChild(title);
      li.appendChild(meta);

      const del = document.createElement("button");
      del.className = "row-delete";
      del.setAttribute("aria-label", "delete entry");
      del.title = "delete this entry";
      del.innerHTML = "&times;";
      del.addEventListener("click", (ev) => {
        ev.stopPropagation();
        deleteEntry(entry.id);
      });
      li.appendChild(del);

      li.addEventListener("click", () => userNavigateTo(entry.id));
      el.timeline.appendChild(li);
    }
  }

  async function deleteEntry(id) {
    await fetch(`/api/entry/${id}`, { method: "DELETE" });
  }

  function relTime(ts) {
    const d = Date.now() / 1000 - ts;
    if (d < 60) return "just now";
    if (d < 3600) return `${Math.floor(d / 60)}m ago`;
    if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
    return `${Math.floor(d / 86400)}d ago`;
  }

  // ---------- code rendering ----------
  function splitHighlighted(html) {
    // hljs.highlight output: highlighted HTML with embedded <span> tags.
    // Splitting naively by "\n" is safe because hljs does not cross <span>
    // boundaries with newlines inside token values except inside string
    // literals, where its highlighter keeps close/reopen balanced.
    const lines = html.split("\n");
    // Balance any spans that cross lines, so each line parses as standalone.
    const balanced = [];
    let open = [];
    const spanRe = /<span class="([^"]*)">|<\/span>/g;
    for (const line of lines) {
      let prefix = open.map((c) => `<span class="${c}">`).join("");
      let content = line;
      let m;
      spanRe.lastIndex = 0;
      while ((m = spanRe.exec(content)) !== null) {
        if (m[0] === "</span>") {
          open.pop();
        } else {
          open.push(m[1]);
        }
      }
      let suffix = open.map(() => "</span>").join("");
      balanced.push(prefix + content + suffix);
    }
    return balanced;
  }

  function renderCode(entry) {
    el.codeEmpty.hidden = true;
    el.codeView.hidden = false;
    const p = entry.payload || {};
    el.codeTitle.textContent = p.filename || entry.title || "code";
    const bits = [];
    if (p.language) bits.push(p.language);
    if (p.line_count) bits.push(`${p.line_count} lines`);
    if (p.path) bits.push(p.path);
    el.codeMeta.textContent = bits.join("  ·  ");

    const lang = p.language && hljs.getLanguage(p.language) ? p.language : null;
    const highlighted = lang
      ? hljs.highlight(p.content || "", { language: lang, ignoreIllegals: true }).value
      : escapeHtml(p.content || "");
    const lines = splitHighlighted(highlighted);

    const hl = new Set(p.highlights || []);
    const annotations = (p.annotations || []).slice().sort((a, b) => a.line - b.line);
    const annotLines = new Set(annotations.map((a) => a.line));

    el.codeBody.classList.toggle("has-annots", annotations.length > 0);

    const frag = document.createDocumentFragment();
    lines.forEach((lineHtml, idx) => {
      const lineNum = idx + 1;
      const row = document.createElement("div");
      row.className = "code-row";
      if (hl.has(lineNum)) row.classList.add("highlight");
      if (annotLines.has(lineNum)) row.classList.add("anchor");
      row.dataset.line = lineNum;

      const ln = document.createElement("div");
      ln.className = "ln";
      ln.textContent = lineNum;

      const code = document.createElement("div");
      code.className = "code hljs";
      code.innerHTML = lineHtml || " ";

      row.appendChild(ln);
      row.appendChild(code);
      frag.appendChild(row);
    });
    el.codeBody.innerHTML = "";
    el.codeBody.appendChild(frag);

    placeAnnotations(el.codeBody, annotations);

    const firstHl = Math.min(...(p.highlights || []));
    if (Number.isFinite(firstHl)) {
      const target = el.codeBody.querySelector(`.code-row[data-line="${firstHl}"]`);
      if (target) target.scrollIntoView({ block: "center", behavior: "smooth" });
    } else {
      el.codeView.scrollTop = 0;
    }
  }

  // ---------- diff rendering ----------
  function renderDiff(entry) {
    el.diffEmpty.hidden = true;
    el.diffView.hidden = false;
    const p = entry.payload || {};
    el.diffTitle.textContent = p.filename || entry.title || "diff";
    const bits = [];
    if (p.language) bits.push(p.language);
    if (p.path) bits.push(p.path);
    el.diffMeta.innerHTML = bits.join("  ·  ");
    if (p.stats) {
      const statAdd = document.createElement("span");
      statAdd.className = "stat-add";
      statAdd.textContent = `+${p.stats.add}`;
      const statRem = document.createElement("span");
      statRem.className = "stat-rem";
      statRem.textContent = `−${p.stats.remove}`;
      el.diffMeta.appendChild(statAdd);
      el.diffMeta.appendChild(statRem);
    }

    const lang = p.language && hljs.getLanguage(p.language) ? p.language : null;
    const hl = new Set(p.highlights || []);
    const annotations = (p.annotations || []).slice().sort((a, b) => a.line - b.line);
    const annotLines = new Set(annotations.map((a) => a.line));

    el.diffBody.classList.toggle("has-annots", annotations.length > 0);

    const frag = document.createDocumentFragment();
    for (const r of p.rows || []) {
      const row = document.createElement("div");
      row.className = "diff-row " + r.type;
      const anchorLine = r.after ?? r.before;
      if (anchorLine != null) row.dataset.line = anchorLine;
      if (r.type === "add" && r.after != null && hl.has(r.after)) row.classList.add("highlight");
      if (r.type === "add" && r.after != null && annotLines.has(r.after)) row.classList.add("anchor");

      const lnBefore = document.createElement("div");
      lnBefore.className = "ln a";
      lnBefore.textContent = r.before ?? "";
      const lnAfter = document.createElement("div");
      lnAfter.className = "ln b";
      lnAfter.textContent = r.after ?? "";

      const code = document.createElement("div");
      code.className = "code hljs";
      const sign = { add: "+", remove: "−", context: " " }[r.type];
      const highlighted = lang
        ? hljs.highlight(r.content || "", { language: lang, ignoreIllegals: true }).value
        : escapeHtml(r.content || "");
      code.innerHTML = `<span class="sign">${sign}</span>${highlighted || " "}`;

      row.appendChild(lnBefore);
      row.appendChild(lnAfter);
      row.appendChild(code);
      frag.appendChild(row);
    }
    el.diffBody.innerHTML = "";
    el.diffBody.appendChild(frag);

    placeAnnotations(el.diffBody, annotations, ".diff-row");

    const firstHl = Math.min(...(p.highlights || []));
    if (Number.isFinite(firstHl)) {
      const target = el.diffBody.querySelector(`.diff-row[data-line="${firstHl}"]`);
      if (target) target.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }

  // ---------- note rendering ----------
  function renderVideo(entry, seekTime) {
    el.videoEmpty.hidden = true;
    el.videoView.hidden = false;
    const p = entry.payload || {};
    el.videoTitle.textContent = entry.title || p.filename || "video";
    const metaBits = [];
    if (p.duration) metaBits.push(formatDuration(p.duration));
    if (p.width && p.height) metaBits.push(`${p.width}×${p.height}`);
    if (p.video_codec) metaBits.push(p.video_codec.toUpperCase());
    if (p.size_bytes) metaBits.push(formatBytes(p.size_bytes));
    el.videoMeta.textContent = metaBits.join(" · ");

    // Pause + reset before swapping the source so we don't fight the old
    // playback while the new media loads.
    try { el.videoPlayer.pause(); } catch {}
    el.videoPlayer.removeAttribute("src");
    el.videoPlayer.load();
    el.videoPlayer.src = `/media/${entry.id}`;
    if (p.poster) {
      el.videoPlayer.poster = `/media/${entry.id}/poster`;
    } else {
      el.videoPlayer.removeAttribute("poster");
    }

    // Seek to specific time if requested (from timeline @t= links)
    if (seekTime != null && Number.isFinite(seekTime)) {
      const doSeek = () => { el.videoPlayer.currentTime = seekTime; };
      if (el.videoPlayer.readyState >= 1) {
        doSeek();
      } else {
        el.videoPlayer.addEventListener("loadedmetadata", doSeek, { once: true });
      }
    }

    // Notes are hidden by default — the user opens them with the toggle in
    // the header. Player gets the full pane unless asked otherwise.
    if (p.notes) {
      if (canRenderMarkdown()) {
        el.videoNotes.innerHTML = renderMarkdown(p.notes);
      } else {
        el.videoNotes.textContent = p.notes;
      }
      el.videoNotesToggle.hidden = false;
    } else {
      el.videoNotes.innerHTML = "";
      el.videoNotesToggle.hidden = true;
    }
    // Per-entry: collapse on every new entry render so a new push doesn't
    // surprise you with an open panel. User can toggle if they want it.
    el.videoNotes.hidden = true;
    el.videoNotesToggle.setAttribute("aria-expanded", "false");
  }

  if (el.videoNotesToggle) {
    el.videoNotesToggle.addEventListener("click", () => {
      const open = el.videoNotes.hidden;
      el.videoNotes.hidden = !open;
      el.videoNotesToggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  function formatDuration(seconds) {
    if (!Number.isFinite(seconds)) return "";
    const total = Math.round(seconds);
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    return h > 0
      ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
      : `${m}:${String(s).padStart(2, "0")}`;
  }

  function formatBytes(bytes) {
    if (!bytes) return "";
    const units = ["B", "KB", "MB", "GB"];
    let n = bytes;
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(n >= 100 ? 0 : 1)} ${units[i]}`;
  }

  function renderNote(entry) {
    el.noteEmpty.hidden = true;
    el.noteView.hidden = false;
    const p = entry.payload || {};
    el.noteTitle.textContent = entry.title || "note";
    el.noteMeta.textContent = p.length ? `${p.length} chars` : "";
    el.noteBody.dataset.entryId = String(entry.id);

    if (canRenderMarkdown()) {
      marked.setOptions({
        gfm: true,
        breaks: false,
        highlight: (code, lang) => {
          if (lang && hljs.getLanguage(lang)) {
            try {
              return hljs.highlight(code, { language: lang, ignoreIllegals: true }).value;
            } catch {}
          }
          return hljs.highlightAuto(code).value;
        },
      });
      el.noteBody.innerHTML = renderMarkdown(p.markdown || "");
    } else {
      el.noteBody.textContent = p.markdown || "";
    }
    // Apply hljs class to code blocks for theme compatibility.
    el.noteBody.querySelectorAll("pre code").forEach((block) => {
      if (!block.classList.contains("hljs")) block.classList.add("hljs");
    });
    decorateNoteLinks();
    decorateNoteBlocks(entry.id);
    loadNoteAnnotations(entry.id);
    el.noteView.scrollTop = 0;
  }

  // Tag each top-level block in the rendered note so the reader can tap it to
  // attach a comment. Block index is stable as long as the source markdown
  // doesn't change (notes are immutable entries, so this holds).
  function decorateNoteBlocks(entryId) {
    const blocks = el.noteBody.children;
    for (let i = 0; i < blocks.length; i++) {
      const b = blocks[i];
      b.classList.add("note-block", "annotatable");
      b.dataset.blockIndex = String(i);

      // Sub-block "lines" — tables and lists have semantic child rows/items
      // that deserve their own anchor. Generated timeline notes render each
      // cut / title as a <tr>, and he wants to comment on a specific row.
      let lineEls = [];
      if (b.tagName === "TABLE") {
        // Use tbody rows; skip header rows in thead. If no tbody, all <tr>.
        const tbody = b.querySelector(":scope > tbody");
        lineEls = Array.from((tbody || b).querySelectorAll(":scope > tr"));
      } else if (b.tagName === "UL" || b.tagName === "OL") {
        lineEls = Array.from(b.querySelectorAll(":scope > li"));
      }

      if (lineEls.length > 0) {
        lineEls.forEach((line, li) => {
          line.classList.add("note-line", "annotatable-line");
          line.dataset.blockIndex = String(i);
          line.dataset.lineIndex = String(li);
          line.addEventListener("click", (ev) => {
            const sel = window.getSelection();
            if (sel && sel.toString().trim()) return;
            if (ev.target.closest("a")) return;
            if (ev.target.closest(".annot-bubble")) return;
            if (el.noteBody.querySelector(".note-composer")) return;
            ev.stopPropagation();
            openNoteComposer(line, entryId, i, li);
          });
        });
      }

      // Block-level click still opens a whole-block annotation, but only
      // when the click isn't on one of the sub-block lines above.
      b.addEventListener("click", (ev) => {
        const sel = window.getSelection();
        if (sel && sel.toString().trim()) return;
        if (ev.target.closest("a")) return;
        if (ev.target.closest(".annot-bubble")) return;
        if (ev.target.closest(".annotatable-line")) return;
        if (el.noteBody.querySelector(".note-composer")) return;
        openNoteComposer(b, entryId, i);
      });
    }
  }

  async function loadNoteAnnotations(entryId) {
    clearNoteAnnotations();
    try {
      const r = await fetch(`/api/note/annotations/${entryId}`);
      if (!r.ok) return;
      const data = await r.json();
      placeNoteAnnotations(data.annotations || []);
    } catch {}
  }

  function clearNoteAnnotations() {
    el.noteBody.querySelectorAll(":scope > .annot-bubble").forEach((b) => b.remove());
    const svg = el.noteBody.querySelector(":scope > svg.connector-layer");
    if (svg) svg.remove();
    el.noteBody.classList.remove("has-annots");
  }

  // For a given annotation, return the most specific anchor element —
  // the line (tr/li) if the annotation was made at line level, else the
  // whole block. Falls back to null if neither can be found.
  function annotationAnchor(a) {
    if (a.line_index != null) {
      const line = el.noteBody.querySelector(
        `.annotatable-line[data-block-index="${a.block_index}"][data-line-index="${a.line_index}"]`,
      );
      if (line) return line;
    }
    return el.noteBody.querySelector(
      `.note-block[data-block-index="${a.block_index}"]`,
    );
  }

  function placeNoteAnnotations(annotations) {
    if (!annotations || !annotations.length) return;
    el.noteBody.classList.add("has-annots");

    const placed = annotations.map((a) => {
      const anchor = annotationAnchor(a);
      const block = el.noteBody.querySelector(
        `.note-block[data-block-index="${a.block_index}"]`,
      );
      const b = document.createElement("div");
      b.className = "annot-bubble kind-" + (a.kind || "note") + " user-annot";
      if (a.line_index != null) b.classList.add("line-level");
      b.dataset.annotId = String(a.id);
      const dot = document.createElement("span");
      dot.className = "annot-dot";
      b.appendChild(dot);
      b.appendChild(document.createTextNode(a.comment));
      const del = document.createElement("button");
      del.type = "button";
      del.className = "annot-del";
      del.title = "delete annotation";
      del.textContent = "×";
      del.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        try {
          await fetch(`/api/note/annotation/${a.id}`, { method: "DELETE" });
        } catch {}
      });
      b.appendChild(del);
      // Bubbles always live at the top level of noteBody (insertBefore
      // into a tbody would break table rendering). Anchor rect comes from
      // the line element; layout hops over the table.
      if (block && block.nextSibling) {
        el.noteBody.insertBefore(b, block.nextSibling);
      } else {
        el.noteBody.appendChild(b);
      }
      return { a, b, anchor };
    });

    requestAnimationFrame(() => {
      const bodyRect = el.noteBody.getBoundingClientRect();
      const placements = [];
      for (const { a, b, anchor } of placed) {
        if (!anchor) continue;
        const rect = anchor.getBoundingClientRect();
        const targetCenter = rect.top + rect.height / 2 - bodyRect.top;
        const h = b.offsetHeight || 40;
        placements.push({ b, h, halfH: h / 2, desired: targetCenter });
      }
      placements.sort((x, y) => x.desired - y.desired);
      const gap = 10;
      let prevBottom = -Infinity;
      for (const p of placements) {
        let top = p.desired - p.halfH;
        if (top < prevBottom + gap) top = prevBottom + gap;
        prevBottom = top + p.h;
        p.b.style.top = (top + p.halfH) + "px";
      }
    });
  }

  // Placement strategy for the composer:
  //   - Wide viewports: float it in the right-rail column (same vertical
  //     band that holds the saved annotation bubbles), vertically aligned
  //     with the clicked target. Uses position:absolute inside .note-body
  //     so scrolling the note moves the composer naturally with its target.
  //   - Narrow viewports: fall back to inline placement after the target's
  //     containing block (fits on a phone where the right-rail column
  //     doesn't exist).
  // The target element (the <tr>, <li>, or block) gets .note-composer-target
  // while the composer is open, giving the user a clear "this is what I'm
  // commenting on" box outline.
  function openNoteComposer(block, entryId, blockIndex, lineIndex = null) {
    const host = document.createElement("div");
    host.className = "note-composer";
    if (lineIndex != null) host.classList.add("line-level");

    const ta = document.createElement("textarea");
    ta.placeholder = "annotation…";
    ta.rows = 2;

    const actions = document.createElement("div");
    actions.className = "note-composer-actions";

    const save = document.createElement("button");
    save.type = "button";
    save.className = "btn-primary";
    save.textContent = "save";

    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "btn-secondary";
    cancel.textContent = "cancel";

    actions.appendChild(cancel);
    actions.appendChild(save);
    host.appendChild(ta);
    host.appendChild(actions);

    if (lineIndex != null) {
      const hint = document.createElement("div");
      hint.className = "note-composer-hint";
      const preview = (block.textContent || "").trim().slice(0, 80);
      hint.textContent = `re: ${preview}`;
      host.insertBefore(hint, ta);
    }

    // Highlight the target so the user sees exactly what they're annotating.
    block.classList.add("note-composer-target");

    // Wide-viewport floating placement: attach to .note-body and position
    // absolute, aligned with the target element. Narrow viewport: inline.
    const WIDE_VIEWPORT_MIN = 900;  // px — below this, right-rail float would overlap text
    const useFloating = window.innerWidth >= WIDE_VIEWPORT_MIN;

    if (useFloating) {
      host.classList.add("floating");
      // Position relative to .note-body so the composer scrolls with it.
      el.noteBody.appendChild(host);
      positionFloatingComposer(host, block);
    } else {
      // Fall back to inline placement. For line-level, insert directly
      // after the containing top-block; for block-level, after the block
      // itself. Keeps the composer near the click target on narrow screens
      // without overlapping text.
      const topBlock = block.closest(".note-block") || block;
      if (topBlock.nextSibling) {
        el.noteBody.insertBefore(host, topBlock.nextSibling);
      } else {
        el.noteBody.appendChild(host);
      }
    }

    requestAnimationFrame(() => ta.focus());

    // If the user scrolls or resizes while floating, keep the composer
    // glued to its target. Re-measuring each event is cheap since there's
    // only ever one composer open.
    let reposition = null;
    if (useFloating) {
      reposition = () => positionFloatingComposer(host, block);
      el.noteView.addEventListener("scroll", reposition, { passive: true });
      window.addEventListener("resize", reposition);
    }

    const close = () => {
      block.classList.remove("note-composer-target");
      if (reposition) {
        el.noteView.removeEventListener("scroll", reposition);
        window.removeEventListener("resize", reposition);
      }
      host.remove();
    };

    cancel.addEventListener("click", close);
    ta.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") { ev.preventDefault(); close(); }
      if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) {
        ev.preventDefault();
        save.click();
      }
    });
    save.addEventListener("click", async () => {
      const comment = ta.value.trim();
      if (!comment) { close(); return; }
      save.disabled = true;
      const preview = (block.textContent || "").trim().slice(0, 80);
      const body = {
        entry_id: entryId,
        block_index: blockIndex,
        comment,
        block_preview: preview,
      };
      if (lineIndex != null) body.line_index = lineIndex;
      try {
        await fetch("/api/note/annotation", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      } catch (err) {
        toast("couldn't save annotation");
      }
      close();
    });
  }

  // Align a floating composer with its target element. Target top/bottom
  // measured relative to .note-body (the composer's offset parent); the
  // composer centers vertically on the target, clamped to stay inside
  // .note-body vertical bounds.
  function positionFloatingComposer(host, target) {
    const body = el.noteBody;
    const bodyRect = body.getBoundingClientRect();
    const tRect = target.getBoundingClientRect();
    // Top of the target within the scrolled noteBody coords.
    const targetTopInBody = (tRect.top - bodyRect.top) + body.scrollTop;
    const targetH = tRect.height;
    const targetCenter = targetTopInBody + targetH / 2;

    // Host is attached; we need its own height to center it on the target.
    const hostH = host.offsetHeight || 120;  // rough guess before first layout
    let top = targetCenter - hostH / 2;
    // Clamp to visible area of the note body so the composer never slides
    // off the top/bottom edge.
    const minTop = body.scrollTop + 8;
    const maxTop = body.scrollTop + body.clientHeight - hostH - 8;
    if (top < minTop) top = minTop;
    if (top > maxTop) top = maxTop;
    host.style.top = top + "px";
    // Record target offset on the composer so the CSS pointer pseudo-
    // element can be positioned. The pointer's y within the composer =
    // (targetCenter - top).
    host.style.setProperty("--composer-pointer-y",
      (targetCenter - top) + "px");
  }

  // Links inside a markdown note can target other timeline entries so a
  // prose walkthrough can point at its supporting diagrams and code.
  // Supported href schemes (only for <a> elements inside the note body):
  //   #entry-42            → jump to numeric entry id 42
  //   #latest:code         → most recent entry of kind "code"
  //   #latest:diff         → most recent diff
  //   #latest:graph        → most recent force graph
  //   #latest:uml          → most recent UML entry
  //   #latest:note         → most recent note (excluding the current one)
  //   #title:foo bar       → first entry whose title contains "foo bar"
  function decorateNoteLinks() {
    const links = el.noteBody.querySelectorAll('a[href^="#entry-"], a[href^="#latest:"], a[href^="#title:"]');
    links.forEach((a) => {
      a.classList.add("entry-link");
      a.dataset.linkTarget = a.getAttribute("href");
      a.addEventListener("click", (ev) => {
        ev.preventDefault();
        const href = a.dataset.linkTarget;
        const timePart = href.match(/@t=([\d.]+)/);
        const seekTime = timePart ? parseFloat(timePart[1]) : null;
        const cleanHref = href.replace(/@t=[\d.]+/, "");
        const target = resolveEntryLink(cleanHref);
        if (target) userNavigateTo(target.id, { seekTime });
      });
    });
  }

  function resolveEntryLink(href) {
    if (!href) return null;
    let decoded;
    try { decoded = decodeURIComponent(href); } catch { decoded = href; }
    if (decoded.startsWith("#entry-")) {
      const id = parseInt(decoded.slice("#entry-".length), 10);
      return state.entries.find((e) => e.id === id) || null;
    }
    if (decoded.startsWith("#latest:")) {
      const kinds = decoded.slice("#latest:".length).split(",").map((s) => s.trim());
      const currentId = state.currentId;
      return state.entries.find(
        (e) => kinds.includes(e.kind) && e.id !== currentId,
      ) || null;
    }
    if (decoded.startsWith("#title:")) {
      const needle = decoded.slice("#title:".length).trim().toLowerCase();
      return state.entries.find((e) => (e.title || "").toLowerCase().includes(needle)) || null;
    }
    return null;
  }

  // ---------- uml rendering (Mermaid) ----------
  async function renderUml(entry) {
    el.graphEmpty.hidden = true;
    el.graphView.hidden = false;
    if (graphState.network) {
      graphState.network.destroy();
      graphState.network = null;
      graphState.nodes = null;
      graphState.edges = null;
      graphState.currentId = null;
    }
    disposeMermaidPanzoom();
    beginDiagramEntry(entry);
    const p = entry.payload || {};
    el.graphTitle.textContent = entry.title || "UML";
    el.graphMeta.textContent = p.kind ? `mermaid · ${p.kind}` : "mermaid";
    el.graphCanvas.classList.add("mermaid-host");
    el.graphCanvas.innerHTML = '<div style="color:var(--text-faint);font-size:12px;">rendering…</div>';

    if (typeof window.mermaid === "undefined") {
      el.graphCanvas.innerHTML = '<pre class="mermaid-error">Mermaid not loaded</pre>';
      return;
    }
    try {
      const id = `mermaid-${entry.id}-${Date.now()}`;
      const { svg } = await window.mermaid.render(id, p.mermaid || "");
      el.graphCanvas.innerHTML = svg;
      attachMermaidPanzoom();
      graphState.agentAnnots = p.annotations || [];
      if (graphState.agentAnnots.length) refreshDiagramAnnotations();
      loadDiagramUserAnnotations(entry.id);
    } catch (err) {
      el.graphCanvas.innerHTML = `<pre class="mermaid-error">${escapeHtml(String(err?.message || err))}</pre>`;
    }
  }

  function disposeMermaidPanzoom() {
    if (graphState.mermaidPanzoom) {
      try { graphState.mermaidPanzoom.dispose(); } catch {}
      graphState.mermaidPanzoom = null;
    }
    graphState.mermaidSvg = null;
    graphState.diagramAnnotations = [];
    if (graphState.annotLayer) {
      graphState.annotLayer.remove();
      graphState.annotLayer = null;
    }
    removeAnnotToggle();
    cancelIdleDim();
  }

  function ensureAnnotLayer() {
    if (!graphState.annotLayer || !graphState.annotLayer.isConnected) {
      const layer = document.createElement("div");
      layer.className = "annot-layer";
      el.graphCanvas.appendChild(layer);
      graphState.annotLayer = layer;
    }
    return graphState.annotLayer;
  }

  // ---------- annotation friendliness: idle-dim + toggle (diagrams only) ----
  // The idle timer only runs while the graph canvas has annotations. Pointer
  // activity inside #graph-canvas removes the .annotations-idle class and
  // restarts the countdown.
  const IDLE_DIM_DELAY_MS = 4000;
  let idleDimTimer = null;

  function resetIdleDim() {
    el.graphCanvas.classList.remove("annotations-idle");
    if (idleDimTimer) clearTimeout(idleDimTimer);
    idleDimTimer = null;
    if (!graphState.annotLayer) return;
    idleDimTimer = setTimeout(() => {
      if (graphState.annotLayer) el.graphCanvas.classList.add("annotations-idle");
    }, IDLE_DIM_DELAY_MS);
  }

  function cancelIdleDim() {
    el.graphCanvas.classList.remove("annotations-idle");
    if (idleDimTimer) clearTimeout(idleDimTimer);
    idleDimTimer = null;
  }

  ["pointermove", "pointerdown", "wheel", "touchstart"].forEach((evt) => {
    el.graphCanvas.addEventListener(evt, resetIdleDim, { passive: true });
  });

  function ensureAnnotToggle() {
    let btn = el.graphCanvas.querySelector(":scope > .annot-toggle");
    if (btn) return btn;
    btn = document.createElement("button");
    btn.className = "annot-toggle";
    btn.type = "button";
    btn.title = "hide/show annotations (local to this diagram)";
    const dot = document.createElement("span");
    dot.className = "dot";
    btn.appendChild(dot);
    const label = document.createElement("span");
    label.textContent = "notes";
    btn.appendChild(label);
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const nowHidden = el.graphCanvas.classList.toggle("annotations-hidden");
      if (nowHidden) {
        cancelIdleDim();
      } else {
        resetIdleDim();
      }
    });
    el.graphCanvas.appendChild(btn);
    return btn;
  }

  function removeAnnotToggle() {
    const btn = el.graphCanvas.querySelector(":scope > .annot-toggle");
    if (btn) btn.remove();
    el.graphCanvas.classList.remove("annotations-hidden");
  }

  // ---------- connector lines from annotations to their targets ----------
  const SVG_NS = "http://www.w3.org/2000/svg";

  function ensureConnectorLayer(container) {
    let svg = container.querySelector(":scope > svg.connector-layer");
    if (!svg) {
      svg = document.createElementNS(SVG_NS, "svg");
      svg.setAttribute("class", "connector-layer");
      // Insert first so it sits behind bubbles and code rows (z-index
      // on the bubbles pulls them forward).
      container.insertBefore(svg, container.firstChild);
    }
    return svg;
  }

  function clearConnectors(svg) {
    while (svg.firstChild) svg.removeChild(svg.firstChild);
  }

  // Extract "note" | "warn" | "ok" from a bubble's classList.
  function bubbleKind(bubble) {
    for (const c of bubble.classList) {
      if (c.startsWith("kind-")) return c.slice("kind-".length);
    }
    return "note";
  }

  // Draw a single bezier connector from (x1,y1) to (x2,y2) with a small
  // target dot. All coordinates are in the container's local space.
  // `bow` is how much the curve bows perpendicular to the straight line.
  function drawConnector(svg, x1, y1, x2, y2, kind, bow = 18) {
    const dx = x2 - x1;
    const dy = y2 - y1;
    const len = Math.hypot(dx, dy) || 1;
    // Perpendicular offset for the control point
    const px = -dy / len;
    const py = dx / len;
    const midX = (x1 + x2) / 2 + px * bow;
    const midY = (y1 + y2) / 2 + py * bow;

    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("class", "kind-" + kind);
    path.setAttribute("d", `M ${x1.toFixed(1)} ${y1.toFixed(1)} Q ${midX.toFixed(1)} ${midY.toFixed(1)} ${x2.toFixed(1)} ${y2.toFixed(1)}`);
    svg.appendChild(path);

    const dot = document.createElementNS(SVG_NS, "circle");
    dot.setAttribute("class", "kind-" + kind);
    dot.setAttribute("cx", x2.toFixed(1));
    dot.setAttribute("cy", y2.toFixed(1));
    dot.setAttribute("r", "2.8");
    svg.appendChild(dot);
  }

  // For a bubble, pick the point on its perimeter facing its target.
  // Returns {x, y} in container-local coordinates.
  function bubbleAnchorPoint(bubble, containerRect, variant) {
    const r = bubble.getBoundingClientRect();
    const cx = r.left + r.width / 2 - containerRect.left;
    const cy = r.top + r.height / 2 - containerRect.top;
    const left = r.left - containerRect.left;
    const right = r.right - containerRect.left;
    const top = r.top - containerRect.top;
    const bottom = r.bottom - containerRect.top;
    switch (variant) {
      case "above": return { x: cx, y: bottom };
      case "below": return { x: cx, y: top };
      case "side-left": return { x: right, y: cy };
      case "side-right":
      case "code":
      default:
        return { x: left, y: cy };
    }
  }

  // For a target element, pick the point on its perimeter facing the bubble.
  function targetAnchorPoint(targetRect, containerRect, variant) {
    const cx = targetRect.left + targetRect.width / 2 - containerRect.left;
    const cy = targetRect.top + targetRect.height / 2 - containerRect.top;
    const left = targetRect.left - containerRect.left;
    const right = targetRect.right - containerRect.left;
    const top = targetRect.top - containerRect.top;
    const bottom = targetRect.bottom - containerRect.top;
    switch (variant) {
      case "above": return { x: cx, y: top };
      case "below": return { x: cx, y: bottom };
      case "side-left": return { x: left, y: cy };
      case "side-right": return { x: right, y: cy };
      case "code": return { x: right, y: cy };  // aim at right edge of the row
      default:       return { x: cx, y: cy };
    }
  }

  function variantFromBubble(bubble) {
    if (bubble.classList.contains("above")) return "above";
    if (bubble.classList.contains("below")) return "below";
    if (bubble.classList.contains("side-left")) return "side-left";
    if (bubble.classList.contains("side-right")) return "side-right";
    return "code";  // code/diff margin bubbles (no variant class)
  }

  // Code/diff container: bubble is to the right of a .code-row (or
  // .diff-row). Draw a gentle arc from the bubble's left edge back to
  // the right edge of the *rendered text* on that row, so the
  // connector visibly terminates where the line's content ends.
  function refreshCodeConnectors(container) {
    if (!container) return;
    const svg = ensureConnectorLayer(container);
    clearConnectors(svg);
    const cRect = container.getBoundingClientRect();
    const bubbles = container.querySelectorAll(":scope > .annot-bubble");
    bubbles.forEach((bubble) => {
      const row = bubble.previousElementSibling;
      if (!row || (!row.classList.contains("code-row") && !row.classList.contains("diff-row"))) return;
      const codeCell = row.querySelector(".code");
      if (!codeCell) return;
      const kind = bubbleKind(bubble);
      const anchor = bubbleAnchorPoint(bubble, cRect, "code");

      // Measure the rendered text's right edge via a Range. This ignores
      // the grid cell's full width and gives us where the characters
      // actually stop. Fall back to the cell's own bounding rect for
      // empty lines.
      let textRight;
      let textMidY;
      const range = document.createRange();
      range.selectNodeContents(codeCell);
      const textRect = range.getBoundingClientRect();
      if (textRect.width > 0) {
        textRight = textRect.right;
        textMidY = textRect.top + textRect.height / 2;
      } else {
        const cellRect = codeCell.getBoundingClientRect();
        textRight = cellRect.left + 12;
        textMidY = cellRect.top + cellRect.height / 2;
      }

      // Add a tiny gap so the dot doesn't touch the last character.
      const targetX = textRight - cRect.left + 6;
      const targetY = textMidY - cRect.top;
      drawConnector(svg, anchor.x, anchor.y, targetX, targetY, kind, 10);
    });
  }

  // Diagram container (mermaid): bubble is in an overlay div, target is
  // an SVG element inside the panzoom-transformed scene group. We use
  // viewport coordinates converted to container-local.
  function refreshDiagramConnectors(placed) {
    if (!placed || !graphState.annotLayer) return;
    const svg = ensureConnectorLayer(el.graphCanvas);
    clearConnectors(svg);
    const cRect = el.graphCanvas.getBoundingClientRect();
    for (const p of placed) {
      const annotation = p.annotation;
      const target = findSvgTarget(graphState.mermaidSvg, annotation);
      if (!target || !p.bubble) continue;
      const variant = p.variant;
      const tRect = target.getBoundingClientRect();
      const anchor = bubbleAnchorPoint(p.bubble, cRect, variant);
      const targetPt = targetAnchorPoint(tRect, cRect, variant);
      const kind = annotation.user ? "user" : annotation.kind || "note";
      const bow = variant === "above" || variant === "below" ? 8 : 14;
      drawConnector(svg, anchor.x, anchor.y, targetPt.x, targetPt.y, kind, bow);
    }
  }

  // Graph container (vis-network node bubbles): target is a node whose
  // screen position we get from network.canvasToDOM(). We draw a short
  // arc from the bubble's left edge to the node's edge.
  function refreshGraphNodeConnectors() {
    if (!graphState.network || !graphState.annotLayer) return;
    const svg = ensureConnectorLayer(el.graphCanvas);
    clearConnectors(svg);
    const cRect = el.graphCanvas.getBoundingClientRect();
    const bubbles = graphState.annotLayer.querySelectorAll(".annot-bubble.floating");
    bubbles.forEach((bubble, i) => {
      const annotation = graphState.diagramAnnotations[i];
      const dom = annotation && graphAnnotDomPos(annotation);
      if (!dom) return;
      const anchor = bubbleAnchorPoint(bubble, cRect, "side-right");
      const kind = annotation.user ? "user" : annotation.kind || "note";
      drawConnector(svg, anchor.x, anchor.y, dom.x, dom.y, kind, 12);
    });
  }

  function makeFloatingBubble(annotation, { variant = "side" } = {}) {
    const b = document.createElement("div");
    b.className = "annot-bubble floating kind-" + (annotation.kind || "note");
    if (variant === "above") b.classList.add("above");
    const dot = document.createElement("span");
    dot.className = "annot-dot";
    b.appendChild(dot);
    b.appendChild(document.createTextNode(annotation.text));
    if (annotation.user) {
      b.classList.add("user-annot");
      const del = document.createElement("button");
      del.type = "button";
      del.className = "annot-del";
      del.title = "delete annotation";
      del.textContent = "×";
      del.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        try {
          await fetch(`/api/annotation/${annotation.id}`, { method: "DELETE" });
        } catch {}
      });
      b.appendChild(del);
    }
    return b;
  }

  function findSvgTarget(svg, annotation) {
    if (!svg) return null;
    if (annotation.user) return resolveUserSvgTarget(svg, annotation);
    let sel = annotation.selector || "";
    // Convenience: text_match:Foo finds an SVG text node whose text content
    // equals (or closely matches) "Foo". We return the <text> element
    // itself — not its closest <g>, because in sequence diagrams the
    // enclosing <g> is the whole actor column (lifeline + messages), which
    // would anchor the bubble to the middle of the diagram, not the label.
    if (sel.startsWith("text_match:")) {
      return findTextNode(svg, sel.slice("text_match:".length).trim());
    }
    if (annotation.text_match && !sel) {
      return findTextNode(svg, annotation.text_match);
    }
    if (!sel) return null;
    try {
      return svg.querySelector(sel);
    } catch {
      return null;
    }
  }

  function findTextNode(svg, needle) {
    if (!needle) return null;
    // Mermaid uses <text> for sequence diagrams and <foreignObject> with
    // embedded HTML for class, state, and flowchart diagrams. Search both.
    // Prefer exact trimmed matches so "Browser" doesn't latch onto
    // "browser.renderCode(entry)" in a message label.
    const candidates = Array.from(svg.querySelectorAll("text, foreignObject"));
    const exact = candidates.find((t) => t.textContent.trim() === needle);
    let hit =
      exact ||
      candidates.find((t) =>
        new RegExp(`(^|\\s)${escapeRegex(needle)}(\\s|$)`).test(t.textContent.trim()),
      ) ||
      candidates.find((t) => t.textContent.includes(needle)) ||
      null;
    if (!hit) return null;
    // Walk up to the nearest container group ("node", "cluster", or
    // actor wrapper). This lets the annotation anchor to the whole class
    // box / state box / flowchart node instead of just the label, so the
    // bubble can sit beside the full shape.
    const container = hit.closest("g.node, g.cluster, g.statediagram-state, g.actor-man");
    return container || hit;
  }

  function escapeRegex(s) {
    return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function repositionDiagramAnnotations() {
    if (!graphState.annotLayer) return;
    if (!graphState.mermaidSvg) return;
    const containerRect = el.graphCanvas.getBoundingClientRect();
    const bubbles = graphState.annotLayer.querySelectorAll(".annot-bubble.floating");
    const cw = containerRect.width;
    const ch = containerRect.height;

    // First pass: compute desired position per bubble and decide which
    // variant to use. Containers (class boxes, state nodes, flowchart
    // nodes) get side-anchored bubbles. Raw text/label targets (sequence
    // actors) get above-anchored bubbles.
    const placed = [];
    bubbles.forEach((b, i) => {
      const annotation = graphState.diagramAnnotations[i];
      if (!annotation) return;
      const target = findSvgTarget(graphState.mermaidSvg, annotation);
      if (!target) {
        b.classList.add("hidden-anchor");
        return;
      }
      b.classList.remove("hidden-anchor");
      const rect = target.getBoundingClientRect();
      const cls = (target.getAttribute && target.getAttribute("class")) || "";
      const isContainer =
        target.tagName.toLowerCase() === "g" &&
        (cls.includes("node") || cls.includes("cluster") || cls.includes("state") || cls.includes("actor"));

      const targetTop = rect.top - containerRect.top;
      const targetBottom = rect.bottom - containerRect.top;
      const targetLeft = rect.left - containerRect.left;
      const targetRight = rect.right - containerRect.left;
      const targetMidX = targetLeft + (targetRight - targetLeft) / 2;
      const targetMidY = targetTop + (targetBottom - targetTop) / 2;
      const bw = b.offsetWidth || 150;
      const bh = b.offsetHeight || 30;

      let variant = "above";
      let cx, cy;
      if (isContainer) {
        // Prefer side (right of target); flip to left if no room.
        const rightSpace = cw - targetRight;
        const leftSpace = targetLeft;
        if (rightSpace >= bw + 24 || rightSpace >= leftSpace) {
          variant = "side-right";
          cx = targetRight + 16 + (annotation.offset_x || 0);
          cy = targetMidY + (annotation.offset_y || 0);
        } else {
          variant = "side-left";
          cx = targetLeft - 16 - bw + (annotation.offset_x || 0);
          cy = targetMidY + (annotation.offset_y || 0);
        }
      } else {
        // Small text label: use above, flip to below if it would clip.
        cx = targetMidX + (annotation.offset_x || 0);
        if (targetTop - 14 - bh >= 6) {
          variant = "above";
          cy = targetTop - 14 + (annotation.offset_y || 0);
        } else {
          variant = "below";
          cy = targetBottom + 14 + (annotation.offset_y || 0);
        }
        // Centred bubbles on targets near the canvas edge (e.g. a comment
        // pinned to empty space) would hang off it; keep them inside.
        cx = Math.max(bw / 2 + 6, Math.min(cx, cw - bw / 2 - 6));
      }
      placed.push({ bubble: b, annotation, cx, cy, variant, bw, bh });
    });

    // Second pass: collision avoidance. Track occupied rectangles and
    // shift new placements that overlap.
    placed.sort((a, b) => a.cx - b.cx);
    const occupied = [];  // {xStart, xEnd, yTop, yBottom}
    const boxOf = (p, cx, cy) => {
      // Each variant has a different transform origin; compute the
      // bounding rect accordingly.
      if (p.variant === "above") {
        return { xStart: cx - p.bw / 2, xEnd: cx + p.bw / 2, yTop: cy - p.bh, yBottom: cy };
      }
      if (p.variant === "below") {
        return { xStart: cx - p.bw / 2, xEnd: cx + p.bw / 2, yTop: cy, yBottom: cy + p.bh };
      }
      if (p.variant === "side-right" || p.variant === "side-left") {
        return { xStart: cx, xEnd: cx + p.bw, yTop: cy - p.bh / 2, yBottom: cy + p.bh / 2 };
      }
      return { xStart: cx, xEnd: cx + p.bw, yTop: cy, yBottom: cy + p.bh };
    };
    const hitsAny = (box) =>
      occupied.some(
        (o) =>
          box.xStart < o.xEnd &&
          box.xEnd > o.xStart &&
          box.yTop < o.yBottom &&
          box.yBottom > o.yTop,
      );

    for (const p of placed) {
      let cy = p.cy;
      let box = boxOf(p, p.cx, cy);
      let safety = 20;
      while (hitsAny(box) && safety-- > 0) {
        if (p.variant === "above") cy -= p.bh + 6;
        else if (p.variant === "below") cy += p.bh + 6;
        else cy += p.bh + 6;  // side: stack downward
        box = boxOf(p, p.cx, cy);
      }
      occupied.push(box);
      p.bubble.classList.remove("above", "below", "side-right", "side-left");
      p.bubble.classList.add(p.variant);
      p.bubble.style.left = `${p.cx}px`;
      p.bubble.style.top = `${cy}px`;
    }

    refreshDiagramConnectors(placed);
  }

  function placeDiagramAnnotations(annotations) {
    const layer = ensureAnnotLayer();
    layer.innerHTML = "";
    graphState.diagramAnnotations = annotations || [];
    for (const a of graphState.diagramAnnotations) {
      layer.appendChild(makeFloatingBubble(a, { variant: "above" }));
    }
    // Two rAFs: first one flushes layout so offsetWidth/offsetHeight are
    // accurate on freshly inserted bubbles, second one places them.
    requestAnimationFrame(() => requestAnimationFrame(repositionDiagramAnnotations));
    if (graphState.diagramAnnotations.length) {
      ensureAnnotToggle();
      resetIdleDim();
    } else {
      removeAnnotToggle();
      cancelIdleDim();
    }
  }

  // panzoom batches DOM updates to the next rAF (via makeDirty). That
  // breaks us because we need to read getBoundingClientRect right after a
  // zoom to compute the follow-up pan. This helper reads panzoom's
  // internal transform and writes it to the scene group synchronously so
  // subsequent reads reflect the zoom immediately.
  function flushPanzoomTransform() {
    const pz = graphState.mermaidPanzoom;
    const scene = graphState.mermaidSceneGroup;
    if (!pz || !scene || !pz.getTransform) return;
    const t = pz.getTransform();
    scene.setAttribute(
      "transform",
      `matrix(${t.scale} 0 0 ${t.scale} ${t.x} ${t.y})`,
    );
  }

  // Intersect the graph-canvas bounds with the actual visible viewport.
  // On iPad Safari / PWA the visual viewport can be shorter than the
  // layout thinks (URL bar, home bar, tab bar, virtual keyboard), and
  // centering to the full canvas rect lands the target in an area that's
  // covered by chrome. visualViewport gives us the really-visible region.
  function visibleCanvasRect() {
    const cRect = el.graphCanvas.getBoundingClientRect();
    const vv = window.visualViewport;
    if (!vv) return cRect;
    const topVisible = Math.max(cRect.top, vv.offsetTop);
    const bottomVisible = Math.min(cRect.bottom, vv.offsetTop + vv.height);
    const leftVisible = Math.max(cRect.left, vv.offsetLeft);
    const rightVisible = Math.min(cRect.right, vv.offsetLeft + vv.width);
    if (bottomVisible <= topVisible || rightVisible <= leftVisible) return cRect;
    return {
      left: leftVisible,
      top: topVisible,
      right: rightVisible,
      bottom: bottomVisible,
      width: rightVisible - leftVisible,
      height: bottomVisible - topVisible,
    };
  }

  function focusOnSvgElement(target, { scale = 2.2, padding = 0.7 } = {}) {
    const pz = graphState.mermaidPanzoom;
    if (!pz || !target) return;

    const cRect = visibleCanvasRect();
    const tRect = target.getBoundingClientRect();

    let s = typeof scale === "number" ? scale : 2.2;
    if (scale === "fit") {
      const sx = (cRect.width * padding) / Math.max(tRect.width, 1);
      const sy = (cRect.height * padding) / Math.max(tRect.height, 1);
      s = Math.max(0.5, Math.min(sx, sy, 6));
    }

    // Step 1: zoom to absolute scale `s` around the target's current
    // viewport position. Flush the transform to the DOM right away so
    // step 2's getBoundingClientRect returns the post-zoom position.
    pz.zoomAbs(tRect.left + tRect.width / 2, tRect.top + tRect.height / 2, s);
    flushPanzoomTransform();

    // Step 2: pan so the target lands at the visible center (not the
    // canvas-rect center, which may be partly covered by chrome).
    const wantedX = cRect.left + cRect.width / 2;
    const wantedY = cRect.top + cRect.height / 2;
    const r = target.getBoundingClientRect();
    const dx = wantedX - (r.left + r.width / 2);
    const dy = wantedY - (r.top + r.height / 2);
    pz.moveBy(dx, dy, false);
    flushPanzoomTransform();

    repositionDiagramAnnotations();
  }

  function handleDiagramFocus(msg) {
    if (!graphState.mermaidPanzoom) return;
    const pz = graphState.mermaidPanzoom;

    if (msg.mode === "reset") {
      const scene = graphState.mermaidSceneGroup || graphState.mermaidSvg;
      if (!scene) return;
      const cRect = visibleCanvasRect();
      pz.zoomAbs(cRect.left + cRect.width / 2, cRect.top + cRect.height / 2, 1);
      flushPanzoomTransform();
      const r = scene.getBoundingClientRect();
      pz.moveBy(
        cRect.left + cRect.width / 2 - (r.left + r.width / 2),
        cRect.top + cRect.height / 2 - (r.top + r.height / 2),
        false,
      );
      flushPanzoomTransform();
      repositionDiagramAnnotations();
      return;
    }

    if (msg.mode === "selector" && msg.selector) {
      const target = findSvgTarget(graphState.mermaidSvg, { selector: msg.selector });
      if (target) {
        focusOnSvgElement(target, { scale: msg.scale, padding: msg.padding });
      }
      return;
    }

    if (msg.mode === "point") {
      // Interpret x/y as container-local coordinates. Convert to screen
      // coordinates and apply the same zoomAbs+moveBy dance, centering
      // on the visible viewport rather than the full canvas rect.
      const canvasRect = el.graphCanvas.getBoundingClientRect();
      const visible = visibleCanvasRect();
      const scale = typeof msg.scale === "number" ? msg.scale : 2;
      const targetScreenX = canvasRect.left + msg.x;
      const targetScreenY = canvasRect.top + msg.y;
      const centerScreenX = visible.left + visible.width / 2;
      const centerScreenY = visible.top + visible.height / 2;
      pz.zoomAbs(targetScreenX, targetScreenY, scale);
      flushPanzoomTransform();
      pz.moveBy(centerScreenX - targetScreenX, centerScreenY - targetScreenY, false);
      flushPanzoomTransform();
      repositionDiagramAnnotations();
    }
  }

  function attachMermaidPanzoom() {
    if (typeof window.panzoom === "undefined") return;
    const svg = el.graphCanvas.querySelector("svg");
    if (!svg) return;

    // Strip the max-width style Mermaid bakes in so the SVG can fill the
    // canvas.
    svg.style.maxWidth = "none";
    svg.style.maxHeight = "none";
    svg.style.width = "100%";
    svg.style.height = "100%";
    svg.removeAttribute("width");
    svg.removeAttribute("height");
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");

    // IMPORTANT: panzoom refuses to operate on the root <svg> element and
    // silently falls back to DOM-mode CSS transforms, whose coordinate
    // math doesn't line up with the SVG's viewBox / CTM. Wrap the svg's
    // current children in a <g> and hand that to panzoom instead — the
    // library's SVG controller takes over and uses getScreenCTM, which
    // keeps zoomAbs(clientX, clientY, ...) coordinates consistent with
    // getBoundingClientRect().
    let sceneGroup = svg.querySelector(":scope > g.buildspace-scene");
    if (!sceneGroup) {
      sceneGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
      sceneGroup.setAttribute("class", "buildspace-scene");
      while (svg.firstChild) {
        sceneGroup.appendChild(svg.firstChild);
      }
      svg.appendChild(sceneGroup);
    }

    const pz = window.panzoom(sceneGroup, {
      maxZoom: 12,
      minZoom: 0.25,
      smoothScroll: false,
      zoomDoubleClickSpeed: 1,  // disable double-click zoom so double-tap resets instead
      bounds: false,
      beforeWheel: (e) => !e.shiftKey,  // always allow wheel zoom
      beforeMouseDown: () => false,
    });
    graphState.mermaidPanzoom = pz;
    graphState.mermaidSvg = svg;
    graphState.mermaidSceneGroup = sceneGroup;
    if (typeof window !== "undefined") {
      window._pz = pz;
      window._svg = svg;
    }
    pz.on("transform", () => repositionDiagramAnnotations());
    pz.on("pan", () => repositionDiagramAnnotations());
    pz.on("zoom", () => repositionDiagramAnnotations());

    // Floating reset + zoom buttons.
    const hint = document.createElement("div");
    hint.className = "zoom-hint";
    const makeBtn = (label, title, onClick) => {
      const b = document.createElement("button");
      b.className = "zoom-btn";
      b.type = "button";
      b.textContent = label;
      b.title = title;
      b.addEventListener("click", (ev) => {
        ev.stopPropagation();
        onClick();
      });
      return b;
    };
    hint.appendChild(
      makeBtn("−", "zoom out", () => pz.smoothZoom(el.graphCanvas.clientWidth / 2, el.graphCanvas.clientHeight / 2, 0.7)),
    );
    hint.appendChild(
      makeBtn("⤢", "reset", () => {
        const t = pz.getTransform();
        pz.moveBy(-t.x, -t.y, false);
        pz.zoomAbs(0, 0, 1);
      }),
    );
    hint.appendChild(
      makeBtn("+", "zoom in", () => pz.smoothZoom(el.graphCanvas.clientWidth / 2, el.graphCanvas.clientHeight / 2, 1.4)),
    );
    el.graphCanvas.appendChild(hint);

    // Double-tap to reset on touch devices.
    let lastTap = 0;
    el.graphCanvas.addEventListener("touchend", (ev) => {
      if (ev.touches.length > 0) return;
      const now = Date.now();
      if (now - lastTap < 320) {
        const t = pz.getTransform();
        pz.moveBy(-t.x, -t.y, true);
        pz.zoomAbs(0, 0, 1);
      }
      lastTap = now;
    }, { passive: true });
  }

  // ---------- viewer comments on diagrams ----------
  // Same return channel as note comments: tap a diagram element (or empty
  // space) to leave a comment; it's stored server-side and the agent reads
  // it back with get_annotations(). Mermaid comments anchor to the tapped
  // element's label text (plus which occurrence of that text), with the
  // tap point in scene space as a fallback for unlabeled things like
  // arrows and lifelines. Graph comments anchor to a node id or a canvas
  // point.
  const DIAGRAM_CONTAINER_SEL = "g.node, g.cluster, g.statediagram-state, g.actor-man";

  function beginDiagramEntry(entry) {
    closeDiagramComposer();
    graphState.shownEntry = { id: entry.id, kind: entry.kind };
    graphState.agentAnnots = [];
    graphState.userAnnots = [];
  }

  async function loadDiagramUserAnnotations(entryId) {
    let annots = [];
    try {
      const r = await fetch(`/api/annotations/${entryId}`);
      if (!r.ok) return;
      annots = (await r.json()).annotations || [];
    } catch {
      return;
    }
    // The viewer may have moved on while we were fetching.
    if (!graphState.shownEntry || graphState.shownEntry.id !== entryId) return;
    graphState.userAnnots = annots.filter((a) => a.anchor);
    refreshDiagramAnnotations();
  }

  function refreshDiagramAnnotations() {
    const shown = graphState.shownEntry;
    if (!shown) return;
    const user = graphState.userAnnots.map((a) => ({
      user: true,
      id: a.id,
      text: a.comment,
      kind: "note",
      anchor: a.anchor,
      node: a.anchor.node,
    }));
    const all = graphState.agentAnnots.concat(user);
    if (shown.kind === "uml") {
      if (graphState.mermaidSvg) placeDiagramAnnotations(all);
    } else if (shown.kind === "graph") {
      if (graphState.network) placeGraphAnnotations(all);
    }
  }

  function svgLabelText(elm) {
    return (elm.textContent || "").replace(/\s+/g, " ").trim();
  }

  function svgLabels(svg) {
    return Array.from(svg.querySelectorAll("text, foreignObject"));
  }

  function resolveUserSvgTarget(svg, annotation) {
    const anc = annotation.anchor || {};
    if (anc.label) {
      const hits = svgLabels(svg).filter((t) => svgLabelText(t) === anc.label);
      const hit = hits[anc.nth || 0] || hits[0];
      if (hit) return hit.closest(DIAGRAM_CONTAINER_SEL) || hit;
    }
    if (anc.x == null) return null;
    return userPin(annotation.id, anc.x, anc.y);
  }

  // An invisible 2x2 rect inside the panzoom scene group, so point-anchored
  // comments move with pan/zoom and go through the same placement code.
  function userPin(id, x, y) {
    const scene = graphState.mermaidSceneGroup;
    if (!scene) return null;
    let pin = scene.querySelector(`:scope > rect.bs-user-pin[data-annot-id="${id}"]`);
    if (!pin) {
      pin = document.createElementNS(SVG_NS, "rect");
      pin.setAttribute("class", "bs-user-pin");
      pin.dataset.annotId = String(id);
      pin.setAttribute("x", String(x - 1));
      pin.setAttribute("y", String(y - 1));
      pin.setAttribute("width", "2");
      pin.setAttribute("height", "2");
      pin.setAttribute("fill", "none");
      pin.style.pointerEvents = "none";
      scene.appendChild(pin);
    }
    return pin;
  }

  // Work out what a tap on the mermaid svg landed on: the nearest label
  // (text or foreignObject), else the first label of the enclosing node,
  // else the lone label of the enclosing group (sequence actors). Returns
  // {label, nth} or null when the tap hit nothing labeled.
  function mermaidLabelAt(target) {
    const svg = graphState.mermaidSvg;
    let lab = target.closest("text, foreignObject");
    if (!lab) {
      const container = target.closest(DIAGRAM_CONTAINER_SEL);
      if (container) {
        lab = container.querySelector("text, foreignObject");
      } else {
        const g = target.closest("g");
        if (g && g !== graphState.mermaidSceneGroup) {
          const own = g.querySelectorAll(":scope > text, :scope > foreignObject");
          if (own.length === 1) lab = own[0];
        }
      }
    }
    if (!lab || !svg.contains(lab)) return null;
    const label = svgLabelText(lab);
    if (!label) return null;
    const same = svgLabels(svg).filter((t) => svgLabelText(t) === label);
    return { label, nth: Math.max(0, same.indexOf(lab)) };
  }

  function clientToScene(clientX, clientY) {
    const scene = graphState.mermaidSceneGroup;
    const m = scene && scene.getScreenCTM();
    if (!m) return null;
    const pt = graphState.mermaidSvg.createSVGPoint();
    pt.x = clientX;
    pt.y = clientY;
    const r = pt.matrixTransform(m.inverse());
    return { x: r.x, y: r.y };
  }

  function onMermaidTap(target, clientX, clientY) {
    const shown = graphState.shownEntry;
    if (!shown || shown.kind !== "uml" || !graphState.mermaidSvg) return;
    const pt = clientToScene(clientX, clientY);
    if (!pt) return;
    const hit = mermaidLabelAt(target);
    const anchor = { label: hit ? hit.label : null, nth: hit ? hit.nth : 0, x: pt.x, y: pt.y };
    openDiagramComposer(clientX, clientY, shown.id, anchor, hit ? hit.label : null);
  }

  function onGraphClick(params) {
    const shown = graphState.shownEntry;
    if (!shown || shown.kind !== "graph" || graphState.composer) return;
    let anchor;
    let preview = null;
    if (params.nodes && params.nodes.length) {
      const id = params.nodes[0];
      anchor = { node: id };
      const n = graphState.nodes && graphState.nodes.get(id);
      preview = (n && n.label) || String(id);
    } else {
      anchor = { x: params.pointer.canvas.x, y: params.pointer.canvas.y };
    }
    const cRect = el.graphCanvas.getBoundingClientRect();
    openDiagramComposer(
      cRect.left + params.pointer.DOM.x,
      cRect.top + params.pointer.DOM.y,
      shown.id,
      anchor,
      preview,
    );
  }

  // Tap detection for mermaid. panzoom swallows touch clicks, so we watch
  // pointer events instead: a short press that didn't move is a tap. On
  // touch, wait out the double-tap window so double-tap-to-reset still
  // works without popping a composer.
  (() => {
    let start = null;
    let pendingTap = null;
    el.graphCanvas.addEventListener("pointerdown", (ev) => {
      start = ev.isPrimary ? { x: ev.clientX, y: ev.clientY, t: Date.now() } : null;
    }, { passive: true });
    el.graphCanvas.addEventListener("pointerup", (ev) => {
      const s = start;
      start = null;
      if (!s || !ev.isPrimary || !graphState.mermaidSvg || graphState.composer) return;
      if (Math.hypot(ev.clientX - s.x, ev.clientY - s.y) > 6 || Date.now() - s.t > 600) return;
      if (!graphState.mermaidSvg.contains(ev.target)) return;
      const { target, clientX, clientY } = ev;
      if (ev.pointerType !== "touch") {
        onMermaidTap(target, clientX, clientY);
        return;
      }
      if (pendingTap) {
        clearTimeout(pendingTap);
        pendingTap = null;
        return;
      }
      pendingTap = setTimeout(() => {
        pendingTap = null;
        onMermaidTap(target, clientX, clientY);
      }, 330);
    }, { passive: true });
  })();

  function openDiagramComposer(clientX, clientY, entryId, anchor, preview) {
    closeDiagramComposer();
    const cRect = el.graphCanvas.getBoundingClientRect();
    const localX = clientX - cRect.left;
    const localY = clientY - cRect.top;

    const mark = document.createElement("div");
    mark.className = "diagram-composer-mark";
    mark.style.left = `${localX}px`;
    mark.style.top = `${localY}px`;

    const host = document.createElement("div");
    host.className = "note-composer diagram-composer";
    const hint = document.createElement("div");
    hint.className = "note-composer-hint";
    hint.textContent = preview ? `re: ${preview.slice(0, 80)}` : "comment on this spot";
    const ta = document.createElement("textarea");
    ta.placeholder = "annotation…";
    ta.rows = 2;
    const actions = document.createElement("div");
    actions.className = "note-composer-actions";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "btn-secondary";
    cancel.textContent = "cancel";
    const save = document.createElement("button");
    save.type = "button";
    save.className = "btn-primary";
    save.textContent = "save";
    actions.appendChild(cancel);
    actions.appendChild(save);
    host.appendChild(hint);
    host.appendChild(ta);
    host.appendChild(actions);
    // Keep taps inside the composer away from panzoom / vis-network.
    ["pointerdown", "mousedown", "touchstart", "wheel", "click"].forEach((evt) =>
      host.addEventListener(evt, (ev) => ev.stopPropagation()),
    );

    el.graphCanvas.appendChild(mark);
    el.graphCanvas.appendChild(host);
    graphState.composer = { host, mark };

    // Beside the tap point, flipping left when there's no room on the right;
    // vertically centred on it, clamped inside the canvas.
    const w = host.offsetWidth || 260;
    const h = host.offsetHeight || 120;
    let x = localX + 16;
    if (x + w > cRect.width - 8) x = localX - 16 - w;
    x = Math.max(8, Math.min(x, cRect.width - w - 8));
    const y = Math.max(8, Math.min(localY - h / 2, cRect.height - h - 8));
    host.style.left = `${x}px`;
    host.style.top = `${y}px`;
    requestAnimationFrame(() => ta.focus());

    cancel.addEventListener("click", closeDiagramComposer);
    ta.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") { ev.preventDefault(); closeDiagramComposer(); }
      if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) {
        ev.preventDefault();
        save.click();
      }
    });
    save.addEventListener("click", async () => {
      const comment = ta.value.trim();
      if (!comment) { closeDiagramComposer(); return; }
      save.disabled = true;
      try {
        const r = await fetch("/api/annotation", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ entry_id: entryId, comment, anchor, block_preview: preview }),
        });
        if (!r.ok) throw new Error(String(r.status));
      } catch {
        toast("couldn't save annotation");
      }
      closeDiagramComposer();
    });
  }

  function closeDiagramComposer() {
    const c = graphState.composer;
    if (!c) return;
    c.host.remove();
    c.mark.remove();
    graphState.composer = null;
  }

  // ---------- graph rendering ----------
  const GROUP_PALETTE = {
    default: { background: "#1b2030", border: "#555e74", font: "#d7dbe3" },
    class: { background: "#3a2e1d", border: "#e8b87d", font: "#f4e0c0" },
    db: { background: "#192d3e", border: "#6cc4ff", font: "#cfe6fa" },
    interface: { background: "#1e3024", border: "#7cd97c", font: "#d4f0d4" },
    external: { background: "#2a2e38", border: "#8b93a4", font: "#c8ccd4" },
    service: { background: "#2f1d30", border: "#d488e8", font: "#e8d0ec" },
  };

  function visNodeFromModel(n, highlights) {
    const g = GROUP_PALETTE[n.group] || GROUP_PALETTE.default;
    const isHl = highlights.has(n.id);
    return {
      id: n.id,
      label: n.label || n.id,
      title: n.title || undefined,
      shape: n.shape || "dot",
      size: n.size || (isHl ? 28 : 22),
      borderWidth: isHl ? 3 : 2,
      font: { color: g.font, size: 13, face: "Inter, system-ui, sans-serif" },
      color: {
        background: n.color || g.background,
        border: isHl ? "#e8b87d" : g.border,
        highlight: { background: n.color || g.background, border: "#e8b87d" },
        hover: { background: n.color || g.background, border: "#e8b87d" },
      },
      shadow: isHl
        ? { enabled: true, color: "rgba(232,184,125,0.6)", size: 18, x: 0, y: 0 }
        : { enabled: true, color: "rgba(0,0,0,0.45)", size: 10, x: 0, y: 4 },
    };
  }

  function visEdgeFromModel(e, seq) {
    return {
      id: e._eid || `e${seq}`,
      from: e.source,
      to: e.target,
      label: e.label || undefined,
      dashes: e.dashes || e.kind === "references",
      font: {
        color: "#8b93a4",
        size: 11,
        face: "Inter, system-ui, sans-serif",
        background: "rgba(14,17,22,0.85)",
        strokeWidth: 0,
      },
      color: { color: "#4a5270", highlight: "#e8b87d", hover: "#e8b87d" },
      arrows: { to: { enabled: true, scaleFactor: 0.55, type: "arrow" } },
      smooth: { enabled: true, type: "continuous", roundness: 0.35 },
      width: 1.6,
    };
  }

  function renderGraph(entry) {
    el.graphEmpty.hidden = true;
    el.graphView.hidden = false;
    el.graphCanvas.classList.remove("mermaid-host");
    disposeMermaidPanzoom();
    beginDiagramEntry(entry);
    const p = entry.payload || {};
    el.graphTitle.textContent = entry.title || "graph";
    updateGraphMeta(p);

    const highlights = new Set(p.highlights || []);
    const nodes = (p.nodes || []).map((n) => visNodeFromModel(n, highlights));
    graphState.edgeKey = new Map();
    const edges = (p.edges || []).map((e, i) => {
      const visEdge = visEdgeFromModel(e, i);
      graphState.edgeKey.set(`${e.source}|${e.target}`, visEdge.id);
      return visEdge;
    });

    const options = {
      autoResize: true,
      nodes: {
        shape: "dot",
        size: 22,
        borderWidth: 2,
        scaling: { label: { enabled: true, min: 11, max: 18 } },
      },
      edges: {
        smooth: { enabled: true, type: "continuous", roundness: 0.35 },
      },
      physics: {
        enabled: true,
        solver: "forceAtlas2Based",
        forceAtlas2Based: {
          gravitationalConstant: -85,
          centralGravity: 0.012,
          springConstant: 0.09,
          springLength: 180,
          damping: 0.45,
          avoidOverlap: 1,
        },
        stabilization: { enabled: true, iterations: 260, updateInterval: 30 },
        timestep: 0.4,
      },
      interaction: {
        hover: true,
        dragNodes: true,
        dragView: true,
        zoomView: true,
        tooltipDelay: 120,
        navigationButtons: false,
      },
    };

    // Build fresh DataSets so incremental patches can mutate them in place
    // and vis-network animates node additions/removals rather than redrawing.
    if (graphState.network) graphState.network.destroy();
    graphState.nodes = new vis.DataSet(nodes);
    graphState.edges = new vis.DataSet(edges);
    graphState.network = new vis.Network(
      el.graphCanvas,
      { nodes: graphState.nodes, edges: graphState.edges },
      options,
    );
    graphState.currentId = entry.id;
    graphState.network.once("stabilizationIterationsDone", () => {
      graphState.network.fit({ animation: { duration: 500, easingFunction: "easeOutQuad" } });
    });

    // Node-anchored annotations (pushed ones now, the viewer's once fetched).
    graphState.network.on("afterDrawing", repositionGraphAnnotations);
    graphState.network.on("click", onGraphClick);
    graphState.agentAnnots = p.annotations || [];
    refreshDiagramAnnotations();
    loadDiagramUserAnnotations(entry.id);
  }

  function placeGraphAnnotations(annotations) {
    graphState.diagramAnnotations = annotations;
    if (annotations.length) {
      const layer = ensureAnnotLayer();
      layer.innerHTML = "";
      for (const a of annotations) layer.appendChild(makeFloatingBubble(a));
      requestAnimationFrame(repositionGraphAnnotations);
      ensureAnnotToggle();
      resetIdleDim();
    } else {
      if (graphState.annotLayer) {
        graphState.annotLayer.remove();
        graphState.annotLayer = null;
      }
      const svg = el.graphCanvas.querySelector(":scope > svg.connector-layer");
      if (svg) svg.remove();
      removeAnnotToggle();
      cancelIdleDim();
    }
  }

  // Screen position (graph-canvas local) of a graph annotation's target:
  // a node, or a free canvas point for comments left on empty space.
  function graphAnnotDomPos(annotation) {
    const net = graphState.network;
    if (!net) return null;
    if (annotation.node != null) {
      const pos = net.getPositions([annotation.node])[annotation.node];
      return pos ? net.canvasToDOM(pos) : null;
    }
    const anc = annotation.anchor;
    if (anc && anc.x != null) return net.canvasToDOM({ x: anc.x, y: anc.y });
    return null;
  }

  function repositionGraphAnnotations() {
    if (!graphState.network || !graphState.annotLayer) return;
    const bubbles = graphState.annotLayer.querySelectorAll(".annot-bubble.floating");
    bubbles.forEach((b, i) => {
      const annotation = graphState.diagramAnnotations[i];
      if (!annotation) return;
      const dom = graphAnnotDomPos(annotation);
      if (!dom) {
        b.classList.add("hidden-anchor");
        return;
      }
      b.classList.remove("hidden-anchor");
      const offX = annotation.offset_x ?? 36;
      const offY = annotation.offset_y ?? 0;
      b.style.left = `${dom.x + offX}px`;
      b.style.top = `${dom.y + offY}px`;
    });
    refreshGraphNodeConnectors();
  }

  function updateGraphMeta(p) {
    const bits = [];
    if (p.stats) bits.push(`${p.stats.nodes} nodes · ${p.stats.edges} edges`);
    el.graphMeta.textContent = bits.join("  ·  ");
  }

  function applyGraphPatch(patch) {
    if (!graphState.network || graphState.nodes === null) return;
    if (patch.target_id !== undefined && patch.target_id !== graphState.currentId) return;

    // Highlights may change the visual style of existing nodes: recompute
    // them by pulling current data out of the DataSet and re-applying.
    let highlightsChanged = false;
    if (patch.highlights !== undefined) highlightsChanged = true;

    if (patch.remove_nodes?.length) {
      graphState.nodes.remove(patch.remove_nodes);
      // Drop any edges whose endpoints were removed.
      const removedSet = new Set(patch.remove_nodes);
      const edgesToRemove = [];
      graphState.edges.forEach((e) => {
        if (removedSet.has(e.from) || removedSet.has(e.to)) edgesToRemove.push(e.id);
      });
      if (edgesToRemove.length) graphState.edges.remove(edgesToRemove);
    }

    const currentHighlights = computeCurrentHighlights(patch);

    if (patch.add_nodes?.length) {
      graphState.nodes.add(patch.add_nodes.map((n) => visNodeFromModel(n, currentHighlights)));
    }

    if (patch.update_nodes?.length) {
      graphState.nodes.update(
        patch.update_nodes.map((n) => visNodeFromModel(n, currentHighlights)),
      );
    }

    if (patch.remove_edges?.length) {
      const ids = [];
      for (const [src, tgt] of patch.remove_edges) {
        const key = `${src}|${tgt}`;
        const id = graphState.edgeKey.get(key);
        if (id) {
          ids.push(id);
          graphState.edgeKey.delete(key);
        }
      }
      if (ids.length) graphState.edges.remove(ids);
    }

    if (patch.add_edges?.length) {
      const seqBase = graphState.edgeKey.size + graphState.edges.length + 1;
      const visEdges = patch.add_edges.map((e, i) => {
        const ve = visEdgeFromModel(e, seqBase + i);
        graphState.edgeKey.set(`${e.source}|${e.target}`, ve.id);
        return ve;
      });
      graphState.edges.add(visEdges);
    }

    if (highlightsChanged) {
      // Re-style every existing node so removed highlights drop their glow.
      const updated = [];
      graphState.nodes.forEach((existing) => {
        const model = {
          id: existing.id,
          label: existing.label,
          title: existing.title,
          shape: existing.shape,
          group: existing._group,
        };
        updated.push(visNodeFromModel(model, currentHighlights));
      });
      if (updated.length) graphState.nodes.update(updated);
    }

    if (patch.focus) {
      graphState.network.focus(patch.focus, {
        scale: 1.1,
        animation: { duration: 650, easingFunction: "easeInOutCubic" },
      });
    }

    // Update stats in meta line without requerying the server.
    updateGraphMeta({ stats: { nodes: graphState.nodes.length, edges: graphState.edges.length } });
  }

  function computeCurrentHighlights(patch) {
    if (patch.highlights !== undefined) return new Set(patch.highlights);
    // Fall back to what's still in the DataSet — nodes we styled with the
    // highlight glow have borderWidth 3.
    const out = new Set();
    if (!graphState.nodes) return out;
    graphState.nodes.forEach((n) => {
      if (n.borderWidth === 3) out.add(n.id);
    });
    return out;
  }

  function placeAnnotations(container, annotations, rowSelector = ".code-row") {
    if (!annotations.length) {
      const existing = container.querySelector(":scope > svg.connector-layer");
      if (existing) existing.remove();
      return;
    }

    const bubbles = annotations.map((a) => {
      const row = container.querySelector(`${rowSelector}[data-line="${a.line}"]`);
      const b = document.createElement("div");
      b.className = "annot-bubble kind-" + (a.kind || "note");
      const dot = document.createElement("span");
      dot.className = "annot-dot";
      b.appendChild(dot);
      b.appendChild(document.createTextNode(a.text));
      if (row && row.nextSibling) {
        container.insertBefore(b, row.nextSibling);
      } else {
        container.appendChild(b);
      }
      return { a, b };
    });

    // Two-pass layout so we can measure heights and resolve collisions.
    requestAnimationFrame(() => {
      const bodyRect = container.getBoundingClientRect();
      const placements = [];

      for (const { a, b } of bubbles) {
        const row = container.querySelector(`${rowSelector}[data-line="${a.line}"]`);
        if (!row) continue;
        const rect = row.getBoundingClientRect();
        const targetCenter = rect.top + rect.height / 2 - bodyRect.top;
        const h = b.offsetHeight || 40;
        const halfH = h / 2;
        placements.push({ b, h, halfH, desired: targetCenter });
      }

      placements.sort((x, y) => x.desired - y.desired);

      // Greedy collision resolution: ensure adjacent bubbles don't overlap.
      const gap = 10;
      let prevBottom = -Infinity;
      for (const p of placements) {
        let top = p.desired - p.halfH;
        if (top < prevBottom + gap) top = prevBottom + gap;
        p.top = top;
        prevBottom = top + p.h;
        // Position by top rather than center so collision math is trivial.
        p.b.style.top = (top + p.halfH) + "px";
      }

      refreshCodeConnectors(container);
    });
  }

  // Markdown may carry raw HTML, and marked passes it through untouched. A
  // note can quote an untrusted README or web page, and any script it smuggles
  // in would run as this origin, where the Origin guard cannot see it. So the
  // HTML is always sanitized, and without DOMPurify we fall back to plain text
  // rather than render unsanitized.
  function canRenderMarkdown() {
    return typeof marked !== "undefined" && typeof DOMPurify !== "undefined";
  }

  function renderMarkdown(src) {
    return DOMPurify.sanitize(marked.parse(src), { USE_PROFILES: { html: true } });
  }

  function escapeHtml(s) {
    return s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // ---------- navigation / follow ----------
  function setActiveEntry(id, { fromUser = false, seekTime = null } = {}) {
    state.currentId = id;
    const entry = state.entries.find((e) => e.id === id);
    if (!entry) return;
    if (entry.kind === "code") {
      switchTab("code");
      renderCode(entry);
    } else if (entry.kind === "diff") {
      switchTab("diff");
      renderDiff(entry);
    } else if (entry.kind === "graph") {
      switchTab("graph");
      renderGraph(entry);
    } else if (entry.kind === "uml") {
      switchTab("graph");
      renderUml(entry);
    } else if (entry.kind === "note") {
      switchTab("note");
      renderNote(entry);
    } else if (entry.kind === "video") {
      switchTab("video");
      renderVideo(entry, seekTime);
    }
    renderTimeline();
    if (fromUser) disableAutofollow("browsing");
  }

  function userNavigateTo(id, { seekTime = null } = {}) {
    setActiveEntry(id, { fromUser: true, seekTime });
    if (window.innerWidth <= 820) el.layout.classList.remove("sidebar-open");
  }

  function switchTab(name) {
    if (!el.panes[name]) return;
    state.activeTab = name;
    for (const t of el.tabs) t.classList.toggle("active", t.dataset.tab === name);
    for (const [k, pane] of Object.entries(el.panes)) pane.hidden = k !== name;
  }

  function disableAutofollow(reason) {
    if (!state.autofollow) return;
    state.autofollow = false;
    el.autofollow.checked = false;
    if (reason) toast("auto-follow off — " + reason);
  }

  function setAutofollow(value, { source = "user", flash = false } = {}) {
    state.autofollow = !!value;
    el.autofollow.checked = !!value;
    if (flash) {
      el.followLabel.classList.remove("flash");
      void el.followLabel.offsetWidth;
      el.followLabel.classList.add("flash");
    }
    if (source === "server") {
      toast(`The agent turned auto-follow ${value ? "on" : "off"}`);
    }
  }

  // ---------- websocket ----------
  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.addEventListener("open", () => {
      el.connDot.classList.remove("disconnected");
      el.connDot.classList.add("connected");
    });
    ws.addEventListener("close", () => {
      el.connDot.classList.remove("connected");
      el.connDot.classList.add("disconnected");
      setTimeout(connect, 1200);
    });
    ws.addEventListener("message", (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      handleMessage(msg);
    });
  }

  function handleMessage(msg) {
    if (msg.type === "entry_added") {
      state.entries.unshift(msg.entry);
      if (state.entries.length > 500) state.entries.pop();
      renderTimeline();
      if (state.autofollow) setActiveEntry(msg.entry.id);
      else toast("new: " + (msg.entry.title || msg.entry.payload?.filename || "entry"));
    } else if (msg.type === "tab") {
      switchTab(msg.tab);
    } else if (msg.type === "autofollow") {
      setAutofollow(msg.value, { source: msg.source || "server", flash: true });
    } else if (msg.type === "cleared") {
      state.entries = [];
      state.currentId = null;
      renderTimeline();
      el.codeView.hidden = true;
      el.codeEmpty.hidden = false;
      el.diffView.hidden = true;
      el.diffEmpty.hidden = false;
      if (graphState.network) {
        graphState.network.destroy();
        graphState.network = null;
        graphState.nodes = null;
        graphState.edges = null;
      }
      closeDiagramComposer();
      graphState.shownEntry = null;
      el.graphView.hidden = true;
      el.graphEmpty.hidden = false;
      el.graphCanvas.classList.remove("mermaid-host");
      el.graphCanvas.innerHTML = "";
      el.noteView.hidden = true;
      el.noteEmpty.hidden = false;
    } else if (msg.type === "entry_deleted") {
      const wasActive = state.currentId === msg.id;
      state.entries = state.entries.filter((e) => e.id !== msg.id);
      renderTimeline();
      if (wasActive) {
        if (state.entries.length) {
          setActiveEntry(state.entries[0].id);
        } else {
          state.currentId = null;
          el.codeView.hidden = true; el.codeEmpty.hidden = false;
          el.diffView.hidden = true; el.diffEmpty.hidden = false;
          el.graphView.hidden = true; el.graphEmpty.hidden = false;
          el.noteView.hidden = true; el.noteEmpty.hidden = false;
        }
      }
    } else if (msg.type === "graph_patch") {
      applyGraphPatch(msg.patch);
    } else if (msg.type === "diagram_focus") {
      handleDiagramFocus(msg);
    } else if (msg.type === "note_annotation_added") {
      const a = msg.annotation;
      if (a && String(a.entry_id) === el.noteBody.dataset.entryId) {
        // Easiest correct behavior: re-fetch all annotations and re-place
        // them so collision resolution runs with the new bubble included.
        loadNoteAnnotations(a.entry_id);
      }
      if (a && graphState.shownEntry && a.entry_id === graphState.shownEntry.id) {
        loadDiagramUserAnnotations(a.entry_id);
      }
    } else if (msg.type === "note_annotation_deleted") {
      const currentEntry = el.noteBody.dataset.entryId;
      if (currentEntry) loadNoteAnnotations(parseInt(currentEntry, 10));
      if (graphState.shownEntry) loadDiagramUserAnnotations(graphState.shownEntry.id);
    }
  }

  // ---------- bootstrap ----------
  async function loadHistory() {
    const r = await fetch("/api/history");
    const data = await r.json();
    state.entries = data.entries || [];
    renderTimeline();
    if (!navigateFromHash() && state.entries.length) {
      setActiveEntry(state.entries[0].id);
    }
  }

  function bindUI() {
    el.autofollow.addEventListener("change", () => {
      state.autofollow = el.autofollow.checked;
      if (state.autofollow && state.entries.length) {
        setActiveEntry(state.entries[0].id);
      }
    });

    for (const t of el.tabs) {
      t.addEventListener("click", () => {
        if (t.disabled) return;
        switchTab(t.dataset.tab);
      });
    }

    el.sidebarToggle.addEventListener("click", () => {
      if (window.innerWidth <= 820) {
        el.layout.classList.toggle("sidebar-open");
      } else {
        el.layout.classList.toggle("sidebar-collapsed");
      }
    });

    el.clearBtn.addEventListener("click", async () => {
      if (!confirm("Clear all timeline entries?")) return;
      await fetch("/api/clear", { method: "POST", headers: { "content-type": "application/json" }, body: "{}" });
    });
  }

  function registerServiceWorker() {
    if (!("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
  }

  function navigateFromHash() {
    const hash = decodeURIComponent(window.location.hash || "");
    if (!hash.startsWith("#entry-")) return false;
    const timePart = hash.match(/@t=([\d.]+)/);
    const seekTime = timePart ? parseFloat(timePart[1]) : null;
    const cleanHash = hash.replace(/@t=[\d.]+/, "");
    const id = parseInt(cleanHash.slice("#entry-".length), 10);
    if (!Number.isFinite(id)) return false;
    const entry = state.entries.find((e) => e.id === id);
    if (!entry) return false;
    userNavigateTo(id, { seekTime });
    return true;
  }

  window.addEventListener("hashchange", () => navigateFromHash());

  bindUI();
  loadHistory();
  connect();
  registerServiceWorker();
})();
