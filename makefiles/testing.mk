##@ Testing & Quality

.PHONY: test
test: ## Run the full tox envlist (lint + type + py311 + py312 + coverage), matches CI
	@$(TOX)

.PHONY: test-quick
test-quick: ## Run pytest under py311 only — fast inner loop
	@$(TOX) -e py311

.PHONY: test-quick-parallel
test-quick-parallel: ## Run pytest in parallel via pytest-xdist (-n auto)
	@$(TOX) -e test-parallel

.PHONY: test-cov
test-cov: ## Run tests with coverage report
	@$(TOX) -e coverage

.PHONY: test-unit
test-unit: ## Run unit tests only
	@$(TOX) -e test-unit

.PHONY: test-cli
test-cli: ## Run CLI tests only
	@$(TOX) -e test-cli

.PHONY: test-e2e
test-e2e: ## Run end-to-end tests (timeout 300s)
	@$(TOX) -e test-e2e

.PHONY: lint
lint: check-config-template ## Run all linters
	@$(TOX) -e lint

.PHONY: config-template
config-template: ## Regenerate .dd-config.yaml.j2 from config_models.py
	@PYTHONPATH=source $(PYTHON) -m daily_driver.core.config_template > source/daily_driver/resources/templates/.dd-config.yaml.j2
	@echo "regenerated .dd-config.yaml.j2"

.PHONY: check-config-template
check-config-template: ## Verify template matches what codegen would produce
	@TMP=$$(mktemp -t dd-config-template.XXXXXX) && trap 'rm -f "$$TMP"' EXIT; \
		PYTHONPATH=source $(PYTHON) -m daily_driver.core.config_template > "$$TMP" && \
		diff -u source/daily_driver/resources/templates/.dd-config.yaml.j2 "$$TMP" \
		|| { echo "config template is stale; run 'make config-template' to regenerate"; exit 1; }

.PHONY: format
format: ## Auto-format code with black + isort
	@$(TOX) -e format

.PHONY: type
type: ## Run mypy type check
	@$(TOX) -e type
