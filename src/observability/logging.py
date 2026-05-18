"""
Loguru bootstrap.

Минимум для MVP: структурированный JSON в файл + читабельный вывод в stdout.
В Phase 6 сюда же навесим OTel-handler и пробрасывание trace_id в каждую запись.
"""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

from src.config import get_settings

_CONFIGURED = False


def setup_logging() -> None:
    """Идемпотентная инициализация. Вызывается из main и из тестов."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    s = get_settings()
    logger.remove()

    # Stdout — короткий читабельный формат
    logger.add(
        sys.stderr,
        level=s.log_level,
        format=(
            "<green>{time:HH:mm:ss}</green> "
            "<level>{level:<7}</level> "
            "<cyan>{name}</cyan> {message} {extra}"
        ),
        colorize=True,
        backtrace=False,
        diagnose=False,
    )

    # File — JSON lines, чтобы евал-скрипты могли парсить
    s.log_file.parent.mkdir(parents=True, exist_ok=True)
    logger.add(
        s.log_file,
        level="DEBUG",
        serialize=True,
        rotation="10 MB",
        retention=5,
        enqueue=True,  # writes from multiple threads safely
    )

    _CONFIGURED = True
    logger.info("logging.configured", file=str(s.log_file), level=s.log_level)
