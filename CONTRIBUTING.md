# Contributing to Buildspace

Thanks for taking a look. Buildspace is small on purpose: one FastAPI server,
one SQLite file, one vanilla-JS page. Please keep it that way.

## Ground rules

- No build step. The frontend is plain HTML, CSS and JS served as-is.
  Third-party libraries are loaded from a CDN and pinned to exact versions.
- No new dependencies without a reason that survives a short paragraph in
  the PR description.
- The HTTP API is what agents drive. Do not rename or remove endpoints or
  fields without a deprecation note in the README.
- Every content type (code, diff, graph, uml, note, video) must render on a
  phone. Test at 390px wide before opening a PR.

## Local development

```bash
python3 -m venv venv && source venv/bin/activate
pip install -e .
BUILDSPACE_PORT=8098 BUILDSPACE_DB=/tmp/bs-dev.db scripts/run.sh
```

Point a browser at http://127.0.0.1:8098 and push from another shell:

```bash
BUILDSPACE_URL=http://127.0.0.1:8098 buildspace code buildspace/server.py -H 1-10 -a "3:hello"
```

Use a separate port and database for development. `buildspace.clear()` and
`POST /api/clear` delete every entry in the database immediately; never run
tests against the instance you actually use.

## Security posture

Buildspace is a personal tool for a protected network. Do not open PRs that
try to make it safe for public exposure by adding logins or multi-user
features; that is out of scope. See SECURITY.md for what it does and does
not defend against, and for how to report a problem.
