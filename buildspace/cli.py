# Copyright 2026 Reality Checkpoint
# SPDX-License-Identifier: Apache-2.0
import argparse
import json
import sys

from buildspace import client


def _parse_annotations(items: list[str]) -> list[dict]:
    out: list[dict] = []
    for raw in items:
        if ":" not in raw:
            raise SystemExit(f"annotation must be LINE:TEXT, got {raw!r}")
        line_str, text = raw.split(":", 1)
        try:
            line = int(line_str)
        except ValueError:
            raise SystemExit(f"annotation line must be integer, got {line_str!r}")
        out.append({"line": line, "text": text, "kind": "note"})
    return out


def _parse_highlights(raw: str | None) -> list[int] | None:
    if not raw:
        return None
    result: list[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            a, b = chunk.split("-", 1)
            result.extend(range(int(a), int(b) + 1))
        else:
            result.append(int(chunk))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="buildspace")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_code = sub.add_parser("code", help="show a source file")
    p_code.add_argument("path")
    p_code.add_argument("--language", "-l")
    p_code.add_argument("--title", "-t")
    p_code.add_argument("--highlight", "-H", help="line numbers: 10,12-15")
    p_code.add_argument("--annotate", "-a", action="append", default=[], help="LINE:TEXT, repeatable")

    p_diff = sub.add_parser("diff", help="show a diff between two files")
    p_diff.add_argument("before_path", help="old version on disk")
    p_diff.add_argument("after_path", help="new version on disk")
    p_diff.add_argument("--title", "-t")
    p_diff.add_argument("--highlight", "-H")
    p_diff.add_argument("--annotate", "-a", action="append", default=[])

    p_tab = sub.add_parser("tab", help="switch active tab")
    p_tab.add_argument("name", choices=["code", "diff", "graph", "note", "video"])

    p_af = sub.add_parser("autofollow", help="force auto-follow on/off")
    p_af.add_argument("value", choices=["on", "off"])

    sub.add_parser("clear", help="wipe history")
    sub.add_parser("status", help="dump history as JSON")

    args = parser.parse_args(argv)

    if args.cmd == "code":
        result = client.code(
            args.path,
            language=args.language,
            title=args.title,
            highlights=_parse_highlights(args.highlight),
            annotations=_parse_annotations(args.annotate) if args.annotate else None,
        )
        print(f"entry {result['id']}: {result['title']}")
    elif args.cmd == "diff":
        from pathlib import Path as _P

        before = _P(args.before_path).expanduser().read_text(encoding="utf-8", errors="replace")
        after = _P(args.after_path).expanduser().read_text(encoding="utf-8", errors="replace")
        result = client.diff(
            path=args.after_path,
            before=before,
            after=after,
            title=args.title,
            highlights=_parse_highlights(args.highlight),
            annotations=_parse_annotations(args.annotate) if args.annotate else None,
        )
        print(f"entry {result['id']}: {result['title']}")
    elif args.cmd == "tab":
        client.tab(args.name)
        print(f"tab -> {args.name}")
    elif args.cmd == "autofollow":
        client.autofollow(args.value == "on")
        print(f"autofollow -> {args.value}")
    elif args.cmd == "clear":
        client.clear()
        print("cleared")
    elif args.cmd == "status":
        json.dump(client.status(), sys.stdout, indent=2, default=str)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
