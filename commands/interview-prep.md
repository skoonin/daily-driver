---
name: interview-prep
description: Interview preparation - practice questions and gather role context
---

Prepare for an upcoming interview. Follow these steps in order.

## 0. Capture Current Time

```bash
echo "current_time=$(date +%H:%M) current_date=$(date +%Y-%m-%d)"
```

## 1. Identify the Target Role

If `$ARGUMENTS` is set, use it as the company or role to prep for. Otherwise, gather applications and ask:

```bash
bash scripts/gather-applications.sh
```

Show the active applications and ask: "Which company or role are you prepping for? You can paste a job title, company name, or application ID."

## 2. Load Role Context

Read recent notes to surface any prior prep or interview context for this role:

```bash
TODAY=$(date +%Y-%m-%d); DOW=$(date +%u); if [ "$DOW" = "1" ]; then SINCE=$(date -v-7d +%Y-%m-%d); else SINCE=$(date -v-3d +%Y-%m-%d); fi; bash scripts/gather-notes-range.sh "$SINCE" "$TODAY" all
```

## 3. Read User Profile

```bash
cat context.md
```

## 4. Practice Session

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
