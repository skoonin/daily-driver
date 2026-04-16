#!/opt/homebrew/bin/bash
set -euo pipefail

# Open an iTerm2 (or Terminal) window running a daily-driver slash command.
# Called by launchd plists with the command as the first argument.
#
# Usage: open-session.sh <day-start|check-in|day-end>

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! STATE_DIR=$(bash "${SCRIPT_DIR}/get-state-dir.sh" 2>&1); then
  echo "ERROR: $(basename "$0"): could not resolve state_dir: ${STATE_DIR}" >&2
  exit 1
fi
mkdir -p "$STATE_DIR"

log_msg() {
  local msg
  msg="$(date): $1"
  echo "$msg" >&2
  echo "$msg" >> "${STATE_DIR}/open-session.log"
}

LOCK_FILE="${STATE_DIR}/focus.lock"

COMMAND="${1:-check-in}"

# Ensure PATH includes Homebrew and local bins for launchd context
for dir in /opt/homebrew/bin /usr/local/bin "$HOME/.local/bin"; do
  [[ -d "$dir" ]] && export PATH="${dir}:${PATH}"
done

# Read Claude session defaults from config
CONFIG="${SCRIPT_DIR}/../config.yaml"
CLAUDE_MODEL="sonnet"
CLAUDE_EFFORT="medium"
CLAUDE_SUBAGENT="sonnet"
if command -v yq &>/dev/null && [[ -f "$CONFIG" ]]; then
  CLAUDE_MODEL=$(yq '.claude.model // "sonnet"' "$CONFIG" 2>/dev/null)
  CLAUDE_EFFORT=$(yq '.claude.effort // "medium"' "$CONFIG" 2>/dev/null)
  CLAUDE_SUBAGENT=$(yq '.claude.subagent_model // "sonnet"' "$CONFIG" 2>/dev/null)
fi

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
      log_msg "WARNING: could not read ${wid_file}: ${existing_wid}"
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
    write text "echo \$\$ > '${repo_dir}/.session.lock' && cd '${repo_dir}' && CLAUDE_CODE_SUBAGENT_MODEL=${CLAUDE_SUBAGENT} claude --model ${CLAUDE_MODEL} --effort ${CLAUDE_EFFORT} --agent work-planner -n '${session_name}' '${slash_cmd}'; rm -f '${repo_dir}/.session.lock'"
  end tell
  return (id of targetWindow) as text
end tell
EOF
    ); then
      log_msg "ERROR: iTerm AppleScript failed: ${new_wid}"
      return 1
    fi
    if [[ -n "$new_wid" && "$new_wid" =~ ^[0-9]+$ ]]; then
      if ! printf '%s\n' "$new_wid" > "$wid_file" 2>&1; then
        log_msg "WARNING: could not persist window ID to ${wid_file}"
      fi
    fi
  else
    if ! osascript 2>&1 <<EOF
tell application "Terminal"
  do script "echo \$\$ > '${repo_dir}/.session.lock' && cd '${repo_dir}' && CLAUDE_CODE_SUBAGENT_MODEL=${CLAUDE_SUBAGENT} claude --model ${CLAUDE_MODEL} --effort ${CLAUDE_EFFORT} --agent work-planner -n '${session_name}' '${slash_cmd}'; rm -f '${repo_dir}/.session.lock'"
end tell
EOF
    then
      log_msg "ERROR: Terminal AppleScript failed"
      return 1
    fi
  fi
  return 0
}

# Gate 1: Weekends
dow=$(date +%u)
if [[ "$dow" -ge 6 ]]; then
  log_msg "skipped ${COMMAND} — weekend (dow=${dow})"
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
    log_msg "skipped ${COMMAND} — focus lock active (ends $(date -r "${end_epoch}" +%H:%M 2>/dev/null || echo "epoch:${end_epoch}"))"
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
    log_msg "open-session: could not read ${SESSION_LOCK}: ${locked_content} -- skipping ${COMMAND}"
    exit 0
  fi
  if [[ "$locked_content" =~ ^pending:([0-9]+)$ ]]; then
    lock_epoch="${BASH_REMATCH[1]}"
    # If we cannot get the current epoch, treat the lock as stale rather than
    # comparing against 0 (which makes `lock_now - lock_epoch` a large negative
    # and would hold the lock indefinitely).
    if ! lock_now=$(date +%s 2>/dev/null); then
      log_msg "open-session: date +%s failed while checking pending lock — clearing"
      rm -f "$SESSION_LOCK"
    elif (( lock_now - lock_epoch < 60 )); then
      # Hold pending locks for up to 60s to let the spawned terminal replace with its own PID
      log_msg "skipped ${COMMAND} — pending session launch (age $((lock_now - lock_epoch))s)"
      exit 0
    else
      log_msg "clearing stale pending lock (age $((lock_now - lock_epoch))s)"
      rm -f "$SESSION_LOCK"
    fi
  elif [[ "$locked_content" =~ ^[0-9]+$ ]]; then
    if kill -0 "$locked_content" 2>/dev/null; then
      log_msg "skipped ${COMMAND} — session already running (PID ${locked_content})"
      exit 0
    fi
    rm -f "$SESSION_LOCK"
  else
    log_msg "stale/corrupt lock at ${SESSION_LOCK} (content: '${locked_content}') — clearing"
    rm -f "$SESSION_LOCK"
  fi
fi

# Preliminary lock: `pending:<epoch>` sentinel. The spawned terminal replaces this
# with its own shell PID once it starts running. Gate 3 holds the pending lock for
# up to 60s so a concurrent launchd trigger cannot spawn a duplicate session while
# the new terminal is still launching.
if ! PENDING_EPOCH=$(date +%s 2>/dev/null); then
  # If date fails here, skip locking entirely rather than writing pending:0 which
  # later comparisons would treat as indefinitely held. Log and continue without
  # a lock (the worst case is a duplicate session, which is recoverable).
  log_msg "open-session: date +%s failed writing preliminary lock — proceeding unlocked"
else
  echo "pending:${PENDING_EPOCH}" > "$SESSION_LOCK"
fi
if ! open_session; then
  log_msg "open_session failed for ${COMMAND} -- clearing pending session lock"
  rm -f "$SESSION_LOCK"
  exit 1
fi
