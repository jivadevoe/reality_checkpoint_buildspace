# Copyright 2026 Reality Checkpoint
# SPDX-License-Identifier: Apache-2.0
import difflib
from typing import Literal, TypedDict


class DiffLine(TypedDict, total=False):
    type: Literal["context", "add", "remove"]
    before: int | None
    after: int | None
    content: str


def compute_diff(before: str, after: str) -> list[DiffLine]:
    """Compute a per-line unified diff.

    Returns a flat list of line records. Each record is one display row
    in the diff viewer — context lines have both before/after numbers,
    added lines have only `after`, removed lines have only `before`.
    """
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    sm = difflib.SequenceMatcher(None, before_lines, after_lines, autojunk=False)
    rows: list[DiffLine] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                rows.append(
                    {
                        "type": "context",
                        "before": i1 + k + 1,
                        "after": j1 + k + 1,
                        "content": before_lines[i1 + k],
                    }
                )
        elif tag == "delete":
            for k in range(i2 - i1):
                rows.append(
                    {
                        "type": "remove",
                        "before": i1 + k + 1,
                        "after": None,
                        "content": before_lines[i1 + k],
                    }
                )
        elif tag == "insert":
            for k in range(j2 - j1):
                rows.append(
                    {
                        "type": "add",
                        "before": None,
                        "after": j1 + k + 1,
                        "content": after_lines[j1 + k],
                    }
                )
        elif tag == "replace":
            for k in range(i2 - i1):
                rows.append(
                    {
                        "type": "remove",
                        "before": i1 + k + 1,
                        "after": None,
                        "content": before_lines[i1 + k],
                    }
                )
            for k in range(j2 - j1):
                rows.append(
                    {
                        "type": "add",
                        "before": None,
                        "after": j1 + k + 1,
                        "content": after_lines[j1 + k],
                    }
                )
    return rows
