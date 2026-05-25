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
- `--dry-run`: print the proposed new profile to stdout without writing.

## Workflow

### 1. Confirm sources

Ask the user to confirm the source paths before running. Call out any
paths that look risky (binaries, very large files, unrelated drafts).
Files >500KB and binaries are silently skipped by the CLI.

### 2. Preview with --dry-run

Run a dry-run first so the user can review what would be written:

```bash
daily-driver voice-update --from <path>... --dry-run
```

Relay the output to the user and ask whether to proceed.

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
