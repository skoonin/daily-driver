---
name: daily-learning
description: Daily self-directed practice session. First run asks what to learn, how, and how long; every run after that works from the syllabus that answer created. Defaults to a lens-first teaching style if the user has no preference.
---

Short practice session — don't derail morning planning. Run it for the session length the user set (see the syllabus `session_length`); if they haven't set one, keep it focused and stop at a natural break rather than imposing a fixed number. One prompt at a time, wait for the answer, give specific feedback, follow up once or twice, then close out and write a brief log.

This is a practice tool, not a tracking system. The goal is reps + honest feedback on weak spots, not a complete answer database. Nothing about what a specific user wants to learn belongs in this file — that lives entirely in their workspace, created fresh on first run.

## 0. Capture Current Time

```bash
echo "current_time=$(date +%H:%M) current_date=$(date +%Y-%m-%d) day_of_week=$(date +%A)"
```

## 1. Load or Establish the Syllabus

Resolve the workspace output dir:

```bash
daily-driver paths output
```

Look for `<output>/daily-learning/syllabus.md`.

### First run (file does not exist)

Ask the user three things:

1. **"What do you want to focus on in these practice sessions?"** Open-ended — one subject or several. If they want ideas, offer a few shapes (not a fixed menu): behavioral/STAR interview stories, technical fundamentals for a role they're targeting, coding drills, system design, or any other self-directed subject.
2. **"Do you have a specific method or style you'd like me to use, or should I default to a lens-first approach?"** If they ask what that means: "each session opens with one unifying principle broad enough to reorganize several things you already know, teaches that principle, then shows the concrete instances that collapse into it — the specifics get drilled after the reframe, not before." If they have no preference, default to lens-first.
3. **"How long do you want sessions to run?"** Accept whatever they give — a duration ("20 minutes"), a question count ("3-4 questions"), or "no fixed limit, stop at a natural break." Store it verbatim; don't impose a number of your own.

Create `<output>/daily-learning/` and write their answers as the new syllabus:

```yaml
---
created: YYYY-MM-DD
style: lens-first
session_length: <what the user said, verbatim>
topics:
  - name: <topic as the user described it>
    added: YYYY-MM-DD
    last_covered: null
notes: ""
lenses_taught: []
---

# Daily Learning Syllabus

<one or two lines restating what they asked for, in their words where possible>
```

`style` is `lens-first` unless the user named something else (write exactly what they said — "STAR drills," "flashcards," "hands-on labs only," whatever). `session_length` holds their answer verbatim; if they had no preference, write `no fixed limit — stop at a natural break`. `lenses_taught` stays empty until the first lens-first session adds to it (step 5).

### Subsequent runs

Read the syllabus. If the user wants to add a topic, retire one, or change style, update the file in place before continuing — this is a living document, not a one-time snapshot.

## 2. Load Supporting Context (if relevant)

If any topic in the syllabus is interview- or job-search-related (behavioral, technical-fundamentals-for-a-role, system-design, leadership), pull supporting context — otherwise skip this step entirely.

Resolve and read the voice profile:

```bash
daily-driver paths voice-profile
```

Answers the user gives you should sound like that voice — concrete, direct, specific scope, no hedging. Preserve their voice when you mirror an answer back or rewrite it.

Pull active job-search targets to scope questions to real role types:

```bash
daily-driver tracker list --category job --json
```

If there are no active job entries, ask what role type to target, or default to whatever the syllabus topic implies.

## 3. Read Recent Practice Log

List the last 7 days of practice files:

```bash
ls -t <output>/daily-learning/*.md 2>/dev/null | grep -v syllabus.md | head -7
```

Read up to the 3 most recent. From each, extract the `topic:` frontmatter value and the `## Questions` section. Use this to:

- **Avoid repeating recent prompts** verbatim. Variations on the same theme are fine, especially if a `## Revisit` section flagged it.
- **Pick today's topic** — the syllabus topic least recently covered (`last_covered`, or `added` if never covered), unless the user flags one as urgent.
- **Surface a revisit** — if a recent file has a `## Revisit` section, mention it at the top ("Last time you wanted to revisit X — want to start there?").

