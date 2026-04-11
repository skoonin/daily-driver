"""
Shared fixtures and module bootstrap for the daily-driver test suite.

scripts/scrape-jobs.py cannot be imported normally (hyphen in filename),
so we load it via importlib and register it in sys.modules as 'scrape_jobs'.
"""

import csv
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

from fixtures import CSV_HEADER, SAMPLE_CONFIG

# ── Module bootstrap ──────────────────────────────────────────────────────────

_script_path = Path(__file__).parent.parent / "scripts" / "scrape-jobs.py"
_spec = importlib.util.spec_from_file_location("scrape_jobs", _script_path)
assert _spec is not None, f"Could not load scrape_jobs: {_script_path} not found"
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
sys.modules["scrape_jobs"] = _module

# ── Shared fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def config() -> dict:
    return SAMPLE_CONFIG


@pytest.fixture
def config_file(tmp_path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(SAMPLE_CONFIG))
    return path


@pytest.fixture
def csv_path(tmp_path) -> Path:
    return tmp_path / "jobs.csv"


@pytest.fixture
def empty_csv(csv_path) -> Path:
    """CSV file with header only."""
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(CSV_HEADER)
    return csv_path


@pytest.fixture
def populated_csv(csv_path) -> Path:
    """CSV with two existing job rows."""
    def _row(**fields) -> list:
        return [fields.get(col, "") for col in CSV_HEADER]

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        writer.writerow(_row(**{
            "Company": "Acme", "Product/Purpose": "SaaS platform",
            "Role": "Senior SRE", "Location": "Remote", "Source": "HN",
            "Date Found": "2026-04-01", "Status": "found",
            "Link": "https://example.com/job1",
        }))
        writer.writerow(_row(**{
            "Company": "BetaCo", "Product/Purpose": "B2B analytics",
            "Role": "DevOps Lead", "Location": "Remote", "Source": "LinkedIn",
            "Date Found": "2026-04-02", "Status": "applied",
            "Date Applied": "2026-04-02", "Link": "https://example.com/job2",
        }))
    return csv_path
