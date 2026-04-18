#!/usr/bin/env bash
set -euo pipefail

# Pretty-prints the ruled-out job list from {state_dir}/ruled-out.yaml.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! STATE_DIR=$(bash "${SCRIPT_DIR}/get-state-dir.sh" 2>&1); then
  echo "ERROR: list-ruled-out: could not resolve state_dir: ${STATE_DIR}" >&2
  exit 1
fi

RULED_OUT_YAML="${STATE_DIR}/ruled-out.yaml"

if [[ ! -f "$RULED_OUT_YAML" ]]; then
  echo "No ruled-out entries yet."
  exit 0
fi

COUNT=$(yq '.ruled_out | length' "$RULED_OUT_YAML" 2>/dev/null || echo 0)
echo "=== Ruled-Out Jobs (${COUNT}) ==="

yq -r '.ruled_out[] | "\(.recorded_at)  \(.job_key)  [\(.source)]  \(.reason)"' \
  "$RULED_OUT_YAML" 2>/dev/null || echo "(none)"
