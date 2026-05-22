#!/usr/bin/env bash
# install-agent-daemon.sh — install the always-on KVK email agent on macOS
#
# Run once. After this:
#   - the agent starts immediately
#   - it relaunches automatically on every login
#   - if it crashes / hits a hopeless run, launchd restarts it after 60s
#   - logs live at ~/Library/Logs/schild-kvk-agent.log
#
# To uninstall later:
#   launchctl unload ~/Library/LaunchAgents/com.schildinc.kvk-agent.plist
#   rm ~/Library/LaunchAgents/com.schildinc.kvk-agent.plist
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "$0")/.." &> /dev/null && pwd)"
SRC_PLIST="$PROJECT_DIR/scripts/com.schildinc.kvk-agent.plist"
LAUNCHAGENTS_DIR="$HOME/Library/LaunchAgents"
TARGET_PLIST="$LAUNCHAGENTS_DIR/com.schildinc.kvk-agent.plist"
LOG_DIR="$HOME/Library/Logs"

if [ ! -f "$SRC_PLIST" ]; then
  echo "✗ Cannot find $SRC_PLIST — run from inside the project."
  exit 1
fi

# Quick sanity: venv + agent script exist
if [ ! -x "$PROJECT_DIR/.venv/bin/python" ]; then
  echo "✗ Python venv missing at $PROJECT_DIR/.venv/bin/python"
  echo "  Run:   python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi
if [ ! -f "$PROJECT_DIR/scripts/email_agent.py" ]; then
  echo "✗ email_agent.py missing at $PROJECT_DIR/scripts/email_agent.py"
  exit 1
fi

mkdir -p "$LAUNCHAGENTS_DIR" "$LOG_DIR"

# If already loaded, unload first so we pick up the new plist cleanly
if launchctl list 2>/dev/null | grep -q "com.schildinc.kvk-agent"; then
  echo "→ Unloading existing agent…"
  launchctl unload "$TARGET_PLIST" 2>/dev/null || true
fi

cp "$SRC_PLIST" "$TARGET_PLIST"
chmod 644 "$TARGET_PLIST"

echo "→ Loading new agent…"
launchctl load -w "$TARGET_PLIST"

echo
echo "✓ Agent installed and running."
echo
echo "  Status:   launchctl list | grep schildinc"
echo "  Logs:     tail -f $LOG_DIR/schild-kvk-agent.log"
echo "  Stop:     launchctl unload $TARGET_PLIST"
echo "  Restart:  launchctl unload $TARGET_PLIST && launchctl load $TARGET_PLIST"
