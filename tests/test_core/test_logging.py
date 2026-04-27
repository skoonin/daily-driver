from __future__ import annotations

import logging

from rich.logging import RichHandler

from daily_driver.core.logging import configure, get_logger


def _daily_driver_logger() -> logging.Logger:
    return logging.getLogger("daily_driver")


def test_configure_normal_sets_info_level() -> None:
    configure("normal")
    assert _daily_driver_logger().level == logging.INFO


def test_configure_verbose_sets_debug_level() -> None:
    configure("verbose")
    assert _daily_driver_logger().level == logging.DEBUG


def test_configure_quiet_sets_warning_level() -> None:
    configure("quiet")
    assert _daily_driver_logger().level == logging.WARNING


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
    # caplog can't capture here because daily_driver.propagate=False keeps
    # messages off the root logger. Instead verify the handler receives records
    # by attaching a MemoryHandler directly to the daily_driver logger.
    configure("normal")
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
