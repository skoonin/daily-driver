---
name: daily-learning
description: Daily 15-30 minute interview practice - behavioral STAR drills, technical fundamentals, coding drills, system design
---

Short interview-practice session. Aim for 15-30 minutes total — don't derail morning planning. One question at a time, wait for the answer, give specific feedback, follow up once or twice, then close out and write a brief log.

This is a learning tool, not a tracking system. The goal is reps + honest feedback on weak spots, not a complete answer database.

## 0. Capture Current Time and Focus Rotation

```bash
echo "current_time=$(date +%H:%M) current_date=$(date +%Y-%m-%d) day_of_week=$(date +%A) dow_num=$(date +%u)"
```

Pick today's default focus from `dow_num` (1=Mon ... 7=Sun):

| Day | Default focus |
|-----|---------------|
| Mon (1) | behavioral (STAR drills) |
| Tue (2) | technical-fundamentals (alternate concept Qs with coding drills) |
| Wed (3) | system-design |
| Thu (4) | behavioral (STAR drills) |
| Fri (5) | leadership / influence |
| Sat (6) | user choice — ask which area feels weakest |
| Sun (7) | user choice — ask which area feels weakest |

This rotation is the default; override it in step 2 if recent practice already covered the focus, or if the user names a different area when prompted.

## 1. Load Voice Profile and Tracker Targets

Resolve the workspace output dir and Read the voice profile:

```bash
daily-driver paths voice-profile
```

Read the file at the printed path. STAR stories the user gives you should sound like that voice — concrete, direct, specific scope, no hedging. When you mirror an answer back or rewrite it, preserve their voice.

Pull active job-search targets to scope technical and system-design questions to the role types they're chasing:

```bash
daily-driver tracker list --category job --json
```

If there are no active job entries, ask: "Any specific role type to target today (SRE, platform, infra, swe-generalist, ...)?" Default to SRE / platform / infra when unset — that's the user's primary track.

## 2. Read Recent Practice Log

Resolve the output dir and look for the practice subdir:

```bash
daily-driver paths output
```

List the last 7 days of practice files in `<output>/interview-practice/`:

```bash
ls -t <output>/interview-practice/*.md 2>/dev/null | head -7
```

Read up to the 3 most recent files. From each, extract the `focus:` frontmatter value and the `## Questions` section topics. Use this to:

- **Avoid repeating questions** asked in the last 3 sessions verbatim. Variations on the same theme are fine if a `## Revisit` section flagged it.
- **Override the rotation** if today's default focus was the focus on the most recent session — pick the next-least-recent area instead.
- **Surface revisits** — if any recent file has a `## Revisit` section, mention one item at the top of the session ("Last Tuesday you wanted to revisit blameless postmortem framing — want to start there?").

If the directory does not exist or is empty, no recent history — proceed with the rotation default.

## 3. Announce the Session

Tell the user (briefly):

- Today's focus area.
- Whether you're picking up a revisit item from a recent log, or starting fresh.
- The shape: "I'll ask 2-4 questions, give feedback after each, and we'll wrap in about 10 minutes."

Then ask if they want to proceed or swap focus. If they swap, use their pick for the rest of the session.

## 4. Quiz Interactively

Run questions one at a time. Pace: 2-4 questions total depending on depth.

### Behavioral / Leadership (STAR)

Pick a prompt that targets a real workplace scenario the user would hit at the role types from step 1. Examples to vary across:

- A time you disagreed with an engineering decision your manager made — how did you handle it?
- Tell me about an outage you owned end-to-end. What was the root cause, and what changed because of it?
- A peer was blocking your project. How did you unblock it without escalating?
- A time you had to deliver bad news (slip, layoff impact, scope cut) to stakeholders.
- An on-call shift that went badly — what would you do differently?

For each answer, score against STAR explicitly:

- **Situation** — concrete enough? Named system / scope / team size?
- **Task** — was the user's specific responsibility clear, or did it blur into "we"?
- **Action** — first-person verbs? Decisions, tradeoffs, what was rejected?
- **Result** — measurable outcome? What changed for the team / system / business?

