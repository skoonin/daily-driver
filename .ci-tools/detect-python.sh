#!/usr/bin/env bash
# Detect a usable Python >=3.11 interpreter for Makefile targets.
# Prints the interpreter path on stdout; exits 1 if none found.

set -eu

for candidate in python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
            command -v "$candidate"
            exit 0
        fi
    fi
done

exit 1
