##@ Packaging

.PHONY: pip-install
pip-install: ## Install daily-driver in editable mode (pip install -e .)
	@$(PIP) install -e .

.PHONY: pip-uninstall
pip-uninstall: ## Uninstall daily-driver from the current venv
	@$(PIP) uninstall -y daily-driver

# Installs with pipx's default Python, not detect-python.sh: that script
# prefers the CI-tested versions for dev venvs. pip and uv both refuse a Python
# below requires-python, so an old default fails the install. No pip --user
# fallback: Apple's python3 is below 3.11 and Homebrew's refuses pip installs
# outside a venv (PEP 668), so it cannot succeed on a stock Mac.
.PHONY: install
install: ## Install daily-driver with pipx (refuses inside a venv)
	@if [ -n "$$VIRTUAL_ENV" ]; then \
		echo "Error: cannot install from inside a virtual environment."; \
		echo "Run 'deactivate' first, then re-run 'make install'."; \
		exit 1; \
	fi
	@if [ -z "$(PIPX)" ]; then \
		echo "Error: pipx is not installed. Install it with 'brew install pipx', then run 'pipx ensurepath'."; \
		exit 1; \
	fi
	@"$(PIPX)" install --force "git+file://$(PROJ_DIR)"
	@echo "Installed with pipx. Run 'pipx ensurepath' if daily-driver is not on PATH."
