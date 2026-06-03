##@ Clean

.PHONY: clean
clean: ## Remove build artifacts and caches
	@rm -rf dist build source/*.egg-info htmlcov .pytest_cache .tox .mypy_cache .coverage
	@find . -type d -name __pycache__ -not -path './.venv/*' -prune -exec rm -rf {} \; 2>/dev/null || true
	@find . -type f -name '*.pyc' -not -path './.venv/*' -delete 2>/dev/null || true

.PHONY: distclean
distclean: clean ## Also remove .venv
	@rm -rf .venv
