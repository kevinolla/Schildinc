#!/usr/bin/env bash
# install-agent-daemon.sh — install the always-on Schild Inc local agents (macOS)
#
# Installs BOTH:
#   • com.schildinc.kvk-agent    — email/contact finder (always-on)
#   • com.schildinc.owner-agent  — owner-name enrichment (runs at login + every 6h)
#
# Run once. After this:
#   - both agents start immediately and relaunch on every login
#   - logs live at ~/Library/Logs/schild-kvk-agent.log
#                  ~/Library/Logs/schild-owner-agent.log
#
# To uninstall later:
#   launchctl unload ~/Library/LaunchAgents/com.schildinc.kvk-agent.plist
#   launchctl unload ~/Library/LaunchAgents/com.schildinc.owner-agent.plist
#   rm ~/Library/LaunchAgents/com.schildinc.kvk-agent.plist
#   rm ~/Library/LaunchAgents/com.schildinc.owner-agent.plist
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "$0")/.." &> /dev/null && pwd)"
LAUNCHAGENTS_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs"

# Quick sanity: venv exists
if [ ! -x "$PROJECT_DIR/.venv/bin/python" ]; then
  echo "✗ Python venv missing at $PROJECT_DIR/.venv/bin/python"
  echo "  Run:   python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  echo "         python -m playwright install chromium"
  exit 1
fi

mkdir -p "$LAUNCHAGENTS_DIR" "$LOG_DIR"

install_one() {
  local label="$1" script="$2"
  local src="$PROJECT_DIR/scripts/$label.plist"
  local target="$LAUNCHAGENTS_DIR/$label.plist"

  if [ ! -f "$src" ]; then
    echo "✗ Cannot find $src — run from inside the project."; exit 1
  fi
  if [ ! -f "$PROJECT_DIR/scripts/$script" ]; then
    echo "✗ $script missing at $PROJECT_DIR/scripts/$script"; exit 1
  fi

  # If already loaded, unload first so we pick up the new plist cleanly
  if launchctl list 2>/dev/null | grep -q "$label"; then
    echo "→ Unloading existing $label…"
    launchctl unload "$target" 2>/dev/null || true
  fi

  cp "$src" "$target"
  chmod 644 "$target"
  echo "→ Loading $label…"
  launchctl load -w "$target"
}

install_one "com.schildinc.kvk-agent"   "email_agent.py"
install_one "com.schildinc.owner-agent" "owner_agent.py"

echo
echo "✓ Both agents installed and running."
echo
echo "  Status:    launchctl list | grep schildinc"
echo "  Email log: tail -f $LOG_DIR/schild-kvk-agent.log"
echo "  Owner log: tail -f $LOG_DIR/schild-owner-agent.log"
echo "  Stop all:  launchctl unload $LAUNCHAGENTS_DIR/com.schildinc.kvk-agent.plist \\"
echo "                              $LAUNCHAGENTS_DIR/com.schildinc.owner-agent.plist"
