"""Journalisation structurée, corrélée par `run_id`.

Le `run_id` vient de `text_to_sql`, dont la traçabilité était la meilleure des
trois dépôts. Il devient l'identifiant de corrélation de **toute** la
plateforme : une requête HTTP, les appels d'outils qu'elle déclenche, les
appels de modèle et les lignes du journal d'audit portent le même.

Un secret ne passe jamais par ici. Les valeurs sensibles sont masquées à la
source, pas filtrées à la sortie.

**Une seule sortie, un seul format.** Les journaux d'`uvicorn`, d'`alembic` et de
SQLAlchemy passent par la bibliothèque standard, pas par structlog : sans
`_route_stdlib_logging`, une installation en production émettait du JSON pour ses
propres évènements et du texte brut pour les accès HTTP, sur le même flux. Un
collecteur en rejette alors la moitié, et c'est la moitié qui dit quelle requête
a été servie.
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


#: Traitements communs aux deux origines — structlog et bibliothèque standard.
#: Déclarés une fois : deux listes finiraient par diverger, et la divergence se
#: verrait le jour où un champ manque dans la moitié des lignes.
def _shared_processors() -> list[Any]:
    return [
        structlog.contextvars.merge_contextvars,
        _inject_run_id,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]


def configure_logging(*, json_output: bool = True, level: int = logging.INFO) -> None:
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[
            *_shared_processors(),
            # Passe la main au formateur de la bibliothèque standard : c'est lui
            # qui rend, pour que les deux origines produisent exactement la même
            # ligne.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _route_stdlib_logging(renderer=renderer, level=level)


def _route_stdlib_logging(*, renderer: Any, level: int) -> None:
    """Fait passer `uvicorn`, `alembic` et SQLAlchemy par le même rendu.

    L'accès HTTP d'`uvicorn` est **désactivé** plutôt que reformaté : il ne porte
    ni identifiant de corrélation, ni durée, et il dédoublerait la ligne d'accès
    que l'application écrit elle-même avec les deux.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=renderer,
            foreign_pre_chain=_shared_processors(),
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    for name in ("uvicorn", "uvicorn.error", "alembic", "sqlalchemy.engine"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True

    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False

    # `httpx` journalise l'URL **complète** de chaque requête sortante, chaîne de
    # requête comprise. Router la bibliothèque standard vers notre flux a rendu
    # ces lignes visibles — et avec elles tout ce qu'un fournisseur accepte dans
    # une URL, une clé d'API y compris. Ramenées à WARNING : une requête sortante
    # qui réussit n'apprend rien, une qui échoue le dit sans l'URL.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
