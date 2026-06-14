"""Logging setup shared by NetCourier components."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from common.constants import DEFAULT_LOG_DIR


def setup_logging(component: str, *, log_dir: str | Path | None = None) -> logging.Logger:
    """Configure and return a component logger."""

    target_dir = Path(log_dir or os.getenv("NETCOURIER_LOG_DIR", DEFAULT_LOG_DIR))
    target_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(f"netcourier.{component}")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(target_dir / f"{component}.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger
