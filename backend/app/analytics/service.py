"""La voie analytique, de la question au résumé.

Un pipeline explicite, pas un graphe. Le système d'origine orchestrait avec
LangGraph ; ce dépôt a déjà une boucle d'agent manuelle (décision 0010), et
introduire une seconde façon d'orchestrer aurait donné deux endroits où
comprendre l'ordre des étapes. Ici l'ordre se lit dans une fonction.

    question → génération → validation (couche 1) → EXPLAIN (couche 4)
             → exécution bornée (couches 2 et 3) → résumé depuis les lignes

Trois règles portées par la boucle de réparation, et non par l'invite :

1. **Un échec de sécurité n'est jamais réparé.** Un modèle qui a émis un
   `DELETE`, ou visé une table de base, reçoit un refus — pas une seconde
   chance. C'est un événement à signaler, pas une faute de frappe à corriger.
2. **La déduplication compare une empreinte d'arbre, pas une chaîne.**
   Réindenter, changer la casse d'un mot-clé ou renommer un alias de sortie
   produit une chaîne différente pour la même requête — et ce sont exactement les
   changements qu'un modèle fait quand on lui dit « essaie autrement » sans qu'il
   ait compris l'erreur.
3. **Un dépassement de délai est réparable.** Une requête trop lente se resserre
   souvent en une requête qui passe ; une erreur de privilège, non.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from app.analytics.catalog import CatalogSnapshot
from app.analytics.execution_models import (
    UNRECOVERABLE_CLASSES,
    DbError,
    QueryResult,
)
from app.analytics.executor import ExecutionSettings, ReadOnlyExecutor
from app.analytics.introspection import read_catalog
from app.analytics.prompt import (
    PROMPT_VERSION,
    REPAIR_SYSTEM,
    explain_system,
    explain_user,
    generate_system,
    generate_user,
    repair_user,
)
from app.analytics.settings import ValidationSettings
from app.analytics.validation.models import (
    SECURITY_CODES,
    SafetyDecision,
    ValidationReport,
)
from app.analytics.validation.pipeline import SQLValidator
from app.core.errors import FeatureDisabledError, ProviderUnavailableError
from app.core.logging import current_run_id, get_logger
from app.core.untrusted import sanitize_user_text
from app.db.session import AnalyticsScope
from app.llm.base import LLMError, LLMProvider
from app.observability.cost import TokenUsage, estimate_cost_usd

logger = get_logger(__name__)

__all__ = ["AnalyticsAnswer", "AnalyticsService", "Attempt"]

#: Un modèle rend parfois la requête dans un bloc de code malgré la consigne.
#: Le déballer est une **normalisation d'entrée**, pas une indulgence : refuser
#: pour un délimiteur ferait dépenser une tentative de réparation sur une
#: requête qui était correcte.
_FENCE = re.compile(r"^\s*```(?:sql)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Attempt:
    """Une tentative, réussie ou non — la trace que l'interface affiche."""

    index: int
    sql: str
    status: str
    issues_fr: tuple[str, ...] = ()
    plan_cost: float | None = None
    duration_ms: int | None = None


@dataclass(slots=True)
class AnalyticsAnswer:
    question: str
    text_fr: str
    sql: str | None = None
    result: QueryResult | None = None
    attempts: list[Attempt] = field(default_factory=list)
    refused: bool = False
    refusal_reason_fr: str | None = None
    prompt_version: str = PROMPT_VERSION
    run_id: str | None = None
    usage: list[TokenUsage] = field(default_factory=list)
    catalog_fetched_at: datetime | None = None

    @property
    def total_cost_usd(self) -> float | None:
        if any(u.cost_usd is None for u in self.usage):
            return None
        return round(sum(u.cost_usd or 0.0 for u in self.usage), 6)


