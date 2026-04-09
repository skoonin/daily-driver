"""Tests for CSV helpers: load_existing_urls() and append_jobs()."""

import csv
from pathlib import Path

import pytest
import scrape_jobs as sj

from fixtures import CSV_HEADER


class TestLoadExistingUrls:
    def test_missing_file_returns_empty_state(self, csv_path):
        urls, header, next_num = sj.load_existing_urls(csv_path)
        assert urls == set()
        assert header == []
        assert next_num == 1

    def test_empty_file_returns_empty_state(self, csv_path):
        csv_path.write_text("")
        urls, header, next_num = sj.load_existing_urls(csv_path)
        assert urls == set()
        assert header == []
        assert next_num == 1

    def test_header_only_file_returns_zero_urls(self, empty_csv):
        urls, header, next_num = sj.load_existing_urls(empty_csv)
        assert urls == set()
        assert header == CSV_HEADER
        assert next_num == 1

    def test_populated_csv_returns_known_urls(self, populated_csv):
        urls, _, _ = sj.load_existing_urls(populated_csv)
        assert "https://example.com/job1" in urls
        assert "https://example.com/job2" in urls
        assert len(urls) == 2

    def test_populated_csv_next_num_after_last_row(self, populated_csv):
        _, _, next_num = sj.load_existing_urls(populated_csv)
        assert next_num == 3

    def test_populated_csv_returns_header(self, populated_csv):
        _, header, _ = sj.load_existing_urls(populated_csv)
        assert "Link" in header
        assert "#" in header

    def test_skips_blank_rows(self, csv_path):
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADER)
            row = [""] * len(CSV_HEADER)
            row[0] = "1"
            row[CSV_HEADER.index("Link")] = "https://example.com/1"
            writer.writerow(row)
            f.write("\n")  # blank line between data rows
            row2 = [""] * len(CSV_HEADER)
            row2[0] = "2"
            row2[CSV_HEADER.index("Link")] = "https://example.com/2"
            writer.writerow(row2)
        urls, _, next_num = sj.load_existing_urls(csv_path)
        assert len(urls) == 2
        assert next_num == 3

    def test_non_integer_row_number_skipped(self, csv_path):
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADER)
            row = [""] * len(CSV_HEADER)
            row[0] = "N/A"
            row[CSV_HEADER.index("Link")] = "https://example.com/x"
            writer.writerow(row)
        urls, _, next_num = sj.load_existing_urls(csv_path)
        assert "https://example.com/x" in urls
        assert next_num == 1  # max_num stays 0, so next is 1

    def test_rows_without_link_are_excluded_from_urls(self, csv_path):
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADER)
            row = [""] * len(CSV_HEADER)
            row[0] = "1"
            row[CSV_HEADER.index("Company")] = "Acme"
            # Link column intentionally blank
            writer.writerow(row)
        urls, _, next_num = sj.load_existing_urls(csv_path)
        assert urls == set()
        assert next_num == 2


class TestAppendJobs:
    def test_empty_job_list_writes_nothing(self, empty_csv):
        written = sj.append_jobs(empty_csv, [], CSV_HEADER, 1)
        assert written == 0

    def test_single_job_appended(self, empty_csv):
        job = {
            "company": "Acme",
            "role": "Senior SRE",
            "location": "Remote",
            "url": "https://example.com/job99",
            "source": "HN",
        }
        written = sj.append_jobs(empty_csv, [job], CSV_HEADER, 5)
        assert written == 1

        with open(empty_csv, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))

        data = rows[1]
        assert data[CSV_HEADER.index("#")] == "5"
        assert data[CSV_HEADER.index("Company")] == "Acme"
        assert data[CSV_HEADER.index("Role")] == "Senior SRE"
        assert data[CSV_HEADER.index("Status")] == "found"
        assert data[CSV_HEADER.index("Link")] == "https://example.com/job99"

    def test_row_numbers_increment_sequentially(self, empty_csv):
        jobs = [
            {"company": "A", "role": "SRE", "url": "https://a.com", "source": "HN"},
            {"company": "B", "role": "DevOps", "url": "https://b.com", "source": "HN"},
            {"company": "C", "role": "Platform", "url": "https://c.com", "source": "HN"},
        ]
        sj.append_jobs(empty_csv, jobs, CSV_HEADER, 10)

        with open(empty_csv, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))

        assert rows[1][CSV_HEADER.index("#")] == "10"
        assert rows[2][CSV_HEADER.index("#")] == "11"
        assert rows[3][CSV_HEADER.index("#")] == "12"

    def test_returns_count_of_written_rows(self, empty_csv):
        jobs = [
            {"company": "A", "url": "https://a.com"},
            {"company": "B", "url": "https://b.com"},
        ]
        written = sj.append_jobs(empty_csv, jobs, CSV_HEADER, 1)
        assert written == 2

    def test_product_column_gets_default_placeholder(self, empty_csv):
        job = {"company": "Acme", "url": "https://x.com"}
        sj.append_jobs(empty_csv, [job], CSV_HEADER, 1)

        with open(empty_csv, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))

        product = rows[1][CSV_HEADER.index("Product/Purpose")]
        assert "auto-scraped" in product

    def test_handles_partial_header(self, csv_path):
        """append_jobs with a header missing some expected columns writes only columns present."""
        partial_header = ["#", "Company", "Role", "Link"]
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(partial_header)
        job = {"company": "Acme", "role": "SRE", "url": "https://x.com", "source": "HN"}
        written = sj.append_jobs(csv_path, [job], partial_header, 1)
        assert written == 1
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        data = rows[1]
        assert data[partial_header.index("#")] == "1"
        assert data[partial_header.index("Company")] == "Acme"
        assert data[partial_header.index("Role")] == "SRE"
        assert data[partial_header.index("Link")] == "https://x.com"
        assert len(data) == len(partial_header)

    def test_roundtrip_load_then_append(self, empty_csv):
        """Rows written by append_jobs are picked up by load_existing_urls."""
        job = {"company": "Acme", "url": "https://roundtrip.com", "source": "HN"}
        sj.append_jobs(empty_csv, [job], CSV_HEADER, 1)

        urls, _, next_num = sj.load_existing_urls(empty_csv)
        assert "https://roundtrip.com" in urls
        assert next_num == 2
