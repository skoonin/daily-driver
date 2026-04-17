---
name: prep
description: Meeting prep - gather context relevant to an upcoming meeting
---

Prepare a brief for an upcoming meeting. Follow these steps in order:

## 0. Capture Current Time

```bash
echo "current_time=$(date +%H:%M) current_date=$(date +%Y-%m-%d)"
```

## 1. Get Calendar

```bash
bash scripts/gather-calendar.sh
```

## 2. Select Meeting

Present meetings happening within 3 hours of `current_time`. Ask the user which meeting to prep for -- they can pick by number or keyword.

If no meetings are found in the next 3 hours, show all meetings remaining after `current_time` and ask which one.

## 3. Gather Application Context

```bash
bash scripts/gather-applications.sh
```

## 4. Read Recent Notes

```bash
TODAY=$(date +%Y-%m-%d); DOW=$(date +%u); if [ "$DOW" = "1" ]; then YESTERDAY=$(date -v-3d +%Y-%m-%d); else YESTERDAY=$(date -v-1d +%Y-%m-%d); fi; bash scripts/gather-notes-range.sh "$YESTERDAY" "$TODAY" all
```

## 5. Load Voice Profile

```bash
bash scripts/read-voice-profile.sh
```

Apply the voice patterns when drafting any written communication in this session (talking points, follow-up email drafts, etc.).

## 6. Generate Prep Brief

Using the work-planner agent behavior, match the selected meeting topic to gathered context and generate a prep brief:

- **Meeting**: title, time, minutes until start
- **Relevant Applications**: applications matching the meeting topic (e.g., company name for an interview)
- **Context from Notes**: relevant excerpts from recent daily notes/plans
- **Suggested Talking Points**: 3-5 bullets of things to raise or follow up on

For interviews: surface the application details (company, role, date applied, status) and any notes. Include company research if available.

For networking calls: surface recent application activity and job search focus areas as conversation context.

If no direct matches are found between meeting topic and gathered data: state "no direct matches" and show recent Key Outcomes as general context.

Output the brief to stdout only. No file is saved (prep is ephemeral).