class AnalyticsService:
    def __init__(
        self,
        *,
        provider: LLMProvider | None,
        scope: AnalyticsScope,
        validation: ValidationSettings | None = None,
        execution: ExecutionSettings | None = None,
        max_attempts: int = 3,
    ) -> None:
        self._provider = provider
        self._scope = scope
        self._validator = SQLValidator(validation or ValidationSettings())
        self._execution = execution or ExecutionSettings()
        self._max_attempts = max_attempts

    async def ask(self, question: str) -> AnalyticsAnswer:
        if self._provider is None:
            raise FeatureDisabledError(
                "L'analyse en langage naturel n'est pas configurée sur cette "
                "installation : aucune clé d'API n'est renseignée.",
                remedy_fr=(
                    "Les tableaux de bord et les recommandations restent "
                    "accessibles dans le reste de l'application."
                ),
            )

        cleaned = sanitize_user_text(question)
        if not cleaned:
            raise FeatureDisabledError("La question est vide.")

        catalog = await self._catalog()
        answer = AnalyticsAnswer(
            question=cleaned,
            text_fr="",
            run_id=current_run_id(),
            catalog_fetched_at=catalog.fetched_at,
        )
        executor = ReadOnlyExecutor(self._scope, self._execution)

        system = generate_system(catalog)
        user = generate_user(cleaned)
        seen_fingerprints: set[str] = set()

        for index in range(1, self._max_attempts + 1):
            sql = await self._complete(system, user, answer)
            report = self._validator.validate(
                sql, catalog, row_limit=self._execution.max_rows
            )

            if self._is_security_refusal(report):
                return self._refuse(answer, index, sql, report)

            if report.ast_fingerprint and report.ast_fingerprint in seen_fingerprints:
                # Même requête à l'indentation près : une tentative de plus
                # produirait le même rejet, et le budget est mieux dépensé en
                # disant que la réparation ne converge pas.
                answer.attempts.append(
                    Attempt(
                        index=index,
                        sql=sql,
                        status="duplicate",
                        issues_fr=("Requête identique à une tentative précédente.",),
                    )
                )
                break
            if report.ast_fingerprint:
                seen_fingerprints.add(report.ast_fingerprint)

            errors = tuple(i.message for i in report.issues if i.is_error)
            if errors:
                answer.attempts.append(
                    Attempt(index=index, sql=sql, status="invalid", issues_fr=errors)
                )
                system, user = REPAIR_SYSTEM, repair_user(cleaned, sql, errors)
                continue

            validated = report.normalized_sql or sql
            safety = await executor.explain(validated)
            if safety.decision is SafetyDecision.SEMANTIC_ERROR:
                message = safety.message or "Le plan de la requête a échoué."
                answer.attempts.append(
                    Attempt(
                        index=index,
                        sql=validated,
                        status="plan_error",
                        issues_fr=(message,),
                        duration_ms=safety.duration_ms,
                    )
                )
                system, user = REPAIR_SYSTEM, repair_user(cleaned, validated, (message,))
                continue
            if safety.decision is SafetyDecision.TOO_EXPENSIVE:
                message = safety.message or "Requête trop coûteuse."
                answer.attempts.append(
                    Attempt(
                        index=index,
                        sql=validated,
                        status="too_expensive",
                        issues_fr=(message,),
                        plan_cost=safety.total_cost,
                    )
                )
                system, user = REPAIR_SYSTEM, repair_user(cleaned, validated, (message,))
                continue

            outcome = await executor.execute(validated)
            if not outcome.ok:
                error = outcome.error
                assert error is not None
                answer.attempts.append(
                    Attempt(
                        index=index,
                        sql=validated,
                        status="execution_error",
                        issues_fr=(error.render(),),
                    )
                )
                if error.classification in UNRECOVERABLE_CLASSES:
                    return self._unrecoverable(answer, error)
                system, user = REPAIR_SYSTEM, repair_user(
                    cleaned, validated, (error.render(),)
                )
                continue

            assert outcome.result is not None
            result = _declare_limit_truncation(outcome.result, report)
            answer.attempts.append(
                Attempt(
                    index=index,
                    sql=validated,
                    status="ok",
                    plan_cost=safety.total_cost,
                    duration_ms=outcome.result.execution_time_ms,
                )
            )
            answer.sql = validated
            answer.result = result
            answer.text_fr = await self._summarise(cleaned, validated, result, answer)
            return answer

        answer.text_fr = (
            "La question n'a pas pu être traduite en une requête valide en "
            f"{len(answer.attempts)} tentative(s). Le détail de chaque tentative est "
            "visible ci-dessous."
        )
        return answer

    # -- étapes ------------------------------------------------------------

    async def _catalog(self) -> CatalogSnapshot:
        async with self._scope.connection() as conn:
            return await read_catalog(conn)

    async def _complete(self, system: str, user: str, answer: AnalyticsAnswer) -> str:
        try:
            response = await self._provider.complete(  # type: ignore[union-attr]
                system=system,
                messages=[{"role": "user", "content": user}],
                tools=[],
                max_tokens=2_000,
            )
        except LLMError as exc:
            logger.error("analytics_provider_failed", error=str(exc))
            raise ProviderUnavailableError(
                "Le service d'intelligence artificielle est momentanément "
                "indisponible.",
                remedy_fr="Les tableaux de bord restent accessibles.",
            ) from exc

        answer.usage.append(
            TokenUsage(
                model=response.model,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                cost_usd=estimate_cost_usd(
                    response.model, response.input_tokens, response.output_tokens
                ),
            )
        )
        return _unfence(response.text)

    async def _summarise(
        self, question: str, sql: str, result: QueryResult, answer: AnalyticsAnswer
    ) -> str:
        return await self._complete(
            explain_system(), explain_user(question, sql, result), answer
        )

    # -- refus -------------------------------------------------------------

    @staticmethod
    def _is_security_refusal(report: ValidationReport) -> bool:
        return any(i.is_error and i.code in SECURITY_CODES for i in report.issues)

    def _refuse(
        self,
        answer: AnalyticsAnswer,
        index: int,
        sql: str,
        report: ValidationReport,
    ) -> AnalyticsAnswer:
        """Un refus de sécurité, jamais une relance.

        La requête fautive est conservée dans la trace : elle est la matière de
        l'enquête, et la masquer priverait un exploitant du seul élément utile.
        """
        reasons = tuple(
            i.message for i in report.issues if i.is_error and i.code in SECURITY_CODES
        )
        logger.warning(
            "analytics_security_refusal",
            tenant=str(self._scope.tenant_id),
            codes=[i.code.value for i in report.issues if i.code in SECURITY_CODES],
        )
        answer.attempts.append(
            Attempt(index=index, sql=sql, status="refused", issues_fr=reasons)
        )
        answer.refused = True
        answer.refusal_reason_fr = reasons[0] if reasons else "Requête refusée."
        answer.text_fr = (
            "Cette question a produit une requête que la plateforme refuse "
            "d'exécuter. Aucune donnée n'a été lue. Reformulez la question, ou "
            "signalez-la à un responsable de votre organisation."
        )
        return answer

    def _unrecoverable(self, answer: AnalyticsAnswer, error: DbError) -> AnalyticsAnswer:
        answer.refused = True
        answer.refusal_reason_fr = error.render()
        answer.text_fr = (
            "La requête a été refusée par la base de données pour une raison "
            "qu'une réécriture ne corrigerait pas. Aucune donnée n'a été lue."
        )
        return answer


