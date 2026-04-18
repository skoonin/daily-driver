#!/usr/bin/env bash
set -euo pipefail

# Writes an interview-state YAML block to {state_dir}/interview-state/{app-id}.yaml.
# Reads the YAML from stdin, validates it with yq, extracts app_id, and writes
# atomically via a .tmp file. Fails loudly if the YAML is unparseable or app_id
# is missing -- bad state is worse than no state.
#
# Usage: printf '%s\n' "$yaml_block" | bash scripts/record-interview-state.sh
#        bash scripts/record-interview-state.sh <<'EOF'
#          app_id: acme-sre-2024-01
#          ...
#        EOF

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for cmd in yq; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "ERROR: $(basename "$0"): yq is required but not installed. Run: brew install yq" >&2
    exit 1
  fi
done

if ! STATE_DIR=$(bash "${SCRIPT_DIR}/get-state-dir.sh" 2>&1); then
  echo "ERROR: $(basename "$0"): could not resolve state_dir: ${STATE_DIR}" >&2
  exit 1
fi

# Read full YAML block from stdin into a variable so we can validate before writing.
YAML_INPUT=$(cat)

if [[ -z "$YAML_INPUT" ]]; then
  echo "ERROR: $(basename "$0"): no YAML input received on stdin" >&2
  exit 1
fi

# Validate that the input parses as YAML before touching the filesystem.
if ! printf '%s\n' "$YAML_INPUT" | yq '.' > /dev/null 2>&1; then
  echo "ERROR: $(basename "$0"): YAML input failed to parse -- aborting write" >&2
  echo "--- received input ---" >&2
  printf '%s\n' "$YAML_INPUT" >&2
  echo "---------------------" >&2
  exit 1
fi

# Extract app_id; bail if absent so the file can't be written to a nameless path.
APP_ID=$(printf '%s\n' "$YAML_INPUT" | yq '.app_id // ""' 2>/dev/null)
if [[ -z "$APP_ID" || "$APP_ID" == "null" ]]; then
  echo "ERROR: $(basename "$0"): YAML must contain a non-empty 'app_id' field" >&2
  exit 1
fi

INTERVIEW_STATE_DIR="${STATE_DIR}/interview-state"
mkdir -p "$INTERVIEW_STATE_DIR"

TARGET="${INTERVIEW_STATE_DIR}/${APP_ID}.yaml"
TMP_TARGET="${TARGET}.tmp"

if ! printf '%s\n' "$YAML_INPUT" > "$TMP_TARGET"; then
  echo "ERROR: $(basename "$0"): failed to write tmp file at ${TMP_TARGET}" >&2
  exit 1
fi

if ! mv "$TMP_TARGET" "$TARGET"; then
  echo "ERROR: $(basename "$0"): failed to rename tmp to ${TARGET}" >&2
  rm -f "$TMP_TARGET"
  exit 1
fi

echo "Interview state saved: ${TARGET}"
