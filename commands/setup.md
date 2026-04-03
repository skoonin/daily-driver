---
name: setup
description: One-time setup - verify tool auth and configure workspace
---

Verify all integrations are working and configure the daily-driver workspace.

## 1. Check Tool Installation

Verify each tool is installed:
```bash
echo "=== Tool Check ===" && for cmd in acli gh icalBuddy jq yq terminal-notifier; do printf "%-12s %s\n" "$cmd:" "$(command -v $cmd 2>/dev/null || echo 'NOT FOUND (optional)' )"; done
```

## 2. Check Authentication

### Jira (acli)
```bash
acli jira auth status 2>&1
```
If not authenticated, tell the user to run `! acli jira auth login --site <site>` for each site listed in `config.yaml`.

### GitHub (gh)
```bash
gh auth status 2>&1
```
If not authenticated, tell the user to run `! gh auth login`.

### 1Password SSH Agent
```bash
echo "=== 1Password SSH Agent ===" && if [[ "$SSH_AUTH_SOCK" == *".1password/agent.sock" ]]; then printf "%-12s %s\n" "socket:" "OK ($SSH_AUTH_SOCK)" && ssh-add -l 2>/dev/null | while read -r line; do printf "%-12s %s\n" "key:" "$line"; done; else echo "WARNING: SSH_AUTH_SOCK not pointing to 1Password agent ($SSH_AUTH_SOCK)"; fi
```
If the socket is not found, tell the user to enable the 1Password SSH agent in 1Password Settings > Developer > SSH Agent.

### Calendar (icalBuddy)
```bash
icalBuddy calendars 2>&1
```
Show available calendars. Ask user if any should be excluded.

### Calendar Sync Setup
```bash
CALENDAR_NAME=$(yq '.calendar.plan_calendar_name // "Daily Plan"' config.yaml); bash scripts/calendar-check.sh "$CALENDAR_NAME"
```

## 3. Verify Sync Repos

Check that the sync target repos exist:
```bash
echo "=== Sync Repos ===" && while IFS= read -r repo; do repo="${repo/#\~/$HOME}"; printf "%-40s %s\n" "$repo:" "$([ -d "$repo/.git" ] && echo 'OK (git repo)' || echo 'NOT FOUND or not a git repo')"; done < <(yq '.sync_repos[]' config.yaml)
```

## 4. Verify Output Directory

```bash
OUTPUT_DIR=$(yq '.output_dir' config.yaml); OUTPUT_DIR="${OUTPUT_DIR/#\~/$HOME}"; ls -la "$OUTPUT_DIR/" 2>/dev/null || echo "output dir not found: $OUTPUT_DIR"
```

If the output directory doesn't exist, create it:
```bash
OUTPUT_DIR=$(yq '.output_dir' config.yaml); OUTPUT_DIR="${OUTPUT_DIR/#\~/$HOME}"; mkdir -p "$OUTPUT_DIR" && git -C "$OUTPUT_DIR" init 2>/dev/null
```

## 5. Check Automation

### launchd Check-in Agent
```bash
launchctl list 2>/dev/null | grep -q "com.daily-driver.checkin" && echo "launchd agent: INSTALLED" || echo "launchd agent: NOT INSTALLED (run: make launchd-install)"
```

### Runtime State Directory
```bash
STATE_DIR="$HOME/.local/share/daily-driver"; [ -d "$STATE_DIR" ] && echo "State dir: OK ($STATE_DIR)" || echo "State dir: will be created on first /check-in"
```

## 6. Review Context

Show the current context.md:
```bash
cat context.md
```

Ask the user if anything needs updating (timezone, work hours, Jira projects, GitHub orgs, calendar exclusions).

## 7. Test Data Gathering

Run a quick test of each gather script:
```bash
bash scripts/gather-calendar.sh 2>&1 | head -10
```
```bash
bash scripts/gather-jira.sh 2>&1 | head -10
```
```bash
bash scripts/gather-prs.sh 2>&1 | head -10
```
```bash
bash scripts/gather-rfcs.sh 2>&1 | head -10
```

Report which integrations are working and which need attention.
