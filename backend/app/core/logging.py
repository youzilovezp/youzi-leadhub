"""
统一日志配置（基于 loguru）。

仅输出到 stdout——容器时代 12-factor 标准（docker logs / kubectl logs 直读）。

支持 LOG_FORMAT=json（生产 ELK / Loki）或 text（dev 本地友好）。
"""

import json
import sys

from loguru import logger

from app.core.config import settings


def _json_sink(message) -> None:
    """loguru JSON sink：把日志记录序列化为单行 JSON，便于 ELK / Loki 解析。"""
    record = message.record
    sys.stdout.write(
        json.dumps(
            {
                "ts": record["time"].isoformat(),
                "level": record["level"].name,
                "name": record["name"],
                "function": record["function"],
                "line": record["line"],
                "message": record["message"],
                "extra": record.get("extra", {}),
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    sys.stdout.flush()


def setup_logging() -> None:
    """初始化日志输出。"""
    logger.remove()
    if settings.LOG_FORMAT == "json":
        logger.add(
            _json_sink,
            level=settings.LOG_LEVEL,
            backtrace=settings.DEBUG,
            diagnose=settings.DEBUG,
        )
    else:
        logger.add(
            sys.stdout,
            level=settings.LOG_LEVEL,
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
                "<level>{message}</level>"
            ),
            backtrace=settings.DEBUG,
            diagnose=settings.DEBUG,
        )


__all__ = ["logger", "setup_logging"]
