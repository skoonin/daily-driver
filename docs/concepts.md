# Concepts

This page explains the mental model behind Daily Driver: what exists on disk, what each surface does, and why the program is structured the way it is. Read this once; then use [usage.md](usage.md) for the daily flow and [commands.md](commands.md) for flag detail.

## What it does

Daily Driver keeps the durable record of your day on disk (tracker, plans, notes, job search) and hands a `claude` session the context it needs to help you plan, check in, and wind down. The program owns the data; Claude is the conversation layer.

You drive it from one CLI: `daily-driver`. Everything else lives in a workspace directory you scaffold once.

## Workspace layout

```
workspace/                 your data
├── .dd-config.yaml        what daily-driver knows about you
├── context.md             biographical context for claude
├── voice-profile.md       writing-style profile for claude
├── tracker.yaml           anything you follow up on
├── jobs.csv               job-search rows (if you use the jobs plugin)
├── 2026/05/11/            today's plan + notes (created on demand)
└── .claude/, .daily-driver/  managed by daily-driver; mostly leave alone
```

The workspace is a plain directory. All state is in files you can read, edit, and back up with `git` or any tool. Daily Driver does not phone home and has no server component.

## Surfaces

| Surface | What it does | Commands |
| --- | --- | --- |
| **Workspace** | Holds your data. One per machine, typically. | `init`, `doctor`, `paths` |
| **Tracker** | YAML store for tasks, jobs, errands, contacts, anything. | `tracker {add,list,update,show,delete,prune,stats,follow-ups}` |
| **Focus** | File-lock toggle that suppresses scheduled check-ins. | `focus {on,off,status}` |
| **Day cycle** | Interactive `claude` sessions for morning / mid-day / evening. | `day-start`, `check-in`, `day-end` |
| **Jobs** | Multi-board scraper plus AI enrichment for `jobs.csv`. | `jobs {run,status,prune}` |
| **Summary** | Headless period summary copied to clipboard. | `summary -r SPEC` |
| **Voice** | Rewrite `voice-profile.md` from real writing samples. | `voice-update --from PATH` |
| **Scheduler** | macOS launchd plists for unattended runs. | `scheduler {install,uninstall,status}` |
| **Help** | Built-in reference for discoverable values. | `help [TOPIC]` |

## Design decisions

**The program owns the record; Claude owns the conversation.** Claude is not a reliable data store. The tracker, jobs CSV, plans, and notes live in files. Claude reads them — it writes them only via explicit CLI commands (`tracker update`, `jobs run`, etc.).

**One workspace per machine, typically.** Discovery walks upward from `$CWD` to find `.dd-config.yaml`. Pass `-w PATH` to any command to override.

**Managed vs. user territory.** Files under `.claude/*/daily-driver/` are regenerated on `init` and on version drift. Your own commands and agents go at the top level (`.claude/commands/*.md`), where they are never touched. Full ownership table: [Customization](configuration.md#customization).

**No automatic migrations.** Config is validated by Pydantic with `extra="forbid"`. When a release adds or renames a key, `doctor` reports an error and the CHANGELOG describes the manual edit needed.
