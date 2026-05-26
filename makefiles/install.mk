##@ Packaging

.PHONY: pip-install
pip-install: ## Install daily-driver in editable mode (pip install -e .)
	@$(PIP) install -e .

.PHONY: pip-uninstall
pip-uninstall: ## Uninstall daily-driver from the current venv
	@$(PIP) uninstall -y daily-driver

.PHONY: install
install: ## Install daily-driver to user site (refuses inside a venv)
	@if [ -n "$$VIRTUAL_ENV" ]; then \
		echo "Error: cannot install to user site from inside a virtual environment."; \
		echo "Run 'deactivate' first, then re-run 'make install'."; \
		exit 1; \
	fi
	@HOST_PYTHON=$$(bash .ci-tools/detect-python.sh) && \
		"$$HOST_PYTHON" -m pip install --user "git+file://$(PROJ_DIR)"
	@echo "User-site install complete. Add ~/Library/Python/<ver>/bin to PATH if needed."
