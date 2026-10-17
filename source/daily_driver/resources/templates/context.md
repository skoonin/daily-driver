# Personal context

Narrative prose about who you are, what you do, and what you value. Structured
settings live in `.dd-config.yaml`.
{% if "job_search" in plugins %}

This file is read in full and injected into each job-fit evaluation during
`daily-driver jobs run`, so the fit score and notes reflect your real
background — not just the one-line persona in `.dd-config.yaml`. Keep the
sections below filled in, current, and short: every job review sends the whole
file to the AI. day-start and day-end read it too, so keep Current focus and
Recurring Tasks even though they don't help judge fit.
{% endif %}

## About me

(Your background, current role/level{% if "job_search" in plugins %}, and what you're looking for{% endif %}.)

## Experience and skills

(The systems you've built and the skills you're strongest in.{% if "job_search" in plugins %} This is what
fit is scored against. Also note gaps you're deliberately not chasing, so roles
centered on them score lower.{% endif %})
{% if "job_search" in plugins %}

## Location preferences

(Where you want to work, ranked. Note remote vs. in-office, cities/countries,
and any visa constraints. Drives the location-fit part of the score.)
{% endif %}

## Current focus

(What you're working on or prioritizing this quarter/month.)

## Recurring Tasks

(Tasks that repeat, one per line. day-start adds each one to your plan.)
