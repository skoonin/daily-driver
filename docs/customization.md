# Customization

## `.claude/` ownership

Generation only touches three paths. Everything else is yours.

| Path | Owner | Behavior on generate |
|------|-------|-------------------------|
| `.claude/commands/daily-driver/` | Daily Driver | Wiped and recopied from package |
| `.claude/agents/daily-driver/` | Daily Driver | Wiped and recopied from package |
| `.claude/settings.local.json` | Daily Driver + user merge | Re-rendered from template; user-added top-level keys preserved |
| `.claude/commands/*.md` (top level, outside `daily-driver/`) | You | Untouched |
| `.claude/agents/*.md` (top level, outside `daily-driver/`) | You | Untouched |
| Anything else under `.claude/` | You | Untouched |

User edits to files under `.claude/*/daily-driver/` are detected via SHA-256 manifest and preserved by `doctor --fix` on version drift. `doctor --reset` is the nuclear option: it force-overwrites managed files regardless of edits.

## Custom Claude commands

Place a markdown file at `.claude/commands/my-command.md` (any path outside `daily-driver/`). Invoke with `/my-command` inside any `claude` session launched with `--add-dir <workspace>`.

## Custom Claude agents

Place a markdown file at `.claude/agents/my-reviewer.md`. Invoke with `claude --agent my-reviewer ...`.

## Override a shipped command

Do not edit a file under `.claude/commands/daily-driver/` — your edit will survive `--fix` but be lost on `--reset`. Instead, copy the file up one level:

```bash
cp .claude/commands/daily-driver/day-start.md .claude/commands/day-start.md
```

Edit the top-level copy. Claude Code resolves top-level before namespaced, so your version wins.

## Custom tracker categories

Categories are config-driven; no code changes needed. Edit `.dd-config.yaml`:

```yaml
tracker:
  default_category: task
  categories:
    task:    {required: [title]}
    ticket:  {required: [title]}
    contact: {required: [title]}
```

Any fields passed via `tracker add --extra key=value` land in the entry's `extras:` block without schema changes.

## Context and voice profile

`context.md` and `voice-profile.md` in the workspace root are copied once on `init` and are yours thereafter — edit freely. They are read by `read context` / `read voice-profile` and injected into Claude sessions. `voice-update` rewrites `voice-profile.md` from writing samples via headless `claude`.
