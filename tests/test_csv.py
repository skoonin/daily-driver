"""Tests for CSV helpers: load_existing_jobs(), append_jobs(), and _migrate_legacy_header()."""

import csv
from pathlib import Path

import pytest
import scrape_jobs as sj

from fixtures import CSV_HEADER


class TestLoadExistingJobs:
    def test_missing_file_returns_empty_state(self, csv_path):
        urls, keys, header = sj.load_existing_jobs(csv_path)
        assert urls == set()
        assert keys == set()
        assert header == []

    def test_empty_file_returns_empty_state(self, csv_path):
        csv_path.write_text("")
        urls, keys, header = sj.load_existing_jobs(csv_path)
        assert urls == set()
        assert keys == set()
        assert header == []

    def test_header_only_file_returns_zero_urls(self, empty_csv):
        urls, keys, header = sj.load_existing_jobs(empty_csv)
        assert urls == set()
        assert keys == set()
        assert header == CSV_HEADER

    def test_populated_csv_returns_known_urls(self, populated_csv):
        urls, _, _ = sj.load_existing_jobs(populated_csv)
        assert "https://example.com/job1" in urls
        assert "https://example.com/job2" in urls
        assert len(urls) == 2

    def test_populated_csv_returns_known_keys(self, populated_csv):
        _, keys, _ = sj.load_existing_jobs(populated_csv)
        assert sj.dedup_key("Acme", "Senior SRE") in keys
        assert sj.dedup_key("BetaCo", "DevOps Lead") in keys
        assert len(keys) == 2

    def test_populated_csv_returns_header(self, populated_csv):
        _, _, header = sj.load_existing_jobs(populated_csv)
        assert "Link" in header
        assert header[0] == "Status"
        assert "#" not in header

    def test_skips_blank_rows(self, csv_path):
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADER)
            row = [""] * len(CSV_HEADER)
            row[CSV_HEADER.index("Link")] = "https://example.com/1"
            row[CSV_HEADER.index("Company")] = "Acme"
            row[CSV_HEADER.index("Role")] = "SRE"
            writer.writerow(row)
            f.write("\n")  # blank line between data rows
            row2 = [""] * len(CSV_HEADER)
            row2[CSV_HEADER.index("Link")] = "https://example.com/2"
            row2[CSV_HEADER.index("Company")] = "BetaCo"
            row2[CSV_HEADER.index("Role")] = "DevOps"
            writer.writerow(row2)
        urls, keys, _ = sj.load_existing_jobs(csv_path)
        assert len(urls) == 2
        assert len(keys) == 2

    def test_rows_without_link_are_excluded_from_urls(self, csv_path):
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADER)
            row = [""] * len(CSV_HEADER)
            row[CSV_HEADER.index("Company")] = "Acme"
            # Link column intentionally blank
            writer.writerow(row)
        urls, _, _ = sj.load_existing_jobs(csv_path)
        assert urls == set()

    def test_dedup_key_normalizes_case_and_whitespace(self, csv_path):
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADER)
            row = [""] * len(CSV_HEADER)
            row[CSV_HEADER.index("Company")] = "  ACME Corp  "
            row[CSV_HEADER.index("Role")] = "  Senior  SRE  "
            row[CSV_HEADER.index("Link")] = "https://example.com/1"
            writer.writerow(row)
        _, keys, _ = sj.load_existing_jobs(csv_path)
        assert sj.dedup_key("ACME Corp", "Senior  SRE") in keys


