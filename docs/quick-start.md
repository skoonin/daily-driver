# Quick start

Minimal checklist to scaffold a workspace, verify health, and make a first
entry. For the end-to-end daily flow (day-start, check-in, day-end,
jobs, summary), read [usage.md](usage.md) next.

## 1. Initialize

```bash
daily-driver init ~/daily-driver-workspace
cd ~/daily-driver-workspace
```

`init` is idempotent. Re-running on an existing workspace fills any missing
artifacts and exits 0 with a `Created: ... ; Skipped: ...` summary. Static
files (`context.md`, `voice-profile.md`, `.gitignore`) are only written if
missing — `-f, --force` will overwrite `.dd-config.yaml` (preserving the
previous contents as `.dd-config.yaml.bak`) but never clobbers your edits to
the static files.

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

The `.claude/*/daily-driver/` subdirs are package-managed. Top-level entries
under `.claude/commands/` and `.claude/agents/` are yours and never touched.
See [customization.md](customization.md).

## 2. Health check

```bash
daily-driver doctor
daily-driver doctor --fix      # regenerate if drifted; restore deleted managed files
daily-driver doctor --reset    # force full regenerate
```

`--fix` and `--reset` are mutually exclusive. ERROR rows exit non-zero;
WARNING rows exit 0. `--fix` restores managed files you may have accidentally
deleted under `.claude/*/daily-driver/`.

## 3. First tracker entry

```bash
daily-driver tracker add -c task -T "follow up with recruiter"
daily-driver tracker list
daily-driver status
```

`status` shows tracker totals, items stalled >14 days (non-terminal), and
7-day activity. `-j` / `--json` emits machine-readable output.

## 4. Scheduler (optional)

```bash
daily-driver scheduler install
```

Renders launchd plists into `~/Library/LaunchAgents/` for mid-day check-ins
and overnight job searches. Uninstall with `daily-driver scheduler uninstall`.
Show installed jobs with `daily-driver scheduler status`. macOS only.

## Next

- [usage.md](usage.md) — the full daily flow.
- [commands.md](commands.md) — every subcommand and its non-obvious flags.
- [configuration.md](configuration.md) — `.dd-config.yaml` reference.
