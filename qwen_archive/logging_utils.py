"""Console/file logging with deterministic severity filtering and optional ANSI color."""

from __future__ import annotations

import logging
import os
import sys
from enum import Enum
from pathlib import Path
from typing import TextIO


class ColorMode(str, Enum):
    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"

    @classmethod
    def parse(cls, value: str | bool | None) -> "ColorMode":
        if value is True:
            return cls.ALWAYS
        if value is False:
            return cls.NEVER
        text = str(value or "auto").strip().lower()
        try:
            return cls(text)
        except ValueError as exc:
            raise ValueError("Color mode must be auto, always, or never.") from exc


_LEVEL_COLORS = {
    logging.DEBUG: "\033[38;5;244m",
    logging.INFO: "\033[38;5;39m",
    logging.WARNING: "\033[38;5;214m",
    logging.ERROR: "\033[38;5;196m",
    logging.CRITICAL: "\033[1;38;5;196m",
}
_RESET = "\033[0m"


class ColorFormatter(logging.Formatter):
    """Applies color to a complete console line without mutating the LogRecord."""

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        color = _LEVEL_COLORS.get(record.levelno, "")
        return f"{color}{rendered}{_RESET}" if color else rendered


def _supports_color(stream: TextIO, mode: ColorMode) -> bool:
    if mode is ColorMode.NEVER:
        return False
    if mode is ColorMode.ALWAYS:
        return True
    if os.getenv("NO_COLOR") is not None:
        return False
    if os.getenv("TERM", "").casefold() == "dumb":
        return False
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def configure_logging(
    level: str = "INFO",
    log_path: str = "",
    color: str | bool = "auto",
    *,
    stream: TextIO | None = None,
) -> logging.Logger:
    """Configure the application logger idempotently.

    Console logs may be colorized; UTF-8 file logs are always plain. Repeated
    calls replace handlers, preventing duplicated output in tests and embedded
    service restarts.
    """
    logger = logging.getLogger("qwen_archive")
    logger.propagate = False
    numeric = getattr(logging, str(level).upper(), None)
    if not isinstance(numeric, int):
        raise ValueError(f"Unsupported log level: {level}")
    logger.setLevel(numeric)

    for handler in tuple(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    output = stream or sys.stdout
    base_formatter = logging.Formatter(
        "[%(asctime)s][%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    mode = ColorMode.parse(color)
    console = logging.StreamHandler(output)
    console.setLevel(numeric)
    if _supports_color(output, mode):
        console.setFormatter(
            ColorFormatter(
                "[%(asctime)s][%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
    else:
        console.setFormatter(base_formatter)
    logger.addHandler(console)

    if str(log_path).strip():
        path = Path(log_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(base_formatter)
        file_handler.setLevel(numeric)
        logger.addHandler(file_handler)
    return logger
