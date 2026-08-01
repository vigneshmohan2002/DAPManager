"""
Logging configuration for DAP Manager.
"""

import logging
import re
import sys
from pathlib import Path
from typing import Any


_QUERY_SECRET_RE = re.compile(
    r"([?&](?:token|api_token|bundle_token)=)[^&\s\"']+",
    re.IGNORECASE,
)


def _redact_query_secrets(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return _QUERY_SECRET_RE.sub(r"\1[REDACTED]", value)


class QuerySecretRedactionFilter(logging.Filter):
    """Remove bearer-equivalent query values before any handler writes."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact_query_secrets(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                _redact_query_secrets(value) for value in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                key: _redact_query_secrets(value)
                for key, value in record.args.items()
            }
        return True


def setup_logging(log_file: str = "dap_manager.log", level: int = logging.INFO):
    """
    Configure logging for the entire application.

    :param log_file: Path to log file
    :param level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """

    # Create logs directory if it doesn't exist
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Configure root logger
    logger = logging.getLogger()
    logger.setLevel(level)

    # Remove existing handlers
    logger.handlers.clear()

    # Console handler (INFO and above)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.addFilter(QuerySecretRedactionFilter())
    console_formatter = logging.Formatter("%(levelname)s: %(message)s")
    console_handler.setFormatter(console_formatter)

    # File handler (DEBUG and above)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.addFilter(QuerySecretRedactionFilter())
    file_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_formatter)

    # Add handlers
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger
