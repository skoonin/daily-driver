# Feature parity audit: legacy `daily-driver` → pure-Python CLI

> Comparison baseline: legacy bash/markdown implementation at commit
> `7d944dc` (`daily-driver` repo, pre-pure-Python rewrite) versus
> the current `daily-driver` console command (this repo, head of `main`
> at `f054361` plus W4 Daily-State epic).
>
> Status legend:
>
> - **parity** — capability is fully replaced by an equivalent CLI
>   surface or shipped artifact, with no observable user-facing
>   regression from the legacy behavior.
> - **partial** — replacement exists but covers a strict subset of
>   the legacy capability; gap is intentional or scheduled.
> - **missing** — capability has no equivalent today and is worth
>   restoring; tracked as a follow-up row at the bottom of this file.
> - **dropped** — capability was intentionally removed during the
>   rewrite; rationale recorded so the decision is not re-litigated.

This audit is a working document, not user-facing docs. Update it as
parity gaps are closed or new follow-ups are filed.

## Storage location

Lives at `docs/parity-audit.md` rather than `.claude/parity-audit.md`
because the user's global gitignore matches `*CLAUDE*` / `*claude*`
patterns and excludes everything under `.claude/` from version
control. `docs/` is tracked unconditionally.

---

## Slash commands (legacy `commands/*.md`)

| Legacy command | Replacement | Status | Notes |
|---|---|---|---|
| `commands/check-in.md` | `daily-driver check-in` launcher + materialized `commands/daily-driver/check-in.md` | parity | Launcher resolves session-id (W4 daily-state), opens a `claude --resume` session with the same prompt body. |
| `commands/day-start.md` | `daily-driver day-start` + materialized `commands/daily-driver/day-start.md` | parity | W4 added per-day state YAML, late-day handling, scheduled background gather. |
| `commands/day-end.md` | `daily-driver day-end` + materialized `commands/daily-driver/day-end.md` | parity | Day-end now also persists final state to `daily_state.yaml` (W4). |
| `commands/focus.md` | `daily-driver focus {on,off,status}` | parity | Flock-backed; suppresses launchd-driven check-in notifications. |
| `commands/voice-update.md` | `daily-driver voice-update --from PATH` + materialized `commands/daily-driver/voice-update.md` | parity | Headless rewrite of `voice-profile.md` from writing samples. |
| `commands/setup.md` | `daily-driver init` + `daily-driver doctor [--fix\|--reset]` | parity | Init scaffolds workspace; doctor verifies/repairs. Tool-auth nudges (icalBuddy, jq, yq, terminal-notifier) folded into doctor checks. |
| `commands/standup.md` | `daily-driver summary --range SPEC` | partial | `summary` produces a Claude-driven period summary with clipboard output, but the legacy script's structured Y/T/B frontmatter parser (`build-standup.sh`) is not reproduced. Captured as follow-up. |
| `commands/week-end.md` | `daily-driver summary --range week` | parity | Range-resolution + claude prompt covers the legacy "weekly rollup". |
| `commands/month-end.md` | `daily-driver summary --range month` | parity | Same code path as week-end with a different range spec. The "fall back to last month if today is the 1st" affordance is delegated to range-spec parsing. |
| `commands/prep.md` | — | missing | Meeting-prep flow (calendar gather → pick meeting → assemble brief). No CLI equivalent; calendar gather exists but the orchestration / prompt does not. Follow-up. |
| `commands/interview-prep.md` | — | missing | Interview-prep flow + per-application interview-state YAML (`{state_dir}/interview-state/{app-id}.yaml`). No CLI equivalent. Follow-up. |

---

## Agents (legacy `agents/*.md`)

