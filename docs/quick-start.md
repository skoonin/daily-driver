# Quick start

Minimal checklist to scaffold a workspace, verify health, and make a first entry. For the end-to-end daily flow (day-start, check-in, day-end, summary, and optional job search), read [usage.md](usage.md) next.

## 1. Initialize

```bash
daily-driver init ~/daily-driver-workspace
cd ~/daily-driver-workspace
```

Job search is off by default. To use it on a new workspace, add `--job-search`: `daily-driver init --job-search ~/daily-driver-workspace`. That adds a `plugins.job_search` block to `.dd-config.yaml` (only `enabled: true` is on; the rest are commented examples) and the job sections to the Claude prompts. Without `--force`, `--job-search` on an existing workspace changes nothing, and `init` prints what to add; see [Jobs](usage.md#jobs).

`init` is idempotent. Re-running fills any missing artifacts and exits 0 with a `Created: ... ; Skipped: ...` summary. Static files (`context.md`, `voice-profile.md`, `.gitignore`) are written only if missing. `-f` / `--force` replaces `.dd-config.yaml` with a fresh default and rewrites the managed files, so copy any settings you want to keep first; it never overwrites the static files. See [`.claude/` ownership](configuration.md#claude-ownership) for what `init` and `doctor` keep, overwrite, and delete.

`init` creates:

```
~/daily-driver-workspace/
├── .dd-config.yaml            # workspace config (you edit this)
├── .daily-driver/             # version stamp, SHA-256 manifest, state/ for locks + logs
├── .claude/
│   ├── settings.local.json    # rendered from packaged template
│   ├── hooks/                 # managed — regenerated on drift
│   ├── commands/daily-driver/ # managed — regenerated on drift
│   ├── commands/user/         # yours — never touched
│   ├── agents/daily-driver/   # managed — regenerated on drift
│   └── agents/user/           # yours — never touched
├── README.md                  # managed — command reference for this workspace
├── .gitignore                 # copied once; yours thereafter
├── context.md                 # your background and recurring tasks (copied once; yours thereafter)
└── voice-profile.md           # your writing-style profile (ditto)
```

## 2. Health check

```bash
daily-driver doctor
daily-driver doctor --fix      # regenerate if drifted; restore deleted managed files
daily-driver doctor --reset    # overwrite all managed files, including your edits
```

`--fix` and `--reset` are mutually exclusive. ERROR rows exit non-zero; WARNING rows exit 0. `--fix` restores managed files you may have deleted from `.claude/commands/daily-driver/`, `.claude/agents/daily-driver/`, `.claude/hooks/`, and the workspace `README.md`.

## 3. First tracker entry

```bash
daily-driver tracker add -c task -T "renew passport"
daily-driver tracker list
daily-driver status
```

`status` shows tracker totals, items stalled >14 days (non-terminal), and 7-day activity. `-j` / `--json` emits machine-readable output.

## 4. Scheduler (optional)

```bash
daily-driver scheduler install
```

Renders launchd plists into `~/Library/LaunchAgents/` for check-in reminders at 11:00 and 15:00; for the other jobs and times see [usage.md](usage.md#scheduler-macos). `daily-driver scheduler uninstall` removes them; `daily-driver scheduler status` lists configured jobs and whether each is installed. macOS only.

## If something looks off

Re-run the command with `-v` (or `-vv` for full debug traces). Status and log output goes to stderr, so `-v` is safe to add even when piping stdout into another tool:

```bash
daily-driver doctor -v
daily-driver summary --range 7d -vv 2>&1 | tee /tmp/summary.log
```

`-q` does the opposite — errors only — for scheduled or scripted runs where you only care about failures.

## Next

- [usage.md](usage.md) — the full daily flow.
- [commands.md](commands.md) — every subcommand and its non-obvious flags.
- [configuration.md](configuration.md) — `.dd-config.yaml` reference.
