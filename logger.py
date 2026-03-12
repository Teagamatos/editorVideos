"""
logger.py — Configuração central de logging para toda a aplicação.

Uso em qualquer módulo:
    from logger import get_logger
    log = get_logger(__name__)
    log.info("mensagem")
    log.error("erro", exc_info=True)
"""

import logging
import sys
import os

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _configurar():
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format=LOG_FORMAT,
        datefmt=DATE_FORMAT,
        stream=sys.stdout,
        force=True,
    )
    # Silencia libs barulhentas
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("googleapiclient.discovery").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


_configurar()


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
