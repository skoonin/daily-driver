.ONESHELL:
.PHONY: help install uninstall

PROJ_DIR := $(shell pwd)
CLAUDE_DIR := $(PROJ_DIR)/.claude

##@ Setup

help:  ## Display this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

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
	@echo "Install complete. Commands and agents are now available in Claude Code."

uninstall:  ## Remove symlinks from .claude/
	@rm -f $(CLAUDE_DIR)/commands/day-start.md $(CLAUDE_DIR)/commands/day-end.md $(CLAUDE_DIR)/commands/setup.md
	@rm -f $(CLAUDE_DIR)/agents/work-planner.md
	@rm -f $(CLAUDE_DIR)/CLAUDE.md
	@echo "Uninstalled. Symlinks removed from .claude/"
