"""Le socle SQL : les cinq couches, et laquelle tient quand les autres cèdent.

Deux fichiers auraient été plus faciles à écrire — un pour le validateur, un
pour l'exécution — et auraient manqué l'essentiel. Ce qui compte n'est pas que
chaque couche fonctionne isolément : c'est que **la couche 2 tienne quand la
couche 1 est fausse**. Ces tests contournent donc délibérément le validateur
pour interroger la base directement, ce qu'aucun chemin applicatif ne permet.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.analytics.evals.harness import load_corpus, run_corpus
from app.analytics.executor import ExecutionSettings, ReadOnlyExecutor
from app.analytics.introspection import read_catalog
from app.analytics.settings import ValidationSettings
from app.analytics.validation.models import (
    SECURITY_CODES,
    SafetyDecision,
    ValidationCode,
)
from app.analytics.validation.pipeline import SQLValidator
from app.db.session import Databases
from tests.conftest import Org

# `asyncio_mode = "auto"` : les tests asynchrones sont détectés sans marqueur.


@pytest.fixture
async def catalog(databases: Databases):
    scope = databases.analytics_for_tenant(uuid.uuid4())
    async with scope.connection() as conn:
        return await read_catalog(conn)


# ---------------------------------------------------------------------------
# Couche 1 — le corpus adverse
# ---------------------------------------------------------------------------
async def test_the_adversarial_corpus_passes_in_full(catalog) -> None:
    """Tous les cas, et **par la couche qui les revendique**.

    Un cas prévu pour la liste blanche de relations mais refusé par la
    résolution de noms passerait au sens du taux d'attrape, et la garantie
    annoncée ne tiendrait pourtant que par accident — jusqu'au jour où le
    catalogue changerait.
    """
    report = run_corpus(catalog)
    assert report.failures == (), "\n".join(report.summary_lines_fr())
    assert report.total >= 47


async def test_every_tenant_crossing_case_is_refused_as_a_security_event(
    catalog,
) -> None:
    """Le franchissement d'organisation n'est pas une faute de frappe.

    « Table inconnue » ferait lire une tentative comme une erreur de nommage, et
    l'événement se perdrait dans le bruit des erreurs de génération. Le code
    `RELATION_NOT_ALLOWED` la classe comme sécurité — donc jamais réparée,
    toujours signalée.
    """
    validator = SQLValidator(ValidationSettings())
    tenant_cases = [c for c in load_corpus() if c.id.startswith("tenant-")]
    assert len(tenant_cases) >= 9

    for case in tenant_cases:
        report = validator.validate(case.sql, catalog, row_limit=500)
        errors = [i for i in report.issues if i.is_error]
        assert errors, case.id
        assert any(i.code in SECURITY_CODES for i in errors), case.id
        assert any(i.code is ValidationCode.RELATION_NOT_ALLOWED for i in errors), (
            f"{case.id} refusé, mais pas comme franchissement : "
            f"{[i.code.value for i in errors]}"
        )


async def test_a_base_table_is_refused_differently_from_a_typo(catalog) -> None:
    """La distinction que le message doit porter."""
    validator = SQLValidator(ValidationSettings())

    crossing = validator.validate(
        "SELECT s.reference FROM app.shipments s LIMIT 10", catalog, row_limit=500
    )
    typo = validator.validate(
        "SELECT s.reference FROM analytics.v_shipmentz s LIMIT 10", catalog, row_limit=500
    )

    crossing_codes = {i.code for i in crossing.issues if i.is_error}
    typo_codes = {i.code for i in typo.issues if i.is_error}

    assert ValidationCode.RELATION_NOT_ALLOWED in crossing_codes
    assert ValidationCode.RELATION_NOT_ALLOWED not in typo_codes
    assert ValidationCode.UNKNOWN_TABLE in typo_codes
    # Et l'une est un événement de sécurité, l'autre non.
    assert crossing_codes & SECURITY_CODES
    assert not (typo_codes & SECURITY_CODES)


async def test_a_valid_analytics_query_passes_and_gains_a_limit(catalog) -> None:
    """Le socle refuse beaucoup ; il doit laisser passer ce qui est légitime."""
    validator = SQLValidator(ValidationSettings())
    report = validator.validate(
        "SELECT s.reference, s.volume_tonnes FROM analytics.v_shipments s",
        catalog,
        row_limit=500,
    )
    assert not [i for i in report.issues if i.is_error], [
        i.message for i in report.issues
    ]
    assert report.limit_injected is True
    assert "LIMIT 500" in (report.normalized_sql or "").upper()


async def test_an_existing_limit_is_lowered_never_raised(catalog) -> None:
    """Un utilisateur qui demande 10 lignes ne doit pas en recevoir 500."""
    validator = SQLValidator(ValidationSettings())
    report = validator.validate(
        "SELECT s.reference FROM analytics.v_shipments s LIMIT 10",
        catalog,
        row_limit=500,
    )
    assert report.effective_limit == 10


# ---------------------------------------------------------------------------
# Couche 2 — celle qui ne dépend pas de notre code
# ---------------------------------------------------------------------------
async def test_the_analytics_role_cannot_read_base_tables_at_all(
    databases: Databases, two_orgs: tuple[Org, Org]
) -> None:
    """Le validateur contourné, la couche 2 tient encore.

    Ce test envoie délibérément à la base un SQL qu'aucun chemin applicatif ne
    laisserait passer. C'est le point : la couche 2 est la seule qui tienne
    quand notre propre code est le bug.
    """
    alpha, _ = two_orgs
    scope = databases.analytics_for_tenant(alpha.tenant_id)
    async with scope.connection() as conn:
        with pytest.raises(Exception) as excinfo:
            await conn.execute(text("SELECT id FROM app.shipments LIMIT 1"))
    assert "permission" in str(excinfo.value).lower() or "denied" in str(
        excinfo.value
    ).lower()


async def test_a_view_returns_only_the_calling_organisation(
    databases: Databases, demo_org: Org, two_orgs: tuple[Org, Org]
) -> None:
    """Deux organisations, la même vue, deux résultats.

    Une suite d'isolation avec une seule organisation ne peut prouver que
    l'absence de données, jamais la présence d'une frontière.
    """
    alpha, _ = two_orgs

    async with databases.analytics_for_tenant(demo_org.tenant_id).connection() as conn:
        mine = (
            await conn.execute(text("SELECT count(*) FROM analytics.v_fields"))
        ).scalar_one()
    async with databases.analytics_for_tenant(alpha.tenant_id).connection() as conn:
        theirs = (
            await conn.execute(text("SELECT count(*) FROM analytics.v_fields"))
        ).scalar_one()

    assert mine > 0
    assert theirs == 0


async def test_a_write_is_refused_inside_the_read_only_transaction(
    databases: Databases, two_orgs: tuple[Org, Org]
) -> None:
    """`nextval()` est une écriture qu'un arbre syntaxique ne distingue pas."""
    from sqlalchemy.exc import DBAPIError

    alpha, _ = two_orgs
    scope = databases.analytics_for_tenant(alpha.tenant_id)
    async with scope.connection() as conn:
        with pytest.raises(DBAPIError) as excinfo:
            await conn.execute(text("CREATE TEMP TABLE fuite AS SELECT 1 AS x"))
    # Refusée par la transaction en lecture seule ou par le privilège : les deux
    # sont la couche 2, et aucune n'est notre code.
    message = str(excinfo.value).lower()
    assert "read-only" in message or "permission" in message or "denied" in message


