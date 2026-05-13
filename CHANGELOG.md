# Changelog

Daily Driver is a pre-1.0 personal tool with no external users. This file
is a rolling summary of the current state; granular history lives in `git
log`. Versioned release history starts at 1.0.

## [Unreleased]

### AI providers

- **Ollama backend for headless tasks** via the `ai:` config block.
  `enrichment` and `summary` route per-task (`provider: claude | ollama`);
  each has its own `model` field. Omitting the block preserves claude-only
  behavior.
- **Parallel ollama enrichment** via `ai.ollama.max_parallel` (default 4).
  Per-worker log tags (`[enrich w2]`) help trace interleaved failures.
- **`AI providers` doctor row** reports reachability, confirms the
  configured model is pulled, and shows effective parallelism. Omitted
  when no task routes to ollama.
- **Interactive launchers stay claude-only** (session resume, agents,
  workspace `--add-dir` context that ollama does not provide).

### Jobs scraper

- **Fix: Apple scraper now runs in non-headless mode.** `_non_headless_sources`
  previously relied on a `type: playwright` config key that `SourceToggle`
  (`extra="forbid"`) silently rejects, so Apple always fell into the headless
  phase and crashed on macOS with a Mach port error. Classification now uses
  the code-level `_PLAYWRIGHT_SOURCES` constant.

- **Shared `.jobs.lock`** serializes `jobs run` / `jobs prune` / `jobs run
  --backfill` mutations on `jobs.csv`.
- **Ctrl-C during `--backfill` flushes partial progress** and names the
  pre-run `jobs.csv.bak.<unix>` snapshot in the interrupt message. Second
  Ctrl-C force-quits. CLI exits 130.
- **HN sources hardened**: 429 retry/backoff, Algolia thread discovery,
  new `hn_jobs` source, age filter. Detail-fetch skipped for HN and
  Indeed (no useful enrichment).
- **Backfill counters now exclude `status=skipped` rows.** `Backfill
  complete` reports `+N Notes` (previously hidden). `enrich-fit-notes`
  logs startup + end-of-pass INFO lines so silent ollama failures are
  visible.
- **`Jobs backups` doctor row** warns when more than 5 `jobs.csv.bak.*`
  files accumulate.

### Logging and output

- **Two-stream Console** (`core.console.Console`): data payloads on
  stdout, status / warnings / errors / logs on stderr. Module loggers
  share `Console.get_log_console()` for theme consistency.
- **Repeatable verbosity**: `-v` = INFO, `-vv` = DEBUG, `-q` = errors
  only. Single `-v` no longer jumps straight to DEBUG.
- **`--no-color` decoupled from markup.** Color is suppressed via
  `color_system=None`; Rich markup and highlights stay on so tables and
  styled prefixes still render cleanly.

### CLI

- **W8 polish**: `scheduler {install,uninstall,status}` group replaces
  the old top-level commands; `materialize` renamed to `generate`;
  focus/tracker/status improvements; short flags across subcommands;
  idempotent `init` and `doctor --fix` (restores deleted managed files).
- **`/interview-prep` → `/daily-learning`** slash command rename.
- **W2 breaking rename**: `scrape-jobs` family → `jobs run` / `jobs
  status` / `jobs prune`.

### Docs

- **Restructured into Diataxis quadrants.** New `docs/README.md`
  navigation index, new `docs/concepts.md` (mental model + workspace
  layout), `customization.md` merged into
  `configuration.md#customization`, developer docs moved to `docs/dev/`.
- **`docs/cli-tree.md`** reframed as orientation-only reference.
- **New `docs/ollama-setup.md`** walkthrough for the Ollama provider.