class TestAppendJobs:
    def test_empty_job_list_writes_nothing(self, empty_csv):
        written = sj.append_jobs(empty_csv, [], CSV_HEADER)
        assert written == 0

    def test_single_job_appended(self, empty_csv):
        job = {
            "company": "Acme",
            "role": "Senior SRE",
            "location": "Remote",
            "url": "https://example.com/job99",
            "source": "HN",
        }
        written = sj.append_jobs(empty_csv, [job], CSV_HEADER)
        assert written == 1

        with open(empty_csv, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))

        data = rows[1]
        assert data[CSV_HEADER.index("Company")] == "Acme"
        assert data[CSV_HEADER.index("Role")] == "Senior SRE"
        assert data[CSV_HEADER.index("Status")] == "found"
        assert data[CSV_HEADER.index("Link")] == "https://example.com/job99"
        assert "#" not in CSV_HEADER

    def test_returns_count_of_written_rows(self, empty_csv):
        jobs = [
            {"company": "A", "url": "https://a.com"},
            {"company": "B", "url": "https://b.com"},
        ]
        written = sj.append_jobs(empty_csv, jobs, CSV_HEADER)
        assert written == 2

    def test_product_column_gets_default_placeholder(self, empty_csv):
        job = {"company": "Acme", "url": "https://x.com"}
        sj.append_jobs(empty_csv, [job], CSV_HEADER)

        with open(empty_csv, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))

        product = rows[1][CSV_HEADER.index("Product/Purpose")]
        assert "auto-scraped" in product

    def test_handles_partial_header(self, csv_path):
        """append_jobs with a header missing some expected columns writes only columns present."""
        partial_header = ["Status", "Company", "Role", "Link"]
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(partial_header)
        job = {"company": "Acme", "role": "SRE", "url": "https://x.com", "source": "HN"}
        written = sj.append_jobs(csv_path, [job], partial_header)
        assert written == 1
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        data = rows[1]
        assert data[partial_header.index("Company")] == "Acme"
        assert data[partial_header.index("Role")] == "SRE"
        assert data[partial_header.index("Link")] == "https://x.com"
        assert data[partial_header.index("Status")] == "found"
        assert len(data) == len(partial_header)

    def test_roundtrip_load_then_append(self, empty_csv):
        """Rows written by append_jobs are picked up by load_existing_jobs."""
        job = {
            "company": "Acme",
            "role": "Senior SRE",
            "url": "https://roundtrip.com",
            "source": "HN",
        }
        sj.append_jobs(empty_csv, [job], CSV_HEADER)

        urls, keys, _ = sj.load_existing_jobs(empty_csv)
        assert "https://roundtrip.com" in urls
        assert sj.dedup_key("Acme", "Senior SRE") in keys

    def test_append_jobs_writes_status_first(self, empty_csv):
        """Newly appended rows have Status in column 0, no '#' column present."""
        job = {"company": "Acme", "role": "SRE", "url": "https://x.com/1"}
        sj.append_jobs(empty_csv, [job], CSV_HEADER)
        with open(empty_csv) as f:
            rows = list(csv.reader(f))
        assert rows[0][0] == "Status"
        assert "#" not in rows[0]
        assert rows[1][0] == "found"


class TestNewHeaderShape:
    def test_canonical_header_status_first_no_hash(self):
        """Canonical header has Status at index 0 and no '#' column."""
        assert sj.CANONICAL_HEADER[0] == "Status"
        assert "#" not in sj.CANONICAL_HEADER
        assert len(sj.CANONICAL_HEADER) == 13

    def test_csv_header_fixture_matches_canonical(self):
        assert CSV_HEADER == sj.CANONICAL_HEADER


class TestMigrateLegacyHeader:
    def test_migration_from_legacy_header(self, tmp_path):
        """A jobs.csv with the old '#'-first header is rewritten on load."""
        legacy = tmp_path / "jobs.csv"
        legacy.write_text(
            '#,Company,Product/Purpose,Role,Comp,Location,Fit,GD Rating,Source,'
            'Date Found,Status,Date Applied,Link,Notes\n'
            '1,Acme,Widgets,SRE,,,,,RemoteOK,2026-04-01,applied,2026-04-02,'
            'https://example.com/1,\n'
        )
        with open(legacy) as f:
            legacy_header = next(csv.reader(f))
        new_header = sj._migrate_legacy_header(legacy, legacy_header)

        assert new_header[0] == "Status"
        assert "#" not in new_header
        backups = list(tmp_path.glob("jobs.csv.bak.*"))
        assert len(backups) == 1
        with open(legacy) as f:
            rows = list(csv.reader(f))
        assert rows[0][0] == "Status"
        assert rows[1][0] == "applied"  # first data row's Status column

    def test_migration_is_idempotent_on_current_header(self, tmp_path):
        """No backup is created when the header is already current."""
        legacy = tmp_path / "jobs.csv"
        legacy.write_text(
            'Status,Company,Product/Purpose,Role,Comp,Location,Fit,GD Rating,'
            'Source,Date Found,Date Applied,Link,Notes\n'
        )
        with open(legacy) as f:
            current_header = next(csv.reader(f))
        result = sj._migrate_legacy_header(legacy, current_header)
        assert result == current_header
        assert not list(tmp_path.glob("jobs.csv.bak.*"))

    def test_migration_preserves_all_data_columns(self, tmp_path):
        """All non-'#' columns survive the migration."""
        legacy = tmp_path / "jobs.csv"
        legacy.write_text(
            '#,Company,Product/Purpose,Role,Comp,Location,Fit,GD Rating,Source,'
            'Date Found,Status,Date Applied,Link,Notes\n'
            '5,CorpX,Payments,Platform Eng,200k,Remote,8,,LinkedIn,2026-04-10,'
            'found,,https://corpx.io/job,good fit\n'
        )
        with open(legacy) as f:
            legacy_header = next(csv.reader(f))
        sj._migrate_legacy_header(legacy, legacy_header)

        with open(legacy, newline="") as f:
            reader = csv.DictReader(f)
            row = next(reader)
        assert row["Company"] == "CorpX"
        assert row["Role"] == "Platform Eng"
        assert row["Status"] == "found"
        assert row["Link"] == "https://corpx.io/job"
        assert "#" not in row
