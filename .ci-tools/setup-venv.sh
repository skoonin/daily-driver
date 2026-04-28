#!/usr/bin/env bash
# Create .venv and install daily-driver in editable mode with dev extras.
# Always invokes .venv/bin/pip directly — never relies on the host pip,
# which avoids PEP 668 externally-managed-environment errors on Homebrew.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

if ! PYTHON=$(bash .ci-tools/detect-python.sh); then
    echo "Error: Could not find Python 3.11+." >&2
    exit 1
fi

echo "Using Python: ${PYTHON}"

if [ ! -d .venv ]; then
    echo "Creating virtual environment..."
    "${PYTHON}" -m venv .venv
fi

echo "Upgrading pip, setuptools, wheel..."
.venv/bin/pip install --upgrade pip setuptools wheel

echo "Installing daily-driver (editable) with dev extras..."
.venv/bin/pip install -e '.[dev]'

echo "Virtual environment ready at .venv/"