If the directory has no logs yet, this is the first real session — proceed with whichever syllabus topic makes sense to start on.

## 4. Announce the Session

Tell the user, briefly:

- Today's topic, and whether it's a revisit or fresh ground.
- The shape: "I'll ask a few questions, give feedback after each, and we'll wrap up when we hit your session length" — state the `session_length` from the syllabus so they know the scope.

Ask if they want to proceed or swap topics. If they swap, use their pick for the rest of the session.

## 5. Run the Session

How you run it depends on `style` from the syllabus.

### Lens-first (default)

1. **Open with the lens.** Identify one unifying principle for today's topic — broad enough that several concrete facts the user already knows (or is about to learn) turn out to be instances of it. State the principle plainly before any specifics.
2. **Collapse instances into it.** Walk 2-4 concrete things through the lens, showing each is the same underlying idea wearing a different costume.
3. **Bridge to real use** — name where the principle shows up in the user's actual work or goal, and how it would answer a probing question on the subject.
4. **Atomize downward.** Break the lens into 2-4 say-it-back checks — specific, answerable-cold questions the user should be able to hit without notes. Quiz these live; correct immediately, don't just move on.
5. **Record the lens.** Append an entry to the syllabus's `lenses_taught` list: `{topic, principle: <one line>, date}`. This is what lets a later session build on today's instead of re-deriving it.

### A named shape the user specified

If the syllabus topic maps to a well-known practice shape, use its established rubric rather than inventing one:

**Behavioral / STAR** — pick a scenario prompt that targets real situations for the user's goal. Score explicitly against Situation (concrete, named scope), Task (their specific responsibility, not blurred into "we"), Action (first-person verbs, decisions, tradeoffs), Result (measurable outcome). Flag "we" instead of "I," missing metrics, hedge words, no named tradeoff. Name the specific gap, ask one sharpening follow-up, then move on — don't just say "great job."

**Technical fundamentals** — concept questions scoped to the user's stated focus. Score for precision of vocabulary, willingness to say "I don't know" where warranted, and the ability to navigate from a symptom to the layer it lives at.

**Coding drills** — small, real tasks (not algorithm-contest puzzles) unless the user's goal is specifically that. Score for correctness, edge-case awareness, and reasoning about failure modes before the happy path. Name the specific bug or missing edge case rather than "looks good."

**System design** — a design scoped tight enough to walk through in one sitting. Score for clarifying questions asked before sketching, explicit tradeoffs named, capacity numbers attached to choices, and what breaks first at 10x.

If the user's stated method doesn't match any of these, follow what they described.

### Neither (a subject with no obvious shape and no stated method)

Default to teach → say-it-back → correct: introduce the idea plainly, have the user restate it in their own words, correct immediately. Treat it as a lens-first session if a unifying principle is available; otherwise keep it concrete.

## 6. Close the Session

When you reach the `session_length` the user set — a duration, a question count, or a natural stopping point if they set no limit — stop. Don't drag.

Summarize in 3-5 lines:

- What was strong this session.
- The most useful single weak spot to work on — specific, not generic ("name the metric in Result," not "be more confident").
- One thing to revisit next time, if anything.

## 7. Write the Practice Log

Update `<output>/daily-learning/syllabus.md`: set `last_covered` on today's topic, and append to `lenses_taught` if step 5 ran the lens-first path.

Append to `<output>/daily-learning/<YYYY-MM-DD>.md`. Create the directory if needed (it should already exist from step 1). If the file already exists from an earlier session today, append a `---` separator and a new block rather than overwriting.

```markdown
---
date: YYYY-MM-DD
topic: <topic name from the syllabus>
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

Keep it tight. The log is for the next session to read in step 3 — it should help future-Claude pick the next topic and avoid repeats. It is not a journal.

## 8. Hand Back

Return control to the user. If this was invoked from `/daily-driver:day-start`, they will continue with the plan-save step.
