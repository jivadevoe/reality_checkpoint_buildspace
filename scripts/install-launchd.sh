#!/bin/bash
# Install Buildspace as a macOS launchd user agent so it starts at login
# and restarts if it dies. Generates the plist from this checkout's path.
#
#   scripts/install-launchd.sh            # install / reinstall
#   scripts/install-launchd.sh uninstall  # remove
#
# Environment variables set at install time are baked into the plist:
#   BUILDSPACE_HOST, BUILDSPACE_PORT, BUILDSPACE_DB, BUILDSPACE_ALLOWED_HOSTS,
#   BUILDSPACE_ROOTS
#   (see scripts/run.sh)
set -euo pipefail

LABEL="${BUILDSPACE_LAUNCHD_LABEL:-org.realitycheckpoint.buildspace}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
TARGET="gui/$(id -u)/$LABEL"

if [ "${1:-}" = "uninstall" ]; then
  launchctl bootout "$TARGET" 2>/dev/null || true
  rm -f "$PLIST"
  echo "removed $LABEL"
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents"
launchctl bootout "$TARGET" 2>/dev/null || true

# Extra PATH entries so the server can find ffprobe (video metadata).
EXTRA_PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$ROOT/scripts/run.sh</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>WorkingDirectory</key>
    <string>$ROOT</string>
    <key>StandardOutPath</key>
    <string>$ROOT/buildspace.log</string>
    <key>StandardErrorPath</key>
    <string>$ROOT/buildspace.err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$EXTRA_PATH</string>
        <key>BUILDSPACE_HOST</key>
        <string>${BUILDSPACE_HOST:-127.0.0.1}</string>
        <key>BUILDSPACE_PORT</key>
        <string>${BUILDSPACE_PORT:-8097}</string>
        <key>BUILDSPACE_DB</key>
        <string>${BUILDSPACE_DB:-$HOME/.buildspace/buildspace.db}</string>
        <key>BUILDSPACE_ALLOWED_HOSTS</key>
        <string>${BUILDSPACE_ALLOWED_HOSTS:-}</string>
        <key>BUILDSPACE_ROOTS</key>
        <string>${BUILDSPACE_ROOTS:-}</string>
    </dict>
</dict>
</plist>
PLIST

launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed $LABEL -> http://${BUILDSPACE_HOST:-127.0.0.1}:${BUILDSPACE_PORT:-8097}"
echo "restart after code changes:  launchctl kickstart -k $TARGET"
