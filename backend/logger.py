"""Centralised logging infrastructure for the Data Ingestion Framework.

Two pre-built logger singletons are exported:

- ``ui_logger``        — Records API route activity: inbound requests, CRUD
  events, validation failures, and errors raised by the Flask routes.
- ``ingestion_logger`` — Records every stage of the ETL pipeline: metadata
  loading, file discovery, column comparison, bulk loads, file archival, and
  run-log lifecycle transitions.

Swapping the log destination in the future requires only two changes:

1. Subclass :class:`AppLogger` and override :meth:`AppLogger._emit`.
2. Replace the two module-level instances (``ui_logger`` / ``ingestion_logger``)
   at the bottom of this file.

No callers need to change — all route handlers and engine methods import the
singletons by name and call the four public methods.
"""

import logging
import os
from logging.handlers import RotatingFileHandler


# All log files are written into backend/logs/ relative to this file.
_LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')


class AppLogger:
    """Abstract base class for application loggers.

    Defines the public interface (``info``, ``warning``, ``error``, ``debug``)
    and the private extension point (``_emit``).  Concrete subclasses only
    need to implement ``_emit``; all public methods delegate to it.

    Keyword arguments passed to the public methods are appended to the log
    line as ``key=repr(value)`` pairs, making entries both human-readable and
    grep-friendly::

        2026-05-28 10:12:34 [INFO    ] Provider created  provider_id=5  name='Acme'
    """

    def info(self, msg: str, **context) -> None:
        """Log an informational event — normal, expected application activity.

        Args:
            msg (str): Short description of the event.
            **context: Arbitrary key-value pairs that add structured detail.
        """
        self._emit('INFO', msg, **context)

    def warning(self, msg: str, **context) -> None:
        """Log a warning — something unexpected but handled gracefully.

        Examples: duplicate name rejected, file already processed, column mismatch.

        Args:
            msg (str): Short description of the warning condition.
            **context: Arbitrary key-value pairs for context.
        """
        self._emit('WARNING', msg, **context)

    def error(self, msg: str, **context) -> None:
        """Log an error — an operation failed and may need investigation.

        Args:
            msg (str): Short description of the failure.
            **context: Arbitrary key-value pairs; include ``error=`` and/or
                ``traceback=`` for diagnosability.
        """
        self._emit('ERROR', msg, **context)

    def debug(self, msg: str, **context) -> None:
        """Log a low-level diagnostic event — verbose detail for development.

        Args:
            msg (str): Short description of the diagnostic point.
            **context: Arbitrary key-value pairs.
        """
        self._emit('DEBUG', msg, **context)

    def _emit(self, level: str, msg: str, **context) -> None:
        """Write a single log entry.

        Args:
            level (str): One of ``'DEBUG'``, ``'INFO'``, ``'WARNING'``,
                ``'ERROR'``.
            msg (str): The log message.
            **context: Key-value pairs appended after the message.

        Raises:
            NotImplementedError: Subclasses must override this method.
        """
        raise NotImplementedError


class FileLogger(AppLogger):
    """Writes structured log entries to a size-rotating file on disk.

    Each log entry is a single line in the format::

        YYYY-MM-DD HH:MM:SS [LEVEL   ] message  key='value'  key2=123

    Files rotate when they exceed ``max_bytes``; up to ``backup_count``
    rotated copies are kept alongside the active file, so the total disk
    footprint is bounded at ``(backup_count + 1) * max_bytes``.

    The underlying Python ``logging.Logger`` instance is registered by
    ``name``, so re-importing this module (e.g. during Flask development
    reloads) does not open duplicate file handles.

    Args:
        name (str): Unique logger name — must be distinct per log file.
        filename (str): Log file name; created inside ``_LOG_DIR``.
        max_bytes (int): Rotate at this file size.  Default 10 MB.
        backup_count (int): Rotated copies to retain.  Default 5.
    """

    def __init__(self, name: str, filename: str,
                 max_bytes: int = 10 * 1024 * 1024,
                 backup_count: int = 5) -> None:
        os.makedirs(_LOG_DIR, exist_ok=True)
        log_path = os.path.join(_LOG_DIR, filename)

        self._logger = logging.getLogger(name)
        self._logger.setLevel(logging.DEBUG)

        # Prevent duplicate handlers when the module is reloaded in dev mode.
        if not self._logger.handlers:
            handler = RotatingFileHandler(
                log_path,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding='utf-8',
            )
            handler.setFormatter(logging.Formatter(
                '%(asctime)s [%(levelname)-8s] %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S',
            ))
            self._logger.addHandler(handler)

    def _emit(self, level: str, msg: str, **context) -> None:
        """Format one log line and hand it to the Python logging handler.

        Context pairs are rendered as ``  key=repr(value)`` (two leading
        spaces per pair) and appended after the message.
        """
        ctx_str = (
            '  ' + '  '.join(f'{k}={v!r}' for k, v in context.items())
            if context else ''
        )
        getattr(self._logger, level.lower())(f'{msg}{ctx_str}')


# ── Module-level singletons ──────────────────────────────────────────────────
# To redirect logs to a different destination (database, cloud service, SIEM,
# message queue, etc.), replace these two lines — no other code needs to change.

ui_logger        = FileLogger('ui',        'ui.log')
ingestion_logger = FileLogger('ingestion', 'ingestion.log')
