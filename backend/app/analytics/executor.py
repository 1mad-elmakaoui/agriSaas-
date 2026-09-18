"""Exécution bornée — couches 2, 3 et 4.

Une différence structurelle avec le système d'origine, et c'est la plus
importante du portage : **cet exécuteur ne peut pas être construit sans
organisation.** Il prend une `AnalyticsScope`, qui n'est obtenable que depuis un
contexte authentifié, et dont chaque connexion est déjà en lecture seule, bornée
dans le temps, liée à `app.current_tenant` et toujours annulée.

L'exécuteur d'origine prenait un moteur SQLAlchemy et ouvrait ses propres
transactions. Rien n'y empêchait d'en construire un sans tenant — la
multi-location était un non-objectif — et il aurait suffi d'un appelant
distrait pour perdre l'isolation sans qu'aucune ligne de code n'ait l'air fausse.

Pourquoi chaque contrôle existe, étant donné les autres :

* `SET TRANSACTION READ ONLY` bloque les écritures passées à travers le
  validateur, y compris `nextval()`, qu'un arbre syntaxique ne distingue pas
  d'un appel de fonction ;
* `statement_timeout` vit côté serveur et survit à un client qui raccroche ;
* le délai côté client attrape une **socket** figée, que le délai serveur ne voit
  pas ;
* le plafond de lignes borne le nombre, le budget d'octets borne la mémoire —
  qu'un compte de lignes ne borne pas : une seule colonne `jsonb` large met en
  défaut un plafond de 500 lignes ;
* `lock_timeout` empêche une lecture d'attendre derrière un DDL dont elle n'a pas
  besoin.

L'exécuteur ne revalide pas le SQL. Deux copies divergentes de la politique de
sécurité seraient pires qu'une : celle qu'on relit ne serait pas celle qui
s'applique. Il applique en revanche ses bornes d'exécution indépendamment de ce
que la validation a conclu.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import orjson
from sqlalchemy import text

from app.analytics.errors import db_error_from_exception
from app.analytics.execution_models import (
    DbError,
    ErrorClass,
    QueryResult,
    ResultColumn,
)
from app.analytics.validation.models import SafetyDecision, SafetyReport
from app.core.logging import get_logger
from app.db.session import AnalyticsScope

logger = get_logger(__name__)

__all__ = ["ExecutionOutcome", "ExecutionSettings", "ReadOnlyExecutor"]


@dataclass(frozen=True, slots=True)
class ExecutionSettings:
    max_rows: int = 500
    max_result_bytes: int = 2_000_000
    execute_timeout_s: float = 25.0
    explain_enabled: bool = True
    explain_timeout_s: float = 5.0
    #: Le coût de plan est exprimé dans des unités qui dépendent de
    #: l'installation. C'est donc un contrôle de **ressource**, calibré par
    #: déploiement, et jamais une frontière de sécurité.
    explain_cost_threshold: float = 5_000_000.0


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    """Des lignes, ou une erreur classée. Jamais les deux."""

    result: QueryResult | None = None
    error: DbError | None = None

    def __post_init__(self) -> None:
        if (self.result is None) == (self.error is None):
            raise ValueError(
                "Une issue d'exécution porte exactement un résultat ou une erreur."
            )

    @property
    def ok(self) -> bool:
        return self.result is not None


class ReadOnlyExecutor:
    def __init__(self, scope: AnalyticsScope, settings: ExecutionSettings) -> None:
        self._scope = scope
        self._settings = settings

    # -- couche 4 : EXPLAIN ------------------------------------------------

    async def explain(self, sql: str) -> SafetyReport:
        """Planifie sans exécuter.

        C'est le contrôle sémantique **faisant autorité** : `EXPLAIN` fait la
        résolution de noms, la vérification de types et le contrôle de légalité
        du `GROUP BY` à l'intérieur de PostgreSQL, ce qu'aucune analyse sur
        instantané de catalogue ne peut égaler.

        `EXPLAIN` sans `ANALYZE` n'exécute pas l'instruction.
        """
        if not self._settings.explain_enabled:
            return SafetyReport(decision=SafetyDecision.ALLOW, skipped=True)

        started = time.perf_counter()
        try:
            async with self._scope.connection() as conn:
                await conn.execute(
                    text(
                        "SET LOCAL statement_timeout = "
                        f"{int(self._settings.explain_timeout_s * 1000)}"
                    )
                )
                result = await asyncio.wait_for(
                    conn.execute(text(f"EXPLAIN (FORMAT JSON, COSTS TRUE) {sql}")),
                    timeout=self._settings.explain_timeout_s + 1.0,
                )
                raw = result.scalar_one()
        except Exception as exc:
            error = db_error_from_exception(exc)
            # Un échec d'EXPLAIN est un échec **sémantique** de la requête, donc
            # réparable : il dit exactement ce qui ne va pas.
            return SafetyReport(
                decision=SafetyDecision.SEMANTIC_ERROR,
                message=error.render(),
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

        duration_ms = int((time.perf_counter() - started) * 1000)
        plan = _parse_explain(raw)
        total_cost = _as_float(plan.get("Total Cost"))
        plan_rows = _as_float(plan.get("Plan Rows"))
        threshold = self._settings.explain_cost_threshold

        if total_cost > threshold:
            return SafetyReport(
                decision=SafetyDecision.TOO_EXPENSIVE,
                total_cost=total_cost,
                plan_rows=plan_rows,
                threshold=threshold,
                plan=plan,
                duration_ms=duration_ms,
                message=(
                    f"Le coût de plan estimé ({total_cost:,.0f}) dépasse le seuil "
                    f"configuré ({threshold:,.0f}). Restreignez la question : une "
                    "période plus courte, un filtre plus sélectif, ou une agrégation "
                    "plus tôt.".replace(",", " ")
                ),
            )

        return SafetyReport(
            decision=SafetyDecision.ALLOW,
            total_cost=total_cost,
            plan_rows=plan_rows,
            threshold=threshold,
            plan=plan,
            duration_ms=duration_ms,
        )

    # -- couche 3 : exécution bornée ---------------------------------------

    async def execute(self, sql: str) -> ExecutionOutcome:
        started = time.perf_counter()
        try:
            return await asyncio.wait_for(
                self._execute_inner(sql, started),
                timeout=self._settings.execute_timeout_s,
            )
        except TimeoutError:
            return ExecutionOutcome(
                error=DbError(
                    message=(
                        "La requête a dépassé le délai d'exécution côté client de "
                        f"{self._settings.execute_timeout_s:.0f} s."
                    ),
                    classification=ErrorClass.TIMEOUT,
                )
            )
        except Exception as exc:
            return ExecutionOutcome(error=db_error_from_exception(exc))

    async def _execute_inner(self, sql: str, started: float) -> ExecutionOutcome:
        settings = self._settings
        rows: list[dict[str, Any]] = []
        total_bytes = 0
        truncated = False
        truncation_reason: str | None = None

        async with self._scope.connection() as conn:
            cursor = await conn.execute(text(sql))
            columns = tuple(
                ResultColumn(name=str(key), type_name="")
                for key in (cursor.keys() or ())
            )
            for row in cursor.mappings():
                if len(rows) >= settings.max_rows:
                    truncated = True
                    truncation_reason = (
                        f"Résultat tronqué à {settings.max_rows} lignes."
                    )
                    break
                record = dict(row)
                total_bytes += len(orjson.dumps(record, default=str))
                if total_bytes > settings.max_result_bytes:
                    truncated = True
                    truncation_reason = (
                        "Résultat tronqué : le budget de "
                        f"{settings.max_result_bytes // 1000} ko est atteint."
                    )
                    break
                rows.append(record)

        return ExecutionOutcome(
            result=QueryResult(
                columns=columns,
                rows=tuple(rows),
                row_count=len(rows),
                truncated=truncated,
                truncation_reason=truncation_reason,
                bytes_returned=total_bytes,
                execution_time_ms=int((time.perf_counter() - started) * 1000),
            )
        )


def _as_float(value: object) -> float:
    """Un coût de plan absent vaut zéro, pas une exception.

    PostgreSQL omet parfois les champs de coût — sur un plan trivial, ou selon
    la version. Laisser remonter une erreur ici transformerait une requête
    parfaitement valide en échec, au dernier contrôle avant l'exécution.
    """
    return float(value) if isinstance(value, int | float) else 0.0


def _parse_explain(raw: Any) -> dict[str, object]:
    """Extrait le nœud racine du plan.

    `EXPLAIN (FORMAT JSON)` rend une liste d'un élément ; selon le pilote, elle
    arrive déjà décodée ou encore sous forme de chaîne.
    """
    payload = raw
    if isinstance(payload, str):
        try:
            payload = orjson.loads(payload)
        except orjson.JSONDecodeError:
            return {}
    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict):
            plan = first.get("Plan")
            if isinstance(plan, dict):
                return plan
    return {}
