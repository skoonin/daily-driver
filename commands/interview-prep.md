---
name: interview-prep
description: Interview preparation - practice questions and gather role context
---

<!--
## Interview State Schema

Per-application state file written to:
  {state_dir}/interview-state/{app-id}.yaml

Fields:
  app_id:               string   # matches tracker.yaml app_id
  company:              string
  role:                 string
  last_session:         ISO8601  # timestamp of most recent prep session
  round:                int      # interview round being prepped for
  questions_practiced:           # questions covered in past sessions
    - question:         string
      date:             ISO8601
      quality:          string   # "strong" | "ok" | "needs-work"
  feedback_given:       string   # summary of verbal feedback given last session
  open_gaps:                     # areas that still need work going into next session
    - string
  next_session_focus:   string   # one-line directive for the next prep session

Written by: scripts/record-interview-state.sh (reads YAML from stdin)
Read by: Step 1 of this command
-->

Prepare for an upcoming interview. Follow these steps in order.

## 0. Capture Current Time

```bash
echo "current_time=$(date +%H:%M) current_date=$(date +%Y-%m-%d)"
```

## 1. Load Interview State

Resolve the state directory and check for an existing interview-state file for this application.

If `$ARGUMENTS` supplies an app_id (or can be matched to one after Step 2), run:

```bash
STATE_DIR=$(bash scripts/get-state-dir.sh)
APP_ID="<app-id-from-step-2>"
STATE_FILE="${STATE_DIR}/interview-state/${APP_ID}.yaml"
if [[ -f "$STATE_FILE" ]]; then cat "$STATE_FILE"; else echo "NO_PRIOR_STATE"; fi
```

If the file exists, present a continuity summary before proceeding:

> "Last session (<last_session date>): covered <count> questions. Open gaps: <open_gaps list>. Focus today: <next_session_focus>."

If the file does not exist, note: "First interview prep for this application — no prior state."

Note: app_id may not be known until Step 2 identifies the role. If `$ARGUMENTS` is ambiguous, complete Step 2 first, then return here to load the state file before starting the practice session.

## 2. Identify the Target Role

If `$ARGUMENTS` is set, use it as the company or role to prep for. Otherwise, gather applications and ask:

```bash
bash scripts/gather-applications.sh
```

Show the active applications and ask: "Which company or role are you prepping for? You can paste a job title, company name, or application ID."

## 3. Load Role Context

Read recent notes to surface any prior prep or interview context for this role:

```bash
TODAY=$(date +%Y-%m-%d); DOW=$(date +%u); if [ "$DOW" = "1" ]; then SINCE=$(date -v-7d +%Y-%m-%d); else SINCE=$(date -v-3d +%Y-%m-%d); fi; bash scripts/gather-notes-range.sh "$SINCE" "$TODAY" all
```

## 4. Read User Profile

```bash
bash scripts/read-context.sh
```

## 5. Load Voice Profile

```bash
bash scripts/read-voice-profile.sh
```

Apply the voice patterns when drafting any written communication in this session (thank-you notes, follow-up emails, etc.).

## 6. Practice Session

Using the gathered context (role, company, notes, user background), run a focused interview prep session.

Structure the session as follows:

### Role Summary

State the role, company, and what you know about the stack or domain from the application context. Call out any gaps where the user should verify details before the interview.

### Behavioral Questions (STAR format)

Ask 3 behavioral questions relevant to the role and the user's SRE/Platform/DevOps background. After each answer:
- Give direct feedback: what landed, what was vague, what was missing
- Suggest one concrete improvement to the answer

Use the STAR format (Situation, Task, Action, Result). Focus on questions likely to surface in SRE or platform engineering interviews:
- Incident response and on-call experience
- Cross-team collaboration on reliability work
- Times the user drove a process or tooling change
- Handling competing priorities or pressure

### Technical Questions

Ask 3 technical questions based on the role's apparent stack. Wait for the answer to each before continuing.

After each answer, give direct feedback. Call out correct reasoning, missing depth, or incorrect assumptions.

Target areas based on what the application context suggests. Default to:
- Kubernetes operations and troubleshooting
- Terraform and infrastructure-as-code
- Observability (metrics, tracing, alerting)
- Incident response processes

### System Design (if applicable)

For senior roles or roles with a system design round, ask one system design question. Keep it scoped to 10-15 minutes of discussion.

Good defaults for SRE roles:
- "Design a deployment pipeline that minimizes blast radius for production changes."
- "How would you design an alerting system to reduce alert fatigue?"
- "Walk me through how you'd onboard a new service to your observability stack."

After the discussion, summarize what was covered well and what to think through further.

### Prep Summary

At the end of the session, output:
- **Role**: company and title
- **Strengths**: 2-3 things the user handled well in this session
- **Gaps to address**: specific areas to review before the interview
- **Questions to ask them**: 3 smart questions for the user to ask the interviewer

Output only to stdout. No file is saved unless the user explicitly asks.

## 7. Record Interview State

At the end of the practice session, emit a YAML block matching the interview-state schema (see schema comment at top of this file). Populate every field from this session:

- `app_id`: the application ID confirmed in Step 2
- `company` / `role`: from Step 2
- `last_session`: current ISO8601 timestamp (`date -u +%Y-%m-%dT%H:%M:%SZ`)
- `round`: ask "Which interview round is this prep for?" if not already established
- `questions_practiced`: list every question asked this session with its quality rating
- `feedback_given`: one-paragraph summary of the most important feedback given today
- `open_gaps`: carry forward unresolved gaps from prior state, add new ones surfaced today
- `next_session_focus`: one directive line for next time (e.g., "Tighten STAR structure on incident response answers")

Show the YAML block to the user, then run:

```bash
bash scripts/record-interview-state.sh <<'YAML'
<paste the emitted YAML block here>
YAML
```

If the write succeeds, confirm: "Interview state saved for <app_id>."
If it fails, show the error and offer to retry or let the user copy the YAML to save manually.