| Legacy agent | Replacement | Status | Notes |
|---|---|---|---|
| `agents/work-planner.md` | `source/daily_driver/agents/daily-driver/work-planner.md` (shipped via package data) | partial | Schema-stale: refers to `app-NNN` and assumes a job-search-only world. Closed inline by this PR (#47). |

---

## Scripts (legacy `scripts/*.sh`, `scripts/scrape-jobs.py`)

Legacy scripts split into **user-facing capabilities** (worth replacing)
and **shell internals** (absorbed into Python — listed last as `dropped`
with shared rationale).

### User-facing capabilities

| Legacy script | Replacement | Status | Notes |
|---|---|---|---|
| `scripts/tracker.sh` | `daily-driver tracker {add,list,update,stats,follow-ups}` | parity | New tracker is config-driven (categories), not jobs-only. ID format is `{category}-NNN` (e.g. `task-001`, `job-001`). |
| `scripts/scrape-jobs.py` | `daily-driver jobs {run,status}` | parity | W2 rename; W6 prune + currency primary mode; JobSpy hidden behind plugin. |
| `scripts/gather-jobs.sh` | `daily-driver jobs run` (background mode + scheduled run) | parity | launchd plist still drives the scheduled run. |
| `scripts/gather-calendar.sh` | `daily-driver gather calendar [--json]` | parity | Reads icalBuddy or stub. |
| `scripts/gather-git-activity.sh` | `daily-driver gather git [--json]` | parity | W3 hardened. |
| `scripts/gather-sessions.sh` | `daily-driver gather sessions [--json]` | parity | Reads claude session history. |
| `scripts/gather-notes-range.sh` | `daily-driver gather notes [--json]` | parity | Range-resolved via shared `core.dates`. |
| `scripts/gather-carryforward.sh` | W4 daily-state carry-forward | parity | Now handled by `daily_state.yaml` + `day-start` materialization. |
| `scripts/gather-applications.sh` | — | missing | Application-pipeline summary (active stages, blocked items). Tracker can answer parts of it via category filters but no aggregated "applications" view exists. Follow-up. |
| `scripts/gather-company-docs.sh` | — | missing | Per-company doc auto-create on tracker add (legacy CLAUDE.md flow). Follow-up. |
| `scripts/focus-mode.sh` | `daily-driver focus` | parity | Same flock semantics. |
| `scripts/ensure-daily-dir.sh` | `daily-driver ensure-daily-dir` | parity | |
| `scripts/get-output-dir.sh`, `get-state-dir.sh` | `daily-driver paths [<kind>] [--json]` | parity | Single command covers both. |
| `scripts/check-output-dir.sh`, `check-state-dir.sh`, `init-output-dir.sh` | `daily-driver doctor [--fix]` | parity | Verification + repair folded into doctor. |
| `scripts/read-context.sh`, `read-plan.sh`, `read-voice-profile.sh` | `daily-driver read {context,voice-profile,plan}` | parity | |
| `scripts/read-plan-frontmatter.sh` | — | dropped | Implementation detail; current commands consume frontmatter inline via Claude prompt rather than via a shell helper. |
| `scripts/snapshot-tracker.sh` | — | missing | Tracker YAML snapshot helper for crash-recovery / audit. Low priority follow-up; current locking already gives crash-safety on writes. |
| `scripts/launchd-install.sh` | `daily-driver install-scheduler` / `uninstall-scheduler` | parity | Plists generated from `scheduler.default.yaml` + user overrides. |
| `scripts/list-ruled-out.sh`, `record-ruled-out.sh` | — | missing | Job-pipeline "ruled out" log. The RULED_OUT protocol still exists in shipped command prompts but there is no CLI command to record / list ruled-out entries. Follow-up. |
| `scripts/record-interview-state.sh` | — | missing | Per-application interview state YAML (paired with `commands/interview-prep.md`). Follow-up. |
| `scripts/sync-repos.sh` | — | dropped | Multi-repo sync of `.claude/` directories. Obsolete since the rewrite ships managed `.claude/` artifacts via package data. |
| `scripts/commit-notes.sh` | — | dropped | Auto-commit of daily notes. Replaced by user-driven `git add -A` in workspace; commit cadence is a user concern, not a tool concern. |
| `scripts/calendar-check.sh`, `calendar-sync.sh`, `check-calendar-sync.sh` | — | partial | Calendar-sync workflow (writing calendar items into a structured store) is not reproduced. `gather calendar` reads icalBuddy live each session. Follow-up if persistence becomes important. |
| `scripts/find-session-id.sh`, `build-session-delta.sh`, `read-session-delta.sh`, `open-session.sh`, `checkin-state.sh` | W4 daily-state + `integrations/claude.py` | parity | Session resume + delta logic is now Python. |
| `scripts/build-pipeline-summary.sh`, `show-pipeline-summary.sh` | `daily-driver jobs status` | partial | `jobs status` shows last-run metadata, not a full pipeline summary across runs. Adequate for current product needs; revisit if pipeline-summary view returns. |
| `scripts/build-standup.sh` | — | partial | Y/T/B structured assembly from frontmatter (no Claude in the loop) is not reproduced. `summary --range` covers the rollup case but always involves Claude. Follow-up if a deterministic standup is wanted. |
| `scripts/hook-current-time.sh` | `templates/hooks/hook-current-time.sh` (W1 port) | parity | Materialized into workspace `.claude/`; UserPromptSubmit hook entry added on `init`. |
| `scripts/week-range.sh`, `month-range.sh`, `standup-dates.sh`, `standup-save-path.sh`, `monthly-save-path.sh`, `weekly-save-path.sh` | `core.dates` (`parse_since`, range helpers) | dropped | Date-arithmetic helpers absorbed into the unified Python date parser; no longer user-facing. |
| `scripts/list-weekly-summaries.sh` | — | dropped | Glob over the daily-notes tree was a bash convenience for `month-end`. The current `summary --range month` reads notes directly via gather. |
| `scripts/build-pipeline-summary.sh` (subset) | — | dropped | The bash-only "show me the deltas since last run" output is implementation detail; current jobs flow handles dedup in the archive table itself (W6). |

---

## Hooks / settings (legacy `.claude/`-equivalent assets)

| Legacy asset | Replacement | Status | Notes |
|---|---|---|---|
| `hook-current-time.sh` UserPromptSubmit hook | `templates/hooks/hook-current-time.sh` materialized on `init` | parity | (#46 — closed by W1.) |
| `settings.json.tmpl` | `templates/settings.local.json.j2` rendered on `init` | parity | Repo-local template; ports portable-paths concern from legacy. |
| `context.md.example` | `templates/context.md` materialized on `init` | parity | |

---

## Launchd plists (legacy `launchd/*.plist`)

| Legacy plist | Replacement | Status |
|---|---|---|
| `com.daily-driver.checkin.plist` | `daily-driver install-scheduler` (config-driven plist gen) | parity |
| `com.daily-driver.day-start.plist` | same | parity |
| `com.daily-driver.day-end.plist` | same | parity |
| `com.daily-driver.gather-jobs.plist` | same (renamed to `jobs`-prefixed plist post-W2) | parity |

Schedule cadence is sourced from `templates/scheduler.default.yaml` plus
user overrides in `.dd-config.yaml`.

---

## Top-level legacy files

| Legacy file | Replacement | Status |
|---|---|---|
| `config.yaml` (legacy global config) | `.dd-config.yaml` per-workspace + pydantic `Config` model | parity |
| `CLAUDE.md` (legacy project instructions) | `CLAUDE.md` (this repo) + materialized `context.md` template | parity |
| `README.md` | `README.md` + `docs/quick-start.md`, `docs/install.md`, etc. | parity |
| `pyproject.toml` | unchanged surface; rewrite migrated to a proper console-script entry point | parity |

---

## Follow-ups discovered by this audit

These are gaps that warrant their own work item rather than inline fixes
in this PR. File against `review-2026-04-23.md`-style numbering when
opening tickets.

1. **`commands/prep.md` — meeting-prep flow.** No CLI equivalent today.
   Calendar gather exists; orchestration prompt + meeting-selection UX
   does not. *(Sized: M; depends on a tracker view of "applications by
   stage".)*
2. **`commands/interview-prep.md` + `record-interview-state.sh` —
   interview-prep flow + per-application state YAML.** Whole subsystem
   missing. *(Sized: M-L; depends on item 1.)*
3. **`scripts/gather-applications.sh` — application-pipeline gather.**
   The tracker can produce category-filtered lists but no aggregated
   "active applications by stage" view. *(Sized: S; same surface as
   `tracker stats`.)*
4. **`scripts/gather-company-docs.sh` — per-company docs auto-create.**
   Legacy CLAUDE.md described tracker `add` auto-creating per-company
   doc files. New tracker does not. *(Sized: S; tracker-side hook.)*
5. **`scripts/list-ruled-out.sh` + `scripts/record-ruled-out.sh` — job
   ruled-out log.** RULED_OUT protocol still appears in shipped command
   prompts but no CLI receives the structured output. *(Sized: S;
   probably folds into tracker as a status transition.)*
6. **`scripts/build-standup.sh` — deterministic Y/T/B standup
   assembly.** Current `summary` always involves Claude. A
   no-Claude-in-the-loop standup builder over plan/notes frontmatter
   would be cheap and predictable. *(Sized: S.)*
7. **Calendar-sync persistence (`calendar-check.sh`,
   `calendar-sync.sh`).** Currently `gather calendar` reads icalBuddy
   live every session. If/when calendar items need to participate in
   carry-forward, persistence will matter. *(Sized: M; defer until
   user-visible need surfaces.)*
8. **`tests/test_core/test_shipped_prompts.py` — lint shipped agent and
   command prompts for known-stale tokens at build time.** Called out
   in `plan-post-review-impl.md` §6 WS-M (#47). Not added in this PR
   to keep scope tight; capture as a follow-up so future drift (e.g.
   if `task-NNN` changes again) is caught at build time. *(Sized: S.)*