def _declare_limit_truncation(
    result: QueryResult, report: ValidationReport
) -> QueryResult:
    """Une LIMIT injectée qui « remplit » est une troncature, et doit se dire.

    Le plafond du validateur est poussé dans la requête, ce qui est la bonne
    façon de le faire : la base n'envoie pas des lignes qu'on jetterait ensuite.
    Mais l'exécuteur ne voit alors jamais son propre plafond, donc il ne signale
    rien — et le résumé décrit deux lignes comme s'il n'y en avait que deux,
    alors qu'il y en a sept.

    « Exactement N lignes, où N est le plafond » est indiscernable de « il y en
    avait davantage ». On le déclare donc comme tel : dire qu'un résultat *peut*
    être incomplet coûte une phrase, le présenter comme complet coûte la
    confiance.
    """
    if result.truncated or not report.limit_injected:
        return result
    limit = report.effective_limit
    if limit is None or result.row_count < limit:
        return result
    return result.model_copy(
        update={
            "truncated": True,
            "truncation_reason": (
                f"Résultat limité à {limit} ligne(s) par la plateforme : il peut en "
                "exister davantage."
            ),
        }
    )


def _unfence(text: str) -> str:
    match = _FENCE.match(text or "")
    return (match.group(1) if match else (text or "")).strip().rstrip(";").strip()

