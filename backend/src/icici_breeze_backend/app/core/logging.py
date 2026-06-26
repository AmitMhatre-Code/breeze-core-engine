"""Structured logging configuration."""
import logging
import os
import sys
from typing import Optional

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(
    level: str = "INFO",
    format_string: Optional[str] = None,
    stream: Optional[object] = None,
    log_file: Optional[str] = None,
) -> None:
    """Configure root logger with consistent format and level. log_file from .env or LOG_FILE env."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    fmt = format_string or LOG_FORMAT
    formatter = logging.Formatter(fmt, datefmt=LOG_DATE_FORMAT)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setLevel(log_level)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(log_level)
    if not root.handlers:
        root.addHandler(handler)
    else:
        root.handlers[0].setLevel(log_level)
        root.handlers[0].setFormatter(formatter)
        root.setLevel(log_level)
    if log_file and log_file.strip():
        try:
            log_path = log_file.strip()
            os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
            file_handler = logging.FileHandler(log_path, encoding="utf-8")
            file_handler.setLevel(log_level)
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError:
            pass
    # Always show uvicorn lifecycle and access (startup, per-request lines)
    for uvicorn_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(uvicorn_name).setLevel(logging.INFO)
    # breeze_connect logs ERROR on empty/invalid API response (e.g. JSON decode); we handle it and return error to user
    for breeze_logger_name in ("APILogger", "breeze_connect"):
        logging.getLogger(breeze_logger_name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a logger for the given module name."""
    return logging.getLogger(name)
