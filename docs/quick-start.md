# Quick start

Scaffold a workspace, verify health, make the first entry.

## 1. Initialize

```bash
daily-driver init ~/daily-driver-workspace
cd ~/daily-driver-workspace
```

`init` creates:

```
~/daily-driver-workspace/
├── .dd-config.yaml            # workspace config (you edit this)
├── .daily-driver/             # version stamp, SHA-256 manifest, state/ for locks + logs
├── .claude/
│   ├── settings.local.json    # rendered from packaged template
│   ├── commands/daily-driver/ # managed — regenerated on version drift
│   └── agents/daily-driver/   # managed — regenerated on version drift
├── context.md                 # your writing context (copied once; yours thereafter)
└── voice-profile.md           # your writing-style profile (ditto)
```

The `.claude/*/daily-driver/` subdirs are package-managed. Top-level entries under `.claude/commands/` and `.claude/agents/` are yours and never touched. See [customization.md](customization.md).

## 2. Health check

```bash
daily-driver doctor
daily-driver doctor --fix      # regenerate if drifted
daily-driver doctor --reset    # force full regenerate
```

`--fix` and `--reset` are mutually exclusive. ERROR rows exit non-zero; WARNING rows exit 0.

## 3. First tracker entry

```bash
daily-driver tracker add --title "follow up with recruiter" --category task
daily-driver tracker list
daily-driver status
```

`status` shows tracker totals, items stalled >14 days (non-terminal), and 7-day activity. `--json` emits machine-readable output.

## 4. Scheduler (optional)

```bash
daily-driver install-scheduler
```

Renders launchd plists into `~/Library/LaunchAgents/` for mid-day check-ins and overnight job scraping. Uninstall with `daily-driver uninstall-scheduler` (`--keep-state` retains mirrored copies under `.daily-driver/state/launchd/`). macOS only.
