from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("audioethernet")
    if logger.handlers:
        logger.setLevel(level.upper())
        return logger

    logger.setLevel(level.upper())
    logger.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(threadName)s | %(message)s"
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        log_dir = Path(local_app_data) / "AudioEthernet" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "audioethernet.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger
