---
name: setup
description: One-time setup - verify tool auth and configure workspace
---

Verify all integrations are working and configure the daily-driver workspace.

## 1. Check Tool Installation

Verify each tool is installed:
```bash
echo "=== Tool Check ===" && for cmd in icalBuddy jq yq terminal-notifier; do printf "%-12s %s\n" "$cmd:" "$(command -v $cmd 2>/dev/null || echo 'NOT FOUND (optional)' )"; done
```

## 2. Check Authentication

### Calendar (icalBuddy)
```bash
icalBuddy calendars 2>&1
```
Show available calendars. Ask user if any should be excluded.

### Calendar Sync Setup
```bash
bash scripts/check-calendar-sync.sh
```

## 3. Verify Sync Repos

Check that the sync target repos exist:
```bash
echo "=== Sync Repos ===" && while IFS= read -r repo; do repo="${repo/#\~/$HOME}"; printf "%-40s %s\n" "$repo:" "$([ -d "$repo/.git" ] && echo 'OK (git repo)' || echo 'NOT FOUND or not a git repo')"; done < <(yq '.sync_repos[]' config.yaml)
```

## 4. Verify Output Directory

```bash
bash scripts/check-output-dir.sh
```

If the output directory doesn't exist, create it:
```bash
bash scripts/init-output-dir.sh
```

## 5. Check Automation

### launchd Check-in Agent
```bash
launchctl list 2>/dev/null | grep -q "com.daily-driver.checkin" && echo "launchd agent: INSTALLED" || echo "launchd agent: NOT INSTALLED (run: make launchd-install)"
```

### Runtime State Directory
```bash
bash scripts/check-state-dir.sh
```

## 6. Review Context

Show the current context.md:
```bash
cat context.md
```

Ask the user if anything needs updating (timezone, target roles, job sources, calendar exclusions).

## 7. Test Data Gathering

Run a quick test of each gather script:
```bash
bash scripts/gather-calendar.sh 2>&1 | head -10
```
```bash
bash scripts/gather-applications.sh 2>&1 | head -10
```

Report which integrations are working and which need attention.

## 8. Initialize Tracker

Ensure the application tracker exists:
```bash
bash scripts/tracker.sh init
```
