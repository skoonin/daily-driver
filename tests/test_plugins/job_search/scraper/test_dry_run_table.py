"""Tests for the `jobs run --dry-run` Rich table renderer + mojibake helper."""

from __future__ import annotations

import io
from contextlib import redirect_stdout


def test_fix_mojibake_reverses_utf8_as_latin1():
    from daily_driver.plugins.job_search.scraper import _fix_mojibake

    # UTF-8 em-dash (\xe2\x80\x94) mis-decoded as latin-1 yields three chars.
    mojibake = "Tech PM " + chr(0xE2) + chr(0x80) + chr(0x94) + " Platform"
    assert _fix_mojibake(mojibake) == "Tech PM — Platform"


def test_fix_mojibake_preserves_ascii():
    from daily_driver.plugins.job_search.scraper import _fix_mojibake

    assert _fix_mojibake("Staff SRE") == "Staff SRE"


def test_fix_mojibake_preserves_legitimate_latin1():
    from daily_driver.plugins.job_search.scraper import _fix_mojibake

    # 'é' is a legitimate latin-1 char that doesn't form valid UTF-8 alone —
    # the helper must round-trip it intact rather than mangle it.
    assert _fix_mojibake("résumé") == "résumé"


def test_fix_mojibake_handles_empty_string():
    from daily_driver.plugins.job_search.scraper import _fix_mojibake

    assert _fix_mojibake("") == ""


def test_dry_run_table_renders_columns_and_count():
    from daily_driver.plugins.job_search.scraper import _print_dry_run_table

    jobs = [
        {
            "source": "RemoteOK",
            "company": "Cayuse",
            "role": "Staff SRE",
            "location": "Remote",
            "url": "https://remoteok.com/x",
        },
    ]
    buf = io.StringIO()
    with redirect_stdout(buf):
        _print_dry_run_table(jobs)

    out = buf.getvalue()
    assert "RemoteOK" in out
    assert "Cayuse" in out
    assert "Staff SRE" in out
    assert "1 new jobs" in out


def test_dry_run_table_empty_jobs_message():
    from daily_driver.plugins.job_search.scraper import _print_dry_run_table

    buf = io.StringIO()
    with redirect_stdout(buf):
        _print_dry_run_table([])

    assert "no new jobs" in buf.getvalue()
