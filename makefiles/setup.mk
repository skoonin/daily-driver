##@ Setup

.PHONY: setup
setup: .venv/touchfile ## Create .venv and install daily-driver (editable) with dev extras
	@echo "Basic setup complete. Activate with: source .venv/bin/activate"

.PHONY: setup-dev
setup-dev: setup ## Setup + install pre-commit hooks
	@.venv/bin/pre-commit install
	@echo "Development environment ready (pre-commit hooks installed)"

.PHONY: setup-force
setup-force: ## Recreate .venv from scratch
	@$(MAKE) clean-venv
	@$(MAKE) setup-dev

.PHONY: deps-update
deps-update: .venv/touchfile ## Upgrade pip + reinstall editable with dev extras
	@.venv/bin/pip install --upgrade pip setuptools wheel --quiet
	@.venv/bin/pip install -e '.[dev]' --upgrade --quiet
	@echo "Dependencies updated"

.PHONY: clean-venv
clean-venv: ## Remove .venv
	@rm -rf .venv
	@echo "Virtual environment removed"

.PHONY: status
status: ## Show environment status
	@echo "Daily Driver v$(VERSION)"
	@echo "Platform:     $(OS_NAME)-$(ARCH)"
	@echo "Python:       $(PYTHON)"
	@$(PYTHON) --version 2>/dev/null || echo "Python: not available"
	@if [ -d .venv ]; then echo ".venv:        present"; else echo ".venv:        missing — run 'make setup'"; fi
	@if [ -f .git/hooks/pre-commit ]; then echo "pre-commit:   installed"; else echo "pre-commit:   not installed"; fi

# Re-run setup-venv.sh whenever pyproject.toml changes.
.venv/touchfile: pyproject.toml
	@bash .ci-tools/setup-venv.sh
	@touch $@