Common weak spots to flag specifically: "we" instead of "I", missing metric in Result, hedge words ("kind of", "sort of"), no tradeoff named in Action. Don't say "great job" — name the specific gap and ask a sharpening follow-up. Once. Then move on.

### Technical Fundamentals & Coding

On a technical-fundamentals day, alternate between **concept questions** and **coding drills** — don't do only one mode for weeks. Check the recent practice log (step 2): if the last technical-fundamentals session was concept-heavy, lead with a coding drill this time, and vice versa. Note the sub-mode in the log's `## Questions` so the rotation stays balanced.

**Concept questions.** Scope to the role types in step 1. SRE / platform / infra examples:

- Walk me through what happens when a TCP connection is established between two hosts.
- A pod is in CrashLoopBackOff. Walk me through your debug path, fastest signal first.
- Difference between a load balancer at L4 vs L7 — and when does the choice matter operationally?
- Describe the read path of a typical metrics pipeline (scrape -> store -> query). Where does it usually break under load?
- What is the failure mode of a leader-follower replicated database during a network partition? How do you reason about the user-visible impact?

Score the answer for: precision of vocabulary, willingness to say "I don't know" where appropriate, ability to navigate from symptom to system layer, and depth of follow-up handling.

**Coding drills.** Scope to SRE / platform / infra reality — not algorithm puzzles. Two styles, vary across sessions:

- *Practical scripting / debugging* — give a small, real task or a broken snippet (bash/Python/Go). Examples: parse a log file and emit the top-N error sources; write a healthcheck that retries with backoff and exits non-zero on failure; this script silently drops errors — fix it; reason out loud about what a given snippet does and where it breaks. Keep it small enough to talk through in a few minutes; the user can type an answer or describe it.
- *Code reading / review* — paste a short snippet and ask for the bug, the race, or a design critique. Tests judgment over blank-page recall: "what's wrong here," "what happens under concurrent calls," "what would you change before this ships."

Score coding answers for: correctness and edge-case awareness (empty input, failure paths, off-by-one), idiomatic use of the language, whether they reason about failure modes before happy path, and whether they catch silent-failure / error-swallowing patterns. Don't say "looks good" — name the specific bug or the missing edge case, ask one sharpening follow-up, then move on.

### System Design

Scope to a tractable 8-minute design. Examples:

- Design a rate limiter for a multi-tenant API gateway. Per-tenant + per-IP. Justify the data store.
- Design a deployment system that can deploy 200 services with per-env approval gates and rollback.
- Design a metrics ingestion pipeline that handles 1M datapoints/sec with <30s query freshness.
- Design a feature flag service used across 50 services with a 50ms p99 read SLA.

Score for: clarifying questions before sketching, explicit tradeoffs (consistency vs availability, push vs pull, sync vs async), capacity numbers attached to choices, and naming what would break first at 10x scale.

## 5. Close the Session

When you've done 2-4 questions or hit ~10 minutes, stop. Don't drag.

Summarize in 3-5 lines:

- What was strong this session.
- The most useful single weak spot to work on (be specific — not "be more confident", but "name the metric in Result" or "always state the consistency model before drawing the diagram").
- One thing to revisit next time, if anything.

## 6. Write the Practice Log

Append to `<output>/interview-practice/<YYYY-MM-DD>.md`. Create the directory if it does not exist. If the file already exists from an earlier session today, append a `---` separator and a new block rather than overwriting.

Use this shape:

```markdown
---
date: YYYY-MM-DD
focus: behavioral | technical-fundamentals | system-design | leadership
duration_minutes: <integer estimate>
---

## Questions

- <Question 1 verbatim or paraphrased>
- <Question 2>
- <...>

## Strengths

- <specific thing the user did well, one line>

## Weak spots

- <specific gap, one line — must be actionable, not generic>

## Revisit

- <topic to come back to, or omit the section if nothing>
```

Keep it tight. The log is for the next session to read in step 2 — it should help future-Claude pick the next focus and avoid repeats. It is not a journal.

## 7. Hand Back

Return control to the user. If this was invoked from `/daily-driver:day-start`, they will continue with the plan-save step.
