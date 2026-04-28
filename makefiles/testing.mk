##@ Testing & Quality

.PHONY: test
test: ## Run the full test suite
	@.venv/bin/tox -e py311

.PHONY: test-cov
test-cov: ## Run tests with coverage report
	@.venv/bin/tox -e coverage

.PHONY: test-unit
test-unit: ## Run unit tests only
	@.venv/bin/tox -e test-unit

.PHONY: test-cli
test-cli: ## Run CLI tests only
	@.venv/bin/tox -e test-cli

.PHONY: test-e2e
test-e2e: ## Run end-to-end tests (timeout 300s)
	@.venv/bin/tox -e test-e2e

.PHONY: lint
lint: ## Run all linters
	@.venv/bin/tox -e lint

.PHONY: format
format: ## Auto-format code with black + isort
	@.venv/bin/tox -e format

.PHONY: type
type: ## Run mypy type check
	@.venv/bin/tox -e type
