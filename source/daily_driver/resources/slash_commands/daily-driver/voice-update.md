---
name: voice-update
description: Update voice-profile.md from one or more source writing samples
---

Refresh the workspace voice profile by extracting observations from the
user's own writing. The profile trains Daily Driver to draft in the user's
voice over time.

## Arguments

- Source paths (required, repeatable): `--from <path>` where each path is
  either a file (`.md` / `.txt`) or a directory (recursed).
- Mode (optional, default `--append`):
  - `--append`: preserve existing profile; add new observations from the samples.
  - `--replace`: regenerate from scratch; previous profile saved as `.bak`.
- `--dry-run`: validate the sources and print the target path without calling the model or writing. It does NOT preview the generated profile (that would cost a model call).

## Workflow

### 1. Confirm sources

Ask the user to confirm the source paths before running. Call out any
paths that look risky (binaries, very large files, unrelated drafts).
Files >500KB and binaries are silently skipped by the CLI.

### 2. Validate with --dry-run (optional)

Run a dry-run first to confirm the sources are accepted and see the target
path. It validates the prompt and prints the target path only — it does NOT
call the model or preview the generated profile (that would cost a model call):

```bash
daily-driver voice-update --from <path>... --dry-run
```

Relay the validation output to the user. To actually generate and review the
profile, run without `--dry-run` (the result is also copied to the clipboard).

### 3. Apply

If they approve:

```bash
daily-driver voice-update --from <path>...
```

Add `--replace` only if the user explicitly wants a full regeneration. The
existing profile is backed up to `voice-profile.md.bak` automatically.

### 4. Reminders

- Never run `--replace` silently. The existing profile took effort to build.
- The voice profile is narrative, not structured config. Don't offer to edit
  it manually here — `voice-update` is the supported path.
