#!/usr/bin/env bash
set -euo pipefail

# Open an iTerm2 (or Terminal) window running a daily-driver slash command.
# Called by launchd plists with the command as the first argument.
#
# Usage: open-session.sh <day-start|check-in|day-end>

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="$HOME/.local/share/daily-driver"
LOCK_FILE="${STATE_DIR}/focus.lock"

COMMAND="${1:-check-in}"

# Ensure PATH includes Homebrew and local bins for launchd context
for dir in /opt/homebrew/bin /usr/local/bin "$HOME/.local/bin"; do
  [[ -d "$dir" ]] && export PATH="${dir}:${PATH}"
done

open_session() {
  local repo_dir session_name slash_cmd wid_file existing_wid new_wid
  repo_dir="$(cd "${SCRIPT_DIR}/.." && pwd)"
  session_name="${COMMAND}-$(date +%Y-%m-%d-%H%M)"
  slash_cmd="/${COMMAND}"
  # Reuse an existing iTerm window (opens new tab) to avoid window-spam across launchd triggers.
  # See memory: feedback_notifications.md -- windows open in background, additional sessions become tabs.
  wid_file="/tmp/iterm-daily-driver-wid"
  existing_wid=""

  if [[ -f "$wid_file" ]]; then
    if ! existing_wid=$(cat "$wid_file" 2>&1); then
      echo "WARNING: open-session: could not read ${wid_file}: ${existing_wid}" >&2
      existing_wid=""
    fi
    if [[ ! "$existing_wid" =~ ^[0-9]+$ ]]; then
      existing_wid=""
    fi
  fi

  if [ -d /Applications/iTerm.app ]; then
    # No `activate` -- iTerm should not steal focus from the foreground app.
    if ! new_wid=$(osascript 2>&1 <<EOF
tell application "iTerm"
  set targetWindow to missing value
  set storedIdText to "${existing_wid}"
  if storedIdText is not "" then
    try
      set storedId to storedIdText as integer
      repeat with w in windows
        if id of w is storedId then
          set targetWindow to w
          exit repeat
        end if
      end repeat
    end try
  end if
  set targetTab to missing value
  if targetWindow is missing value then
    set targetWindow to (create window with default profile)
    set targetTab to current tab of targetWindow
  else
    tell targetWindow
      set targetTab to (create tab with default profile)
    end tell
  end if
  tell current session of targetTab
    write text "echo \$\$ > '${repo_dir}/.session.lock' && cd '${repo_dir}' && CLAUDE_CODE_SUBAGENT_MODEL=sonnet claude --model sonnet --effort medium --agent work-planner -n '${session_name}' '${slash_cmd}'; rm -f '${repo_dir}/.session.lock'"
  end tell
  return (id of targetWindow) as text
end tell
EOF
    ); then
      echo "ERROR: open-session: iTerm AppleScript failed: ${new_wid}" >&2
      return 1
    fi
    if [[ -n "$new_wid" && "$new_wid" =~ ^[0-9]+$ ]]; then
      if ! printf '%s\n' "$new_wid" > "$wid_file" 2>&1; then
        echo "WARNING: open-session: could not persist window ID to ${wid_file}" >&2
      fi
    fi
  else
    if ! osascript 2>&1 <<EOF
tell application "Terminal"
  do script "echo \$\$ > '${repo_dir}/.session.lock' && cd '${repo_dir}' && CLAUDE_CODE_SUBAGENT_MODEL=sonnet claude --model sonnet --effort medium --agent work-planner -n '${session_name}' '${slash_cmd}'; rm -f '${repo_dir}/.session.lock'"
end tell
EOF
    then
      echo "ERROR: open-session: Terminal AppleScript failed" >&2
      return 1
    fi
  fi
  return 0
}

# Gate 1: Weekends
dow=$(date +%u)
if [[ "$dow" -ge 6 ]]; then
  exit 0
fi

