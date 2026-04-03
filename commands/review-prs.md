---
name: review-prs
description: Check for pending PR reviews and open review sessions
---

Check for PRs requesting your review and open review sessions for selected ones.

## 1. Capture Current Time

```bash
echo "current_time=$(date +%H:%M) current_date=$(date +%Y-%m-%d)"
```

## 2. Gather PR Review Requests

```bash
bash scripts/gather-prs.sh
```

## 3. Present Review Requests

From the output, extract only the "PRs Requesting My Review" sections. Present each PR with:
- Number and title
- Repository
- Author
- URL

Number them for selection. If no review requests are pending, say so and exit.

## 4. Select PRs to Review

Ask: "Which PRs do you want to review? (numbers, 'all', or 'none')"

## 5. Launch Review Sessions

For each selected PR, open a new iTerm2 window with claude in review mode (sonnet, high effort):

```bash
bash scripts/open-iterm-window.sh "cd ~/git/reviews && claude --model sonnet --effort high '/sk-review PR_URL'"
```

Replace `PR_URL` with the actual GitHub PR URL for each selected PR.

Report how many review sessions were launched.