# ---------------------------------------------------------------------------
# Couches 3 et 4 — bornes et EXPLAIN
# ---------------------------------------------------------------------------
async def test_explain_catches_what_the_ast_layer_cannot(
    databases: Databases, demo_org: Org, catalog
) -> None:
    """Le contrôle sémantique faisant autorité.

    Le corpus classe ces deux cas comme « non attrapés à la couche 1 » : c'est
    la démonstration honnête de ce que l'analyse statique ne peut pas faire, et
    ce test montre qui les attrape ensuite.
    """
    executor = ReadOnlyExecutor(
        databases.analytics_for_tenant(demo_org.tenant_id), ExecutionSettings()
    )
    report = await executor.explain(
        "SELECT s.reference FROM analytics.v_shipments s WHERE s.status > 5 LIMIT 10"
    )
    assert report.decision is SafetyDecision.SEMANTIC_ERROR
    assert report.message


async def test_explain_allows_a_legitimate_query(
    databases: Databases, demo_org: Org
) -> None:
    executor = ReadOnlyExecutor(
        databases.analytics_for_tenant(demo_org.tenant_id), ExecutionSettings()
    )
    report = await executor.explain(
        "SELECT s.reference FROM analytics.v_shipments s LIMIT 10"
    )
    assert report.decision is SafetyDecision.ALLOW
    assert report.total_cost is not None