# Gate 2: Focus lock (check-in only — day-start and day-end always fire)
if [[ "$COMMAND" == "check-in" && -f "$LOCK_FILE" ]]; then
  if ! command -v jq &>/dev/null; then
    exit 0
  fi
  end_epoch=$(jq -r '.end_epoch // 0' "$LOCK_FILE" 2>/dev/null) || end_epoch=0
  if [[ ! "$end_epoch" =~ ^[0-9]+$ ]]; then
    end_epoch=0
  fi
  if ! now_epoch=$(date +%s 2>&1); then
    echo "WARNING: open-session: could not get current epoch: ${now_epoch}" >&2
    now_epoch=0
  fi
  if [[ "$now_epoch" -lt "$end_epoch" ]]; then
    exit 0
  fi
  rm -f "$LOCK_FILE"
fi

# Gate 3: Session lock — skip if another claude session is already running
# Lock content may be one of:
#   pending:<epoch>  — a just-launched session whose terminal has not yet written its PID
#   <pid>            — a running session's shell PID
SESSION_LOCK="${SCRIPT_DIR}/../.session.lock"
if [[ -f "$SESSION_LOCK" ]]; then
  # Fail-safe: if we cannot read the lock, skip this trigger rather than
  # treating empty content as "corrupt" and deleting the lock, which would
  # bypass duplicate-session protection on a transient permission error.
  if ! locked_content=$(cat "$SESSION_LOCK" 2>&1); then
    mkdir -p "$STATE_DIR"
    echo "$(date): open-session: could not read ${SESSION_LOCK}: ${locked_content} -- skipping ${COMMAND}" >> "${STATE_DIR}/open-session.log"
    exit 0
  fi
  if [[ "$locked_content" =~ ^pending:([0-9]+)$ ]]; then
    lock_epoch="${BASH_REMATCH[1]}"
    # If we cannot get the current epoch, treat the lock as stale rather than
    # comparing against 0 (which makes `lock_now - lock_epoch` a large negative
    # and would hold the lock indefinitely).
    if ! lock_now=$(date +%s 2>/dev/null); then
      echo "$(date): open-session: date +%s failed while checking pending lock — clearing" >> "${STATE_DIR}/open-session.log"
      rm -f "$SESSION_LOCK"
    elif (( lock_now - lock_epoch < 60 )); then
      # Hold pending locks for up to 60s to let the spawned terminal replace with its own PID
      echo "$(date): skipped ${COMMAND} — pending session launch (age $((lock_now - lock_epoch))s)" >> "${STATE_DIR}/open-session.log"
      exit 0
    else
      echo "$(date): clearing stale pending lock (age $((lock_now - lock_epoch))s)" >> "${STATE_DIR}/open-session.log"
      rm -f "$SESSION_LOCK"
    fi
  elif [[ "$locked_content" =~ ^[0-9]+$ ]]; then
    if kill -0 "$locked_content" 2>/dev/null; then
      echo "$(date): skipped ${COMMAND} — session already running (PID ${locked_content})" >> "${STATE_DIR}/open-session.log"
      exit 0
    fi
    rm -f "$SESSION_LOCK"
  else
    echo "$(date): stale/corrupt lock at ${SESSION_LOCK} (content: '${locked_content}') — clearing" >> "${STATE_DIR}/open-session.log"
    rm -f "$SESSION_LOCK"
  fi
fi

mkdir -p "$STATE_DIR"
# Preliminary lock: `pending:<epoch>` sentinel. The spawned terminal replaces this
# with its own shell PID once it starts running. Gate 3 holds the pending lock for
# up to 60s so a concurrent launchd trigger cannot spawn a duplicate session while
# the new terminal is still launching.
if ! PENDING_EPOCH=$(date +%s 2>/dev/null); then
  # If date fails here, skip locking entirely rather than writing pending:0 which
  # later comparisons would treat as indefinitely held. Log and continue without
  # a lock (the worst case is a duplicate session, which is recoverable).
  echo "$(date): open-session: date +%s failed writing preliminary lock — proceeding unlocked" >> "${STATE_DIR}/open-session.log"
else
  echo "pending:${PENDING_EPOCH}" > "$SESSION_LOCK"
fi
if ! open_session; then
  echo "$(date): open_session failed for ${COMMAND} -- clearing pending session lock" >> "${STATE_DIR}/open-session.log"
  rm -f "$SESSION_LOCK"
  exit 1
fi
