#!/usr/bin/env bash
set -euo pipefail

# Appends a ruled-out job entry to {state_dir}/ruled-out.yaml.
#
# Accepts a RULED_OUT: {json} line either from stdin or via --line flag.
# Skips silently if the job_key is already present (idempotent).
#
# Protocol: lines starting with "RULED_OUT:" are emitted by Claude during
# /day-start and /check-in when it decides to drop a candidate.
#
# Usage:
#   echo 'RULED_OUT: {"job_key":"...", "reason":"...", "source":"day-start"}' \
#     | bash scripts/record-ruled-out.sh
#   bash scripts/record-ruled-out.sh --line 'RULED_OUT: {...}'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v yq &>/dev/null; then
  echo "ERROR: yq not installed" >&2
  exit 1
fi
if ! command -v jq &>/dev/null; then
  echo "ERROR: jq not installed" >&2
  exit 1
fi

if ! STATE_DIR=$(bash "${SCRIPT_DIR}/get-state-dir.sh" 2>&1); then
  echo "ERROR: record-ruled-out: could not resolve state_dir: ${STATE_DIR}" >&2
  exit 1
fi

RULED_OUT_YAML="${STATE_DIR}/ruled-out.yaml"

# --- parse arguments ----------------------------------------------------------

INPUT_LINE=""

if [[ $# -ge 1 && "$1" == "--line" ]]; then
  if [[ $# -lt 2 || -z "$2" ]]; then
    echo "ERROR: --line requires a non-empty argument" >&2
    exit 1
  fi
  INPUT_LINE="$2"
else
  # Read from stdin; accept only the first RULED_OUT: line
  while IFS= read -r line; do
    if [[ "$line" == RULED_OUT:* ]]; then
      INPUT_LINE="$line"
      break
    fi
  done
fi

if [[ -z "$INPUT_LINE" ]]; then
  # Nothing to record -- not an error, caller may pipe mixed output
  exit 0
fi

# --- extract and validate JSON payload ----------------------------------------

RAW_JSON="${INPUT_LINE#RULED_OUT:}"
RAW_JSON="${RAW_JSON# }"  # strip one leading space

if ! PAYLOAD=$(echo "$RAW_JSON" | jq -e '.' 2>/dev/null); then
  echo "ERROR: record-ruled-out: invalid JSON payload: ${RAW_JSON}" >&2
  exit 1
fi

JOB_KEY=$(echo "$PAYLOAD" | jq -r '.job_key // empty')
REASON=$(echo "$PAYLOAD"  | jq -r '.reason  // empty')
SOURCE=$(echo "$PAYLOAD"  | jq -r '.source  // "unknown"')

if [[ -z "$JOB_KEY" ]]; then
  echo "ERROR: record-ruled-out: missing job_key in payload: ${RAW_JSON}" >&2
  exit 1
fi

# --- dedup check --------------------------------------------------------------

mkdir -p "$STATE_DIR"

if [[ -f "$RULED_OUT_YAML" ]]; then
  # mikefarah/yq v4: no jq-style // empty; .ruled_out[].job_key emits one line per entry
  EXISTING=$(yq -r '.ruled_out[].job_key' "$RULED_OUT_YAML" 2>/dev/null || true)
  if [[ -n "$EXISTING" ]] && echo "$EXISTING" | grep -qxF "$JOB_KEY"; then
    echo "already ruled out: ${JOB_KEY}" >&2
    exit 0
  fi
fi

# --- append entry -------------------------------------------------------------

RECORDED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Initialise file with list header on first write
if [[ ! -f "$RULED_OUT_YAML" ]]; then
  printf 'ruled_out: []\n' > "$RULED_OUT_YAML"
fi

# Safe single-field values: escape for yq expression injection via env vars
JOB_KEY_ESC="$JOB_KEY" REASON_ESC="$REASON" SOURCE_ESC="$SOURCE" RECORDED_AT_ESC="$RECORDED_AT" \
  yq -i '.ruled_out += [{"job_key": env(JOB_KEY_ESC), "reason": env(REASON_ESC), "source": env(SOURCE_ESC), "recorded_at": env(RECORDED_AT_ESC)}]' \
  "$RULED_OUT_YAML"

echo "Ruled out recorded: ${JOB_KEY}"
