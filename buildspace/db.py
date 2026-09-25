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
        ):
            try:
                c.execute(ddl)
            except Exception:
                pass
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_note_annot_entry ON note_annotations(entry_id)"
        )


def add_entry(kind: str, title: str | None, payload: dict) -> dict:
    now = time.time()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO entries (created_at, kind, title, payload) VALUES (?, ?, ?, ?)",
            (now, kind, title, json.dumps(payload)),
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
        out.append(d)
    return out


def delete_note_annotation(annot_id: int) -> bool:
    with _conn() as c:
        cur = c.execute("DELETE FROM note_annotations WHERE id = ?", (annot_id,))
    return cur.rowcount > 0
