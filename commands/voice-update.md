---
name: voice-update
description: Update voice profile with observations from approved writing or explicit feedback
---

Update the writing voice profile. This keeps the profile current so future drafts match Shawn's voice.

## 0. Read Voice Profile

Read the current profile first:
```bash
OUTPUT_DIR=$(yq '.output_dir' /Users/shawnk/git/daily-driver/config.yaml); OUTPUT_DIR="${OUTPUT_DIR/#\~/$HOME}"; cat "$OUTPUT_DIR/voice-profile.md"
```

## 1. Determine Input Mode

Check if the user provided an argument (a file path, a quoted passage, or explicit feedback):

- **File path** (e.g., `/path/to/cover-letter.md`): read the file, then analyze it for voice patterns
- **Quoted passage**: analyze the passage directly
- **Explicit feedback** (e.g., "I don't like how formal this sounds", "more of this tone"): treat as a direct instruction to update a specific section of the profile
- **No argument**: ask the user to paste the writing sample or describe the feedback

For file input:
```bash
cat "$ARGUMENT"
```

## 2. Analyze and Extract Observations

For writing samples (file or quoted passage), extract observations across these dimensions:

- **Structural patterns**: how the piece is organized, paragraph sequencing, length
- **Language patterns**: word choices, sentence construction, connectors used or avoided
- **Tone markers**: degree of formality, confidence level, presence/absence of hedges
- **Specificity level**: ratio of concrete detail (numbers, tool names, outcomes) to abstractions
- **Gaps or deviations**: anything that differs from the existing profile -- is this a one-off or a new preference?

For explicit feedback, map the feedback directly to the relevant profile section.

Keep the analysis tight. Three to five observations is enough. Do not restate what is already in the profile.

## 3. Present Proposed Updates

Show the user what you plan to add or change before writing anything:

1. Summarize the observations (brief bullets)
2. State which section(s) of the voice profile you propose to update
3. Show the specific addition or change

Ask: "Does this capture it? Anything to revise before I update the profile?"

## 4. Apply Updates

After confirmation, update the voice profile:

```bash
OUTPUT_DIR=$(yq '.output_dir' /Users/shawnk/git/daily-driver/config.yaml); OUTPUT_DIR="${OUTPUT_DIR/#\~/$HOME}"; echo "$OUTPUT_DIR/voice-profile.md"
```

Write the updated file to the path shown above. Apply only what was confirmed in step 3:

- Add new observations to the relevant existing section (do not create new top-level sections unless clearly warranted)
- If adding an approved sample, append it to the Approved Samples section with file path and a brief note on what it demonstrates
- Update the Update Log table at the bottom with today's date, a one-line summary of what changed, and the source (file name or "feedback")

Do not rewrite sections that were not part of the confirmed update. Preserve existing content exactly.
