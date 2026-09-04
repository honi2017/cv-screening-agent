#!/usr/bin/env bash
# Install (or refresh) the daily CV screening job.
#
# Usage: scripts/install-schedule.sh [HH] [MM]
# Default: 08:00 local. Re-running replaces the existing job.

set -euo pipefail

HOUR="${1:-8}"
MINUTE="${2:-0}"
LABEL="com.anduin.screen-cvs"
PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
CLAUDE_BIN="$(command -v claude || true)"

if [[ -z "$CLAUDE_BIN" ]]; then
  echo "error: 'claude' not found on PATH; install Claude Code first" >&2
  exit 1
fi

mkdir -p "$PROJECT/state/logs" "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>WorkingDirectory</key><string>$PROJECT</string>
  <key>ProgramArguments</key>
  <array>
    <string>$CLAUDE_BIN</string>
    <string>-p</string>
    <string>/screen-cvs</string>
    <string>--permission-mode</string>
    <string>acceptEdits</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>$HOUR</integer>
    <key>Minute</key><integer>$MINUTE</integer>
  </dict>
  <key>StandardOutPath</key><string>$PROJECT/state/logs/screen-cvs.out.log</string>
  <key>StandardErrorPath</key><string>$PROJECT/state/logs/screen-cvs.err.log</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
PLIST_EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

printf 'Installed %s — runs daily at %02d:%02d\n' "$LABEL" "$HOUR" "$MINUTE"
echo "Logs:      $PROJECT/state/logs/"
echo "Run now:   launchctl start $LABEL"
echo "Remove:    launchctl unload $PLIST && rm $PLIST"
echo
echo "Note: .env must contain TRAKSTAR_API_KEY, and the job reads it via the skill."
