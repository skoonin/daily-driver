SHELL := /bin/bash
.ONESHELL:
.PHONY: help deps install uninstall launchd-install launchd-uninstall launchd-start setup status \
       day-start day-end check-in standup week-end month-end prep focus

PROJ_DIR := $(shell pwd)

# Claude invocation matching clde/cldelt launch modes
# clde  = sonnet, medium effort, sonnet subagents
# cldelt = sonnet, low effort, haiku subagents
CLDE  = CLAUDE_CODE_SUBAGENT_MODEL=sonnet claude --model sonnet --effort medium
CLDELT = CLAUDE_CODE_SUBAGENT_MODEL=haiku claude --model sonnet --effort low
CLAUDE_DIR := $(PROJ_DIR)/.claude

##@ Setup

help:  ## Display this help
	@echo ""
	@printf "\033[1mDaily Driver\033[0m - Job search planning and daily accountability\n"
	@echo ""
	@echo "Powered by Claude Code. Tracks applications, follow-ups, and calendar"
	@echo "to help you plan your day, stay on track, and report progress."
	@echo ""
	@printf "\033[1mQuick Start\033[0m\n"
	@echo "  1. make setup          Install deps, link commands, verify auth"
	@echo "  2. make day-start      Plan your day (run each morning)"
	@echo "  3. make check-in       Review progress (auto-triggered via launchd)"
	@echo "  4. make day-end        Summarize the day (run before signing off)"
	@echo ""
	@printf "\033[1mTypical Day\033[0m\n"
	@echo "  09:00  make day-start     Gather context, build plan, block calendar"
	@echo "  11:00  make check-in      Review progress, check follow-ups"
	@echo "  13:00  make focus ARGS=90 Suppress check-ins for 90 min deep work"
	@echo "  14:30  (auto check-in)    iTerm2 opens with /check-in after focus ends"
	@echo "  16:30  make day-end       Compare plan vs actual, write daily notes"
	@echo "  Fri    make week-end      Weekly rollup summary"
	@echo ""
	@printf "\033[1mNotes\033[0m\n"
	@echo "  - All commands also work as /slash-commands inside Claude Code"
	@echo "  - Sessions are named for easy /resume (e.g. day-start-2026-04-02)"
	@echo "  - make standup and make focus run headless (sonnet, low effort)"
	@echo ""
	@printf "\033[1mConfiguration\033[0m: config.yaml  (tracker settings, calendar, check-in times)\n"
	@printf "\033[1mOutput\033[0m:        %s\n" "$$(yq '.output_dir // "~/git/daily-notes"' config.yaml 2>/dev/null)"
	@echo ""
	@awk 'BEGIN {FS = ":.*##"; printf "\033[1mTargets\033[0m\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

BREW_DEPS := jq yq ical-buddy terminal-notifier

status:  ## Check installation status of all dependencies and services
	@echo "=== Dependencies ==="
	@for pkg in jq yq; do \
		if command -v "$$pkg" &>/dev/null; then \
			printf "  \033[32m%-20s\033[0m %s\n" "$$pkg" "$$($$pkg --version 2>&1 | head -1)"; \
		else \
			printf "  \033[31m%-20s\033[0m %s\n" "$$pkg" "not installed"; \
		fi; \
	done
	@if command -v icalBuddy &>/dev/null; then \
		printf "  \033[32m%-20s\033[0m %s\n" "icalBuddy" "installed"; \
	else \
		printf "  \033[31m%-20s\033[0m %s\n" "icalBuddy" "not installed"; \
	fi
	@if command -v terminal-notifier &>/dev/null; then \
		printf "  \033[32m%-20s\033[0m %s\n" "terminal-notifier" "installed (optional)"; \
	else \
		printf "  \033[33m%-20s\033[0m %s\n" "terminal-notifier" "not installed (optional)"; \
	fi
	@if command -v claude &>/dev/null; then \
		printf "  \033[32m%-20s\033[0m %s\n" "claude" "$$(claude --version 2>&1 | head -1)"; \
	else \
		printf "  \033[31m%-20s\033[0m %s\n" "claude" "not installed"; \
	fi
	@echo ""
	@echo "=== Symlinks ==="
	@if [ -L "$(CLAUDE_DIR)/CLAUDE.md" ]; then echo "  commands: linked"; else echo "  commands: not linked (run make install)"; fi
	@if [ -L "$(CLAUDE_DIR)/settings.local.json" ]; then echo "  settings.local.json: linked"; else echo "  settings.local.json: not linked (run make install)"; fi
	@echo ""
	@echo "=== LaunchAgent ==="
	@if [ -f "$$HOME/Library/LaunchAgents/com.daily-driver.checkin.plist" ]; then \
		if launchctl list com.daily-driver.checkin &>/dev/null; then \
			printf "  \033[32m%-20s\033[0m %s\n" "check-in" "installed and running"; \
		else \
			printf "  \033[33m%-20s\033[0m %s\n" "check-in" "installed but not loaded (run make launchd-start)"; \
		fi; \
	else \
		printf "  \033[31m%-20s\033[0m %s\n" "check-in" "not installed (run make launchd-install)"; \
	fi

deps:  ## Install all dependencies (Homebrew + claude)
	@echo "=== Homebrew packages ==="
	@for pkg in $(BREW_DEPS); do \
		if brew list "$$pkg" &>/dev/null; then \
			echo "  $$pkg: already installed"; \
		else \
			echo "  $$pkg: installing..."; \
			brew install "$$pkg"; \
		fi; \
	done
	@echo ""
	@echo "=== Claude Code ==="
	@if command -v claude &>/dev/null; then \
		echo "  claude: already installed ($$(claude --version 2>&1 | head -1))"; \
	else \
		echo "  claude: installing via official installer..."; \
		curl -fsSL https://claude.ai/install.sh | bash; \
	fi

