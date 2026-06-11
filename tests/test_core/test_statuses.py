from __future__ import annotations

import logging

import pytest

from daily_driver.core.statuses import normalize_status, warn_unknown_statuses


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("ruled_out", "ruled-out"),
        ("Ruled_Out", "ruled-out"),
        ("  in_progress  ", "in-progress"),
        ("IN-PROGRESS", "in-progress"),
        ("in progress", "in-progress"),
        ("open", "open"),
        ("", ""),
        ("   ", ""),
        ("Found", "found"),
    ],
)
def test_normalize_status(raw: str, expected: str) -> None:
    assert normalize_status(raw) == expected


def test_warn_unknown_statuses_logs_offending_and_recommended(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("test_statuses_helper")
    with caplog.at_level(logging.WARNING, logger="test_statuses_helper"):
        warn_unknown_statuses(
            logger,
            offending=["weird", "frob"],
            recommended=("open", "done"),
        )
    text = caplog.text
    assert "weird" in text
    assert "frob" in text
    assert "open" in text
    assert "done" in text
    # One record, not one per offending value.
    records = [
        r
        for r in caplog.records
        if r.name == "test_statuses_helper" and r.levelno >= logging.WARNING
    ]
    assert len(records) == 1


def test_warn_unknown_statuses_no_op_when_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("test_statuses_helper")
    with caplog.at_level(logging.WARNING, logger="test_statuses_helper"):
        warn_unknown_statuses(logger, offending=[], recommended=("open",))
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
