##@ Setup

.PHONY: venv
venv: ## Create .venv if missing
	@if [ ! -d .venv ]; then \
		$(PYTHON) -m venv .venv; \
		echo "Created .venv"; \
	else \
		echo ".venv already exists"; \
	fi

.PHONY: deps
deps: venv ## Install runtime + dev dependencies (editable)
	@$(PIP) install --upgrade pip
	@$(PIP) install -e '.[dev]'

.PHONY: setup
setup: deps ## Full developer setup (venv + deps + pre-commit hooks)
	@.venv/bin/pre-commit install

.PHONY: status
status: ## Show environment status
	@echo "Daily Driver v$(VERSION)"
	@echo "Platform:     $(OS_NAME)-$(ARCH)"
	@echo "Python:       $(PYTHON)"
	@$(PYTHON) --version 2>/dev/null || echo "Python: not available"
	@if [ -d .venv ]; then echo ".venv:        present"; else echo ".venv:        missing — run 'make setup'"; fi
	@if [ -f .git/hooks/pre-commit ]; then echo "pre-commit:   installed"; else echo "pre-commit:   not installed"; fi
