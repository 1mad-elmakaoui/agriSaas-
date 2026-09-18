"""Journalisation structurée, corrélée par `run_id`.

Le `run_id` vient de `text_to_sql`, dont la traçabilité était la meilleure des
trois dépôts. Il devient l'identifiant de corrélation de **toute** la
plateforme : une requête HTTP, les appels d'outils qu'elle déclenche, les
appels de modèle et les lignes du journal d'audit portent le même.

Un secret ne passe jamais par ici. Les valeurs sensibles sont masquées à la
source, pas filtrées à la sortie.
"""

from __future__ import annotations

import logging
import sys
import uuid
from contextvars import ContextVar
from typing import Any

import structlog
from structlog.typing import EventDict, WrappedLogger

__all__ = ["configure_logging", "current_run_id", "get_logger", "new_run_id", "run_id_var"]

run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)


def new_run_id() -> str:
    return uuid.uuid4().hex


def current_run_id() -> str | None:
    return run_id_var.get()


def _inject_run_id(
    _logger: WrappedLogger, _name: str, event_dict: EventDict
) -> EventDict:
    run_id = run_id_var.get()
    if run_id is not None:
        event_dict.setdefault("run_id", run_id)
    return event_dict


def configure_logging(*, json_output: bool = True, level: int = logging.INFO) -> None:
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _inject_run_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