setup: deps install  ## Full setup: install deps, symlink commands, verify auth
	@echo ""
	@echo "=== Calendar (icalBuddy) ==="
	@if command -v icalBuddy &>/dev/null; then \
		icalBuddy calendars 2>&1 | head -20 || echo "  calendar access failed"; \
	else \
		echo "  icalBuddy not installed"; \
	fi
	@echo ""
	@echo "=== Output Directory ==="
	@OUTPUT_DIR=$$(yq '.output_dir' config.yaml); \
	OUTPUT_DIR="$${OUTPUT_DIR/#\~/$${HOME}}"; \
	if [ -d "$$OUTPUT_DIR" ]; then \
		echo "  $$OUTPUT_DIR: OK"; \
	else \
		echo "  $$OUTPUT_DIR: creating..."; \
		mkdir -p "$$OUTPUT_DIR"; \
		git -C "$$OUTPUT_DIR" init 2>/dev/null; \
	fi
	@echo ""
	@echo "=== Sync Repos ==="
	@yq '.sync_repos[]' config.yaml 2>/dev/null | while IFS= read -r repo; do \
		repo="$${repo/#\~/$${HOME}}"; \
		if [ -d "$$repo/.git" ]; then \
			printf "  %-40s %s\n" "$$repo:" "OK"; \
		else \
			printf "  %-40s %s\n" "$$repo:" "NOT FOUND"; \
		fi; \
	done
	@echo ""
	@echo "=== LaunchAgent ==="
	@if launchctl list com.daily-driver.checkin &>/dev/null 2>&1; then \
		echo "  check-in: running"; \
	elif [ -f "$$HOME/Library/LaunchAgents/com.daily-driver.checkin.plist" ]; then \
		echo "  check-in: installed but not loaded (run make launchd-start)"; \
	else \
		echo "  check-in: not installed (run make launchd-install)"; \
	fi
	@echo ""
	@echo "Setup complete. Run 'make day-start' to begin."

install:  ## Symlink commands and agents into .claude/ for Claude Code
	@mkdir -p $(CLAUDE_DIR)/commands $(CLAUDE_DIR)/agents
	@for f in $(PROJ_DIR)/commands/*.md; do \
		name=$$(basename "$$f"); \
		ln -sf "$$f" "$(CLAUDE_DIR)/commands/$$name"; \
		echo "  linked commands/$$name"; \
	done
	@for f in $(PROJ_DIR)/agents/*.md; do \
		name=$$(basename "$$f"); \
		ln -sf "$$f" "$(CLAUDE_DIR)/agents/$$name"; \
		echo "  linked agents/$$name"; \
	done
	@ln -sf $(PROJ_DIR)/CLAUDE.md $(CLAUDE_DIR)/CLAUDE.md
	@echo "  linked CLAUDE.md"
	@ln -sf $(PROJ_DIR)/settings.json $(CLAUDE_DIR)/settings.local.json
	@echo "  linked settings.local.json"
	@echo "Install complete. Commands and agents are now available in Claude Code."

uninstall:  ## Remove symlinks from .claude/
	@for f in $(PROJ_DIR)/commands/*.md; do \
		rm -f "$(CLAUDE_DIR)/commands/$$(basename $$f)"; \
	done
	@for f in $(PROJ_DIR)/agents/*.md; do \
		rm -f "$(CLAUDE_DIR)/agents/$$(basename $$f)"; \
	done
	@rm -f $(CLAUDE_DIR)/CLAUDE.md
	@echo "Uninstalled. Symlinks removed from .claude/"

##@ Automation

launchd-install:  ## Install check-in notification LaunchAgent
	@bash scripts/launchd-install.sh install

launchd-start:  ## Load LaunchAgent if installed but not running
	@PLIST="$$HOME/Library/LaunchAgents/com.daily-driver.checkin.plist"; \
	if [ ! -f "$$PLIST" ]; then \
		echo "LaunchAgent not installed. Run: make launchd-install"; \
		exit 1; \
	fi; \
	if launchctl list com.daily-driver.checkin &>/dev/null; then \
		echo "LaunchAgent already running"; \
	else \
		launchctl load "$$PLIST"; \
		echo "LaunchAgent loaded"; \
	fi

launchd-uninstall:  ## Remove check-in notification LaunchAgent
	@bash scripts/launchd-install.sh uninstall

##@ Workflow

day-start:  ## Morning planning: gather context and plan the day
	@$(CLDE) --agent work-planner -n "day-start-$$(date +%Y-%m-%d)" '/day-start'

day-end:  ## End of day: compare plan vs actual, write daily notes
	@$(CLDE) --agent work-planner -n "day-end-$$(date +%Y-%m-%d)" '/day-end'

check-in:  ## Mid-day check-in: review progress against plan
	@$(CLDE) --agent work-planner -n "check-in-$$(date +%Y-%m-%d-%H%M)" '/check-in'

standup:  ## Generate async standup summary to clipboard
	@$(CLDELT) -p '/standup' | pbcopy
	@echo "Standup copied to clipboard"

week-end:  ## Weekly rollup: summarize the week into a report
	@$(CLDE) --agent work-planner -n "week-end-$$(date +%Y-W%V)" '/week-end'

month-end:  ## Monthly rollup: summarize the month into a report
	@$(CLDE) --agent work-planner -n "month-end-$$(date +%Y-%m)" '/month-end'

prep:  ## Meeting prep: gather context for an upcoming meeting
	@$(CLDE) --agent work-planner -n "prep-$$(date +%Y-%m-%d-%H%M)" '/prep $(ARGS)'

focus:  ## Toggle focus mode (usage: make focus ARGS="90")
	@$(CLDELT) -p '/focus $(ARGS)'

