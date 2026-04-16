#!/bin/sh
# Injects current local time into Claude context on every user message.
# Used by the UserPromptSubmit hook in settings.local.json.
CURRENT_TIME=$(date '+%H:%M %Z on %A %B %d, %Y')
printf '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"Current time: %s"}}\n' "$CURRENT_TIME"
