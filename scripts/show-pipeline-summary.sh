#!/usr/bin/env bash
set -euo pipefail

# Reads {state_dir}/pipeline-summary.yaml and pretty-prints it for display
# inside day-start and check-in command steps. Outputs plain text, not YAML.
#
# Usage: bash scripts/show-pipeline-summary.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- prerequisites ------------------------------------------------------------

if ! command -v yq &>/dev/null; then
  echo "ERROR: yq not installed" >&2
  exit 1
fi

# --- resolve state_dir -------------------------------------------------------

if ! STATE_DIR=$(bash "${SCRIPT_DIR}/get-state-dir.sh" 2>&1); then
  echo "ERROR: show-pipeline-summary: could not resolve state_dir: ${STATE_DIR}" >&2
  exit 1
fi

SUMMARY="${STATE_DIR}/pipeline-summary.yaml"

if [[ ! -f "$SUMMARY" ]]; then
  echo "No pipeline summary yet at ${SUMMARY}."
  echo "  Run 'bash scripts/build-pipeline-summary.sh' to populate it."
  exit 0
fi

# --- helpers ------------------------------------------------------------------

yq_val() {
  local expr="$1"
  local val
  if ! val=$(yq "$expr" "$SUMMARY" 2>/dev/null); then
    echo ""
    return
  fi
  if [[ "$val" == "null" ]]; then
    echo ""
  else
    echo "$val"
  fi
}

# --- display ------------------------------------------------------------------

GENERATED=$(yq_val '.generated_at')
echo "=== Pipeline Summary (generated ${GENERATED:-unknown}) ==="
echo ""

# --- Tracker section ----------------------------------------------------------

echo "--- Tracker ---"

applied=$(yq_val   '.tracker.counts_by_status.applied // 0')
screening=$(yq_val '.tracker.counts_by_status.screening // 0')
interviewing=$(yq_val '.tracker.counts_by_status.interviewing // 0')
offer=$(yq_val     '.tracker.counts_by_status.offer // 0')
rejected=$(yq_val  '.tracker.counts_by_status.rejected // 0')
ghosted=$(yq_val   '.tracker.counts_by_status.ghosted // 0')
dropped=$(yq_val   '.tracker.counts_by_status.dropped // 0')
withdrawn=$(yq_val '.tracker.counts_by_status.withdrawn // 0')

active=$(( ${applied:-0} + ${screening:-0} + ${interviewing:-0} + ${offer:-0} ))

echo "  Active: ${active}  (applied=${applied:-0}  screening=${screening:-0}  interviewing=${interviewing:-0}  offer=${offer:-0})"
echo "  Closed: rejected=${rejected:-0}  ghosted=${ghosted:-0}  dropped=${dropped:-0}  withdrawn=${withdrawn:-0}"

# --- Follow-ups due -----------------------------------------------------------

fu_count=$(yq_val '.tracker.follow_ups_due.count // 0')
if [[ "${fu_count:-0}" -gt 0 ]]; then
  echo ""
  echo "--- Follow-ups Due (${fu_count}) ---"
  # Read items as newline-separated id|company|role entries
  local_idx=0
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    echo "  ${line}"
  done < <(yq '.tracker.follow_ups_due.items[] | .id + "  " + .company + "  |  " + .role + "  (due: " + .follow_up_date + ")"' "$SUMMARY" 2>/dev/null || true)
else
  echo "  Follow-ups: none due"
fi

# --- Stale apps ---------------------------------------------------------------

stale_count=$(yq_val '.tracker.stale_apps.count // 0')
if [[ "${stale_count:-0}" -gt 0 ]]; then
  echo ""
  echo "--- Stale Applications (${stale_count}) ---"
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    echo "  ${line}"
  done < <(yq '.tracker.stale_apps.items[] | .id + "  " + .company + "  |  " + .role + "  (" + (.days_since_activity | tostring) + " days)"' "$SUMMARY" 2>/dev/null || true)
fi

# --- Jobs CSV section ---------------------------------------------------------

echo ""
echo "--- Jobs Pipeline (jobs.csv) ---"

csv_found=$(yq_val   '.jobs_csv.counts.found // 0')
csv_pending=$(yq_val '.jobs_csv.counts.pending // 0')
csv_applied=$(yq_val '.jobs_csv.counts.applied // 0')
csv_skipped=$(yq_val '.jobs_csv.counts.skipped // 0')
csv_closed=$(yq_val  '.jobs_csv.counts.rejected_or_closed // 0')
csv_total=$(yq_val   '.jobs_csv.counts.total // 0')
csv_new=$(yq_val     '.jobs_csv.new_since_last_scrape // 0')
last_scrape=$(yq_val '.last_scrape')

echo "  Total: ${csv_total:-0}  |  found=${csv_found:-0}  pending=${csv_pending:-0}  applied=${csv_applied:-0}  skipped=${csv_skipped:-0}  closed=${csv_closed:-0}"
echo "  New since last scrape: ${csv_new:-0}"
echo "  Last scrape: ${last_scrape:-unknown}"

# --- Top prospects (Fit >= 8) -------------------------------------------------

prospect_count=0
if prospect_count=$(yq '.jobs_csv.top_prospects | length' "$SUMMARY" 2>/dev/null) && [[ "${prospect_count:-0}" -gt 0 ]]; then
  echo ""
  echo "--- Top Prospects (Fit >= 8, ${prospect_count} roles) ---"
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    echo "  ${line}"
  done < <(yq '.jobs_csv.top_prospects[] | "Fit " + (.fit | tostring) + "  " + .company + "  |  " + .role + "  (" + .status + ")"' "$SUMMARY" 2>/dev/null || true)
fi

echo ""
