##@ Packaging

.PHONY: pip-install
pip-install: ## Install daily-driver in editable mode (pip install -e .)
	@$(PIP) install -e .

.PHONY: pip-uninstall
pip-uninstall: ## Uninstall daily-driver from the current venv
	@$(PIP) uninstall -y daily-driver

# Installs with pipx's (or python3's) default Python, not detect-python.sh:
# that script prefers the CI-tested versions for dev venvs. pip and uv both
# refuse a Python below requires-python, so an old default fails the install.
.PHONY: install
install: ## Install daily-driver with pipx, or with pip --user when pipx is missing (refuses inside a venv)
	@if [ -n "$$VIRTUAL_ENV" ]; then \
		echo "Error: cannot install from inside a virtual environment."; \
		echo "Run 'deactivate' first, then re-run 'make install'."; \
		exit 1; \
	fi
	@( set -e; \
	   if [ -n "$(PIPX)" ]; then \
	     "$(PIPX)" install --force "git+file://$(PROJ_DIR)"; \
	     echo "Installed with pipx. Run 'pipx ensurepath' if daily-driver is not on PATH."; \
	   elif python3 -m pip --version >/dev/null 2>&1; then \
	     python3 -m pip install --user "git+file://$(PROJ_DIR)"; \
	     echo "User-site install complete. Add ~/Library/Python/<ver>/bin to PATH if needed."; \
	   else \
	     echo "Error: neither pipx nor pip is available. Install pipx with 'brew install pipx'."; \
	     exit 1; \
	   fi )
