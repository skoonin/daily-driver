##@ Clean

.PHONY: clean
clean: ## Remove build artifacts and caches
	@$(TOX) -e clean 2>/dev/null || true
	@rm -rf dist build *.egg-info htmlcov .pytest_cache .tox .coverage
	@find . -type d -name __pycache__ -prune -exec rm -rf {} \; 2>/dev/null || true
	@find . -type f -name '*.pyc' -delete 2>/dev/null || true

.PHONY: distclean
distclean: clean ## Also remove .venv
	@rm -rf .venv
