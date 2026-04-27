# Daily Driver — ADHD-aware personal productivity assistant
# Requires Python 3.11+ and macOS (arm64 target for v0.1.0)

.ONESHELL:
SHELL := /bin/bash
.SHELLFLAGS := -o pipefail -c
.DEFAULT_GOAL := help

# Single-source version from source/daily_driver/__init__.py
VERSION := $(shell grep '^__version__' source/daily_driver/__init__.py 2>/dev/null | cut -d'"' -f2)

# Platform detection
OS_NAME ?= $(strip $(shell uname -s | tr '[:upper:]' '[:lower:]'))
RAW_ARCH := $(strip $(shell uname -m | tr '[:upper:]' '[:lower:]'))
ifeq ($(RAW_ARCH),x86_64)
    ARCH := amd64
else ifeq ($(RAW_ARCH),aarch64)
    ARCH := arm64
else
    ARCH := $(RAW_ARCH)
endif

# Python detection: prefer .venv, then detect-python.sh, then python3
PYTHON := $(shell if [ -f .venv/bin/python ]; then echo .venv/bin/python; \
    else .ci-tools/detect-python.sh 2>/dev/null || echo python3; fi)
PIP := $(PYTHON) -m pip

PROJ_DIR := $(shell pwd)

include makefiles/clean.mk
include makefiles/install.mk
include makefiles/setup.mk
include makefiles/testing.mk
include makefiles/release.mk

# -----------------------------------------------------------------
# Help target
# -----------------------------------------------------------------

.PHONY: help
help: ## Show this help
	@echo ""
	@echo "Daily Driver (v$(VERSION))"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo ""
	@printf "  %-18s %s\n" "Version:"  "$(VERSION)"
	@printf "  %-18s %s\n" "Platform:" "$(OS_NAME)-$(ARCH)"
	@printf "  %-18s %s\n" "Python:"   "$(PYTHON)"
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo ""
	@echo "Available targets:"
	@awk 'BEGIN {FS = ":.*##"} \
		/^##@/ {printf "\n\033[1m%s\033[0m\n", substr($$0, 5)} \
		/^[a-zA-Z0-9_-]+:.*?##/ {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
