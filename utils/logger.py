"""
utils/logger.py
===============
Forensic-grade structured logger for DroidTrace Pro.

Design decisions:
- Dual output: rotating file log + console (for debugging during dev)
- Each log record includes the calling module and function name automatically
- File log uses a fixed JSON-like format for easy parsing by external tools
- Console log uses a human-readable colour format
- Forensic note: logs are append-only; rotation creates new files, never truncates
"""

import logging
import logging.handlers
import sys
from pathlib import Path

from config.settings import LOG_DIR, LOG_FILE_NAME, LOG_MAX_BYTES, LOG_BACKUP_COUNT

# ─────────────────────────────────────────────────────────────────────────────
# Formatters
# ─────────────────────────────────────────────────────────────────────────────

class _ForensicFileFormatter(logging.Formatter):
    """
    Produces a pipe-delimited structured log line suitable for forensic records.
    Example:
      2024-11-01T22:15:03.421Z | INFO     | adb_connector.connect | Device connected: emulator-5554
    """
    def formatTime(self, record, datefmt=None):  # noqa: N802
        import datetime
        dt = datetime.datetime.fromtimestamp(record.created, tz=datetime.timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"

    def format(self, record):
        record.asctime = self.formatTime(record)
        location = f"{record.module}.{record.funcName}"
        return (
            f"{record.asctime} | {record.levelname:<8} | {location} | {record.getMessage()}"
        )


class _ConsoleFormatter(logging.Formatter):
    """Colour-coded console formatter for developer readability."""
    _COLOURS = {
        logging.DEBUG:    "\033[37m",    # white
        logging.INFO:     "\033[36m",    # cyan
        logging.WARNING:  "\033[33m",    # yellow
        logging.ERROR:    "\033[31m",    # red
        logging.CRITICAL: "\033[1;31m",  # bold red
    }
    _RESET = "\033[0m"

    def format(self, record):
        colour = self._COLOURS.get(record.levelno, self._RESET)
        record.levelname = f"{colour}{record.levelname:<8}{self._RESET}"
        return super().format(record)


# ─────────────────────────────────────────────────────────────────────────────
# Public factory
# ─────────────────────────────────────────────────────────────────────────────

def get_logger(name: str) -> logging.Logger:
    """
    Return a named logger configured for DroidTrace Pro.

    Usage:
        from utils.logger import get_logger
        log = get_logger(__name__)
        log.info("Device connected: %s", serial)

    Args:
        name: Typically pass ``__name__`` from the calling module.

    Returns:
        A fully configured :class:`logging.Logger` instance.
    """
    logger = logging.getLogger(name)

    # Avoid adding duplicate handlers if get_logger() is called multiple times
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    # ── File handler (rotating) ────────────────────────────────────────────
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / LOG_FILE_NAME
    file_handler = logging.handlers.RotatingFileHandler(
        filename=log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
        delay=False,
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(_ForensicFileFormatter())

    # ── Console handler ────────────────────────────────────────────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(
        _ConsoleFormatter(
            fmt="%(levelname)s %(name)s: %(message)s"
        )
    )

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger
