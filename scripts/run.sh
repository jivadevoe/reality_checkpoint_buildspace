#!/bin/bash
# Start the Buildspace server.
#
#   BUILDSPACE_HOST  interface to bind (default 127.0.0.1 — loopback only).
#                    Set to 0.0.0.0 ONLY on a trusted network; the API has no
#                    authentication and can read any file the process can.
#   BUILDSPACE_PORT  TCP port (default 8097)
#   BUILDSPACE_DB    SQLite path (default ~/.buildspace/buildspace.db)
#   BUILDSPACE_ALLOWED_HOSTS  extra hostnames (comma-separated) the server may
#                    be addressed as, e.g. a reverse-proxy or tailnet name
#   BUILDSPACE_ROOTS directories (colon-separated) the server may read pushed
#                    files from. Unset, only the checkout directory is readable.
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${BUILDSPACE_HOST:-127.0.0.1}"
PORT="${BUILDSPACE_PORT:-8097}"
if [ -x ./venv/bin/uvicorn ]; then
  UVICORN=./venv/bin/uvicorn
else
  UVICORN=uvicorn
fi
exec "$UVICORN" buildspace.server:app --host "$HOST" --port "$PORT" --log-level warning
