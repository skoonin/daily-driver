---
name: focus
description: Toggle focus mode - suppress check-in notifications during deep work
---

Manage focus mode for uninterrupted work. Follow these steps in order:

## 1. Check Current Status

```bash
bash scripts/focus-mode.sh status
```

## 2. Toggle Focus Mode

If focus mode is **active**, show the time remaining and ask:
"Focus mode is active. Would you like to disable it early?"

If focus mode is **not active**, ask:
"How long do you want to focus? (default: read from config, typically 90 minutes)"

Optionally ask: "Working on a specific task? (leave blank to skip)"

## 3. Enable or Disable

If enabling, run with the user's chosen duration and optional ticket:
```bash
bash scripts/focus-mode.sh enable MINUTES TICKET
```

If disabling:
```bash
bash scripts/focus-mode.sh disable
```

## 4. Confirm Result

```bash
bash scripts/focus-mode.sh status
```

Show the result and remind the user that check-in notifications will be suppressed until focus mode ends or is disabled.
