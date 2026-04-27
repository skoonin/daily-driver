##@ Packaging

.PHONY: pip-install
pip-install: ## Install daily-driver in editable mode (pip install -e .)
	@$(PIP) install -e .

.PHONY: pip-uninstall
pip-uninstall: ## Uninstall daily-driver from the current venv
	@$(PIP) uninstall -y daily-driver
