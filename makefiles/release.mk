##@ Release

.PHONY: build
build: ## Build sdist + wheel into dist/
	@rm -rf dist build
	@.venv/bin/python -m pip install --quiet --upgrade build
	@.venv/bin/python -m build
	@ls -la dist/

.PHONY: release
release: ## Cut a release (usage: make release VERSION=X.Y.Z)
	@if [ -z "$(VERSION)" ]; then \
		echo "ERROR: VERSION is required. Usage: make release VERSION=X.Y.Z" >&2; \
		exit 1; \
	fi
	@case "$(VERSION)" in \
		[0-9]*.[0-9]*.[0-9]*) ;; \
		*) echo "ERROR: VERSION must be X.Y.Z (got: $(VERSION))" >&2; exit 1 ;; \
	esac
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "  Release v$(VERSION)"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo ""
	@echo "[1/6] Verifying release branch and clean working tree..."
	@branch=$$(git rev-parse --abbrev-ref HEAD); \
	if [ "$$branch" = "dev" ]; then \
		echo "ERROR: don't cut a release from the dev trunk (it carries the -dev marker)." >&2; \
		echo "       Merge dev -> main and run 'make release' from main." >&2; \
		exit 1; \
	fi
	@if [ -n "$$(git status --porcelain)" ]; then \
		echo "ERROR: working tree is dirty. Commit or stash first." >&2; \
		git status --short >&2; \
		exit 1; \
	fi
	@if git rev-parse "v$(VERSION)" >/dev/null 2>&1; then \
		echo "ERROR: tag v$(VERSION) already exists." >&2; \
		exit 1; \
	fi
	@echo "  OK: on $$(git rev-parse --abbrev-ref HEAD), clean tree, tag v$(VERSION) does not exist"
	@echo ""
	@echo "[2/6] Running test suite on py311 + py312..."
	@$(TOX) -e py311,py312
	@echo ""
	@echo "[3/6] Running install smoke test..."
	@rm -rf /tmp/daily-driver-release-smoke
	@python3 -m venv /tmp/daily-driver-release-smoke
	@/tmp/daily-driver-release-smoke/bin/pip install --quiet --upgrade pip
	@/tmp/daily-driver-release-smoke/bin/pip install --quiet .
	@/tmp/daily-driver-release-smoke/bin/daily-driver --version
	@/tmp/daily-driver-release-smoke/bin/python -c "import daily_driver; print('import OK, version:', daily_driver.__version__)"
	@rm -rf /tmp/daily-driver-release-smoke
	@echo "  OK: entry point resolves, import works"
	@echo ""
	@echo "[4/6] Building sdist + wheel..."
	@$(MAKE) --no-print-directory build
	@echo ""
	@echo "[5/6] Confirming release..."
	@echo "About to:"
	@echo "  - rewrite CHANGELOG.md: [Unreleased] -> [$(VERSION)] — $$(date +%Y-%m-%d)"
	@echo "  - bump __version__ to $(VERSION)"
	@echo "  - commit 'release: v$(VERSION)'"
	@echo "  - tag v$(VERSION) signed with the changelog section"
	@echo ""
	@printf "Proceed? [y/N] "
	@read ans; [ "$$ans" = "y" ] || [ "$$ans" = "Y" ] || { echo "Aborted."; exit 1; }
	@echo ""
	@echo "[6/6] Rewriting CHANGELOG, bumping version, committing, tagging..."
	@.venv/bin/python makefiles/_release_helper.py cut "$(VERSION)" "$$(date +%Y-%m-%d)"
	@git add CHANGELOG.md source/daily_driver/__init__.py
	@git commit -m "release: v$(VERSION)"
	@tag_msg=$$(.venv/bin/python makefiles/_release_helper.py tag-message "$(VERSION)"); \
	git tag -a "v$(VERSION)" -m "v$(VERSION)" -m "$$tag_msg"
	@echo ""
	@echo "  Release v$(VERSION) prepared."
	@echo "  Next step: make release-push"
	@echo ""

.PHONY: release-push
release-push: ## Push the release commit and tag (separate step, explicit)
	@current_tag=$$(git describe --tags --exact-match HEAD 2>/dev/null || echo ""); \
	if [ -z "$$current_tag" ]; then \
		echo "ERROR: HEAD is not tagged. Run 'make release VERSION=X.Y.Z' first." >&2; \
		exit 1; \
	fi; \
	echo "Pushing commit + $$current_tag to origin..."; \
	git push origin HEAD; \
	git push origin "$$current_tag"; \
	echo "  Pushed. CI release.yml will build and attach artifacts."
