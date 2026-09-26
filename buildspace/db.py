# Copyright 2026 Reality Checkpoint
# SPDX-License-Identifier: Apache-2.0
import json
import os
import sqlite3
import time
from pathlib import Path

# Override with BUILDSPACE_DB to run a second instance (or tests) against
# a different database file.
DB_PATH = Path(os.environ.get("BUILDSPACE_DB") or (Path.home() / ".buildspace" / "buildspace.db")).expanduser()


def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                kind TEXT NOT NULL,
                title TEXT,
                payload TEXT NOT NULL
            )
            """
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_entries_created ON entries(created_at DESC)")
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS note_annotations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id INTEGER NOT NULL,
                block_index INTEGER NOT NULL,
                block_preview TEXT,
                comment TEXT NOT NULL,
                kind TEXT,
                created_at REAL NOT NULL,
                line_index INTEGER DEFAULT NULL,
                FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE CASCADE
            )
            """
        )
        # Migrations for existing DBs that predate line_index / anchor.
        # `anchor` is a JSON object locating a comment on a diagram (uml or
        # graph entry); note comments keep using block_index/line_index.
        for ddl in (
            "ALTER TABLE note_annotations ADD COLUMN line_index INTEGER DEFAULT NULL",
            "ALTER TABLE note_annotations ADD COLUMN anchor TEXT DEFAULT NULL",
            # Where an entry came from (agent session id, cwd, transcript
            # path), so a responder can answer comments with that context.
            "ALTER TABLE entries ADD COLUMN origin TEXT DEFAULT NULL",
        ):
            try:
                c.execute(ddl)
            except Exception:
                pass
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_note_annot_entry ON note_annotations(entry_id)"
        )
        # The conversation hanging off an annotation: the viewer's follow-ups
        # (author "user") and responder answers (author "agent"), which go
        # pending -> done | error.
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS annotation_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                annotation_id INTEGER NOT NULL,
                author TEXT NOT NULL,
                text TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'done',
                meta TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(annotation_id) REFERENCES note_annotations(id) ON DELETE CASCADE
            )
            """
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_annot_msg_annot ON annotation_messages(annotation_id)"
        )


def add_entry(kind: str, title: str | None, payload: dict, origin: dict | None = None) -> dict:
    now = time.time()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO entries (created_at, kind, title, payload, origin) VALUES (?, ?, ?, ?, ?)",
            (now, kind, title, json.dumps(payload), json.dumps(origin) if origin else None),
        )
        entry_id = cur.lastrowid
    return {
        "id": entry_id,
        "created_at": now,
        "kind": kind,
        "title": title,
        "payload": payload,
    }


def list_entries(limit: int = 200) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, created_at, kind, title, payload FROM entries ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "created_at": r["created_at"],
            "kind": r["kind"],
            "title": r["title"],
            "payload": json.loads(r["payload"]),
        }
        for r in rows
    ]


def get_entry(entry_id: int) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT id, created_at, kind, title, payload FROM entries WHERE id = ?",
            (entry_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "kind": row["kind"],
        "title": row["title"],
        "payload": json.loads(row["payload"]),
    }


def get_entry_origin(entry_id: int) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT origin FROM entries WHERE id = ?", (entry_id,)).fetchone()
    return json.loads(row["origin"]) if row and row["origin"] else None


def update_payload(entry_id: int, payload: dict) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE entries SET payload = ? WHERE id = ?",
            (json.dumps(payload), entry_id),
        )


def latest_of_kind(kind: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT id, created_at, kind, title, payload FROM entries WHERE kind = ? ORDER BY id DESC LIMIT 1",
            (kind,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "kind": row["kind"],
        "title": row["title"],
        "payload": json.loads(row["payload"]),
    }


def delete_entry(entry_id: int) -> bool:
    with _conn() as c:
        cur = c.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
    return cur.rowcount > 0


def clear_entries():
    with _conn() as c:
        c.execute("DELETE FROM entries")
        c.execute("DELETE FROM note_annotations")
        c.execute("DELETE FROM annotation_messages")


def add_note_annotation(
    entry_id: int,
    block_index: int,
    comment: str,
    block_preview: str | None = None,
    kind: str | None = None,
    line_index: int | None = None,
    anchor: dict | None = None,
) -> dict:
    now = time.time()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO note_annotations "
            "(entry_id, block_index, block_preview, comment, kind, created_at, line_index, anchor) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (entry_id, block_index, block_preview, comment, kind, now, line_index,
             json.dumps(anchor) if anchor is not None else None),
        )
        annot_id = cur.lastrowid
    return {
        "id": annot_id,
        "entry_id": entry_id,
        "block_index": block_index,
        "block_preview": block_preview,
        "comment": comment,
        "kind": kind,
        "created_at": now,
        "line_index": line_index,
        "anchor": anchor,
    }


def list_note_annotations(entry_id: int) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, entry_id, block_index, block_preview, comment, kind, created_at, "
            "line_index, anchor "
            "FROM note_annotations WHERE entry_id = ? ORDER BY id ASC",
            (entry_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["anchor"] = json.loads(d["anchor"]) if d["anchor"] else None
        d["messages"] = list_annotation_messages(d["id"])
        out.append(d)
    return out


def delete_note_annotation(annot_id: int) -> bool:
    with _conn() as c:
        cur = c.execute("DELETE FROM note_annotations WHERE id = ?", (annot_id,))
        c.execute("DELETE FROM annotation_messages WHERE annotation_id = ?", (annot_id,))
    return cur.rowcount > 0


def get_note_annotation(annot_id: int) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT id, entry_id, block_index, block_preview, comment, kind, created_at, "
            "line_index, anchor FROM note_annotations WHERE id = ?",
            (annot_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["anchor"] = json.loads(d["anchor"]) if d["anchor"] else None
    return d


def _message(row) -> dict:
    d = dict(row)
    d["meta"] = json.loads(d["meta"]) if d["meta"] else {}
    return d


def add_annotation_message(annotation_id: int, author: str, text: str = "",
                           status: str = "done", meta: dict | None = None) -> dict:
    now = time.time()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO annotation_messages "
            "(annotation_id, author, text, status, meta, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (annotation_id, author, text, status, json.dumps(meta or {}), now, now),
        )
        row = c.execute("SELECT * FROM annotation_messages WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _message(row)


def update_annotation_message(msg_id: int, *, text: str | None = None,
                              status: str | None = None, meta: dict | None = None) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM annotation_messages WHERE id = ?", (msg_id,)).fetchone()
        if not row:
            return None
        cur = _message(row)
        c.execute(
            "UPDATE annotation_messages SET text = ?, status = ?, meta = ?, updated_at = ? WHERE id = ?",
            (
                cur["text"] if text is None else text,
                cur["status"] if status is None else status,
                json.dumps(cur["meta"] if meta is None else meta),
                time.time(),
                msg_id,
            ),
        )
        row = c.execute("SELECT * FROM annotation_messages WHERE id = ?", (msg_id,)).fetchone()
    return _message(row)


def list_annotation_messages(annotation_id: int) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM annotation_messages WHERE annotation_id = ? ORDER BY id ASC",
            (annotation_id,),
        ).fetchall()
    return [_message(r) for r in rows]


def fail_pending_messages(reason: str) -> int:
    """Mark answers left pending by a restart as failed, so the UI doesn't
    show "thinking" forever."""
    with _conn() as c:
        cur = c.execute(
            "UPDATE annotation_messages SET status = 'error', text = ?, updated_at = ? "
            "WHERE status = 'pending'",
            (reason, time.time()),
        )
    return cur.rowcount
