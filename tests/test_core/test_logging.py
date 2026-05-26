from __future__ import annotations

import logging
from datetime import UTC, datetime

from rich.logging import RichHandler

from daily_driver.core.logging import configure, get_logger, log_query_window


def _daily_driver_logger() -> logging.Logger:
    return logging.getLogger("daily_driver")


def test_configure_normal_sets_warning_level() -> None:
    configure("normal")
    assert _daily_driver_logger().level == logging.WARNING


def test_configure_verbose_sets_info_level() -> None:
    configure("verbose")
    assert _daily_driver_logger().level == logging.INFO


def test_configure_debug_sets_debug_level() -> None:
    configure("debug")
    assert _daily_driver_logger().level == logging.DEBUG


def test_configure_quiet_sets_error_level() -> None:
    configure("quiet")
    assert _daily_driver_logger().level == logging.ERROR


def test_handler_is_rich() -> None:
    configure("normal")
    logger = _daily_driver_logger()
    assert any(isinstance(h, RichHandler) for h in logger.handlers)


def test_idempotent_no_double_handlers() -> None:
    configure("normal")
    configure("normal")
    configure("verbose")
    logger = _daily_driver_logger()
    assert len(logger.handlers) == 1


def test_get_logger_namespace() -> None:
    lg = get_logger("test_module")
    assert lg.name == "daily_driver.test_module"


def test_log_message_captured() -> None:
    configure("verbose")
    lg = get_logger("memory_test")
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    cap = _Capture()
    parent = logging.getLogger("daily_driver")
    parent.addHandler(cap)
    try:
        lg.info("hello from test")
    finally:
        parent.removeHandler(cap)

    assert any("hello from test" in r.getMessage() for r in records)


def test_log_query_window_emits_debug_with_bounds(caplog) -> None:
    configure("debug")
    caplog.set_level(logging.DEBUG, logger="daily_driver")
    lg = get_logger("window_test")
    since = datetime(2026, 4, 10, 0, 0)
    until = datetime(2026, 4, 11, 0, 0, tzinfo=UTC)
    log_query_window(lg, "calendar", since, until)
    msg = next(r.getMessage() for r in caplog.records if "calendar" in r.getMessage())
    assert "since=2026-04-10T00:00:00" in msg
    assert "until=2026-04-11T00:00:00+00:00" in msg
    assert "(naive)" in msg  # since is naive


def test_caplog_captures_daily_driver_records(caplog) -> None:
    """The root conftest installs an autouse fixture that mirrors caplog's
    handler onto the `daily_driver` logger. Without it, pytest's caplog
    misses records because `configure()` sets `propagate=False`."""
    configure("verbose")
    caplog.set_level(logging.INFO, logger="daily_driver")
    lg = get_logger("caplog_test")
    lg.info("captured via caplog")
    assert any("captured via caplog" in r.getMessage() for r in caplog.records)