async def test_execution_is_bounded_by_the_row_cap(
    databases: Databases, demo_org: Org
) -> None:
    """Et la troncature est **déclarée**, pas subie en silence.

    Décrire un résultat tronqué comme s'il était complet est une faute de
    correction, pas de présentation.
    """
    executor = ReadOnlyExecutor(
        databases.analytics_for_tenant(demo_org.tenant_id),
        ExecutionSettings(max_rows=2),
    )
    outcome = await executor.execute(
        "SELECT f.code, f.area_ha FROM analytics.v_fields f LIMIT 50"
    )
    assert outcome.ok
    assert outcome.result is not None
    assert outcome.result.row_count == 2
    assert outcome.result.truncated is True
    assert "tronqué" in (outcome.result.truncation_reason or "")


async def test_the_executor_cannot_be_built_without_an_organisation() -> None:
    """La garantie est structurelle, pas conventionnelle.

    `AnalyticsScope` exige un `tenant_id` à la construction ; il n'existe donc
    aucun exécuteur analytique sans organisation, et aucun appelant distrait ne
    peut en fabriquer un.
    """
    import inspect

    from app.db.session import AnalyticsScope

    parameters = inspect.signature(AnalyticsScope).parameters
    assert "tenant_id" in parameters
    assert parameters["tenant_id"].default is inspect.Parameter.empty


async def test_a_failed_execution_is_classified_not_raised(
    databases: Databases, demo_org: Org
) -> None:
    """Une erreur de base devient une donnée classée, relayable au réparateur."""
    executor = ReadOnlyExecutor(
        databases.analytics_for_tenant(demo_org.tenant_id), ExecutionSettings()
    )
    outcome = await executor.execute("SELECT nexistepas FROM analytics.v_fields")
    assert not outcome.ok
    assert outcome.error is not None
    assert outcome.error.classification.value in {"missing_object", "syntax"}


async def test_every_multilingual_reference_is_valid_against_the_live_catalog(
    catalog,
) -> None:
    """Le corpus, pas le modèle.

    Une référence écrite contre une colonne qui n'existe plus transformerait
    silencieusement la suite en test de rien : elle mesurerait la capacité du
    modèle à reproduire une erreur.
    """
    from app.analytics.evals.multilingual import check_references

    report = check_references(catalog)
    assert report.failures == (), "\n".join(report.summary_lines_fr())
    assert report.checked >= 11


async def test_the_multilingual_corpus_covers_both_product_languages() -> None:
    from app.analytics.evals.multilingual import load_multilingual

    languages = {case.language for case in load_multilingual()}
    assert {"fr", "ar"} <= languages


async def test_the_trap_cases_have_no_reference_because_none_is_correct() -> None:
    """Un piège n'a pas de bonne réponse SQL, et ne doit pas en recevoir une.

    Lui en donner une ferait passer une tentative de franchissement pour une
    question ordinaire dont on connaît la réponse.
    """
    from app.analytics.evals.multilingual import load_multilingual

    traps = [c for c in load_multilingual() if c.id.startswith("ml-trap-")]
    assert traps
    injection = next(c for c in traps if "app.tenants" in c.question)
    assert injection.reference_sql is None
