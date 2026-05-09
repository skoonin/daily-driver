##@ Testing & Quality

.PHONY: test
test: ## Run the full tox envlist (lint + type + py311 + py312 + coverage), matches CI
	@$(TOX)

.PHONY: test-quick
test-quick: ## Run pytest under py311 only — fast inner loop
	@$(TOX) -e py311

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
lint: ## Run all linters
	@$(TOX) -e lint

.PHONY: format
format: ## Auto-format code with black + isort
	@$(TOX) -e format

.PHONY: type
type: ## Run mypy type check
	@$(TOX) -e type
