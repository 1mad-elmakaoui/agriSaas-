"""Les garde-fous refusent-ils vraiment ?

Une sonde qui ne peut pas échouer ne prouve rien, et se remarque d'autant moins
qu'elle affiche « pass ». Chaque test ici **casse délibérément** une garantie,
vérifie que la sonde correspondante la voit, puis remet l'état d'origine.

Ce fichier est le complément indispensable de `test_tenant_isolation.py` : l'un
montre que l'isolation fonctionne, l'autre que les contrôles qui la surveillent
sont capables de dire le contraire.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.db.preflight import (
    ProbeOutcome,
    _probe_analytics_cannot_reach_base_tables,
    _probe_analytics_view_chain,
    _probe_cross_tenant,
    _probe_no_materialized_views,
    _probe_rls_coverage,
)
from app.db.session import Databases

# `asyncio_mode = "auto"` : les tests asynchrones sont détectés, et un marqueur
# explicite ferait avertir pytest sur les tests synchrones du même module.


@pytest_asyncio.fixture
async def owner_engine() -> AsyncIterator[AsyncEngine]:
    dsn = os.environ["ATLAS_TEST_OWNER_DATABASE_URL"]
    engine = create_async_engine(dsn)
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_rls_coverage_probe_sees_an_unprotected_table(
    databases: Databases, owner_engine: AsyncEngine
) -> None:
    """Une table portant `tenant_id` sans politique doit être détectée.

    C'est le scénario réaliste : quelqu'un ajoute un modèle en phase 3 et oublie
    la migration de politique. Rien ne casse, les requêtes réussissent — elles
    renvoient simplement les lignes de tout le monde.
    """
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("CREATE TABLE app.__leaky (id int, tenant_id uuid)")
        )
    try:
        result = await _probe_rls_coverage(databases.app_engine)
        assert result.outcome is ProbeOutcome.FAIL
        assert "__leaky" in result.detail
    finally:
        async with owner_engine.begin() as conn:
            await conn.execute(text("DROP TABLE app.__leaky"))

    # ... et repasse une fois la table retirée : sans cela, le test prouverait
    # seulement que la sonde échoue, pas qu'elle discrimine.
    assert (await _probe_rls_coverage(databases.app_engine)).outcome is ProbeOutcome.PASS


async def test_rls_coverage_probe_sees_enable_without_force(
    databases: Databases, owner_engine: AsyncEngine
) -> None:
    """`ENABLE` sans `FORCE` est la fuite silencieuse : la sonde doit la voir.

    Politique présente, RLS activée, `\\d` rassurant — et une vue détenue par le
    propriétaire de la table renvoie toutes les organisations. C'est le défaut
    exact que la phase 0 a mesuré, et la seule chose qui le distingue d'une
    installation correcte est ce drapeau.
    """
    async with owner_engine.begin() as conn:
        await conn.execute(text("ALTER TABLE app.sites NO FORCE ROW LEVEL SECURITY"))
    try:
        result = await _probe_rls_coverage(databases.app_engine)
        assert result.outcome is ProbeOutcome.FAIL
        assert "sites" in result.detail
    finally:
        async with owner_engine.begin() as conn:
            await conn.execute(text("ALTER TABLE app.sites FORCE ROW LEVEL SECURITY"))
    assert (await _probe_rls_coverage(databases.app_engine)).outcome is ProbeOutcome.PASS


async def test_cross_tenant_probe_sees_a_dropped_policy(
    databases: Databases, owner_engine: AsyncEngine
) -> None:
    """Politique retirée : la sonde comportementale doit le constater.

    Elle est la seule à mesurer le comportement plutôt que la configuration,
    donc la seule qui attraperait une politique syntaxiquement présente mais
    fonctionnellement fausse.
    """
    async with owner_engine.begin() as conn:
        await conn.execute(text("DROP POLICY tenant_isolation ON app.tenants"))
        # Sans politique, une table RLS n'expose plus rien : on en pose une
        # permissive, ce qui reproduit exactement l'erreur réaliste — une
        # politique qui ne filtre pas.
        await conn.execute(text("CREATE POLICY tenant_isolation ON app.tenants USING (true)"))
    try:
        result = await _probe_cross_tenant(databases.app_engine)
        assert result.outcome is ProbeOutcome.FAIL
        assert "isolate" in result.detail
    finally:
        async with owner_engine.begin() as conn:
            await conn.execute(text("DROP POLICY tenant_isolation ON app.tenants"))
            await conn.execute(
                text(
                    "CREATE POLICY tenant_isolation ON app.tenants "
                    "USING (id = current_setting('app.current_tenant')::uuid) "
                    "WITH CHECK (id = current_setting('app.current_tenant')::uuid)"
                )
            )
    assert (await _probe_cross_tenant(databases.app_engine)).outcome is ProbeOutcome.PASS


async def test_confinement_probe_sees_a_grant_on_a_base_table(
    databases: Databases, owner_engine: AsyncEngine
) -> None:
    """Un `GRANT SELECT` de trop sur `app` doit faire échouer le démarrage.

    C'est la dérive la plus banale : quelqu'un débogue une requête analytique,
    accorde un accès direct « juste pour voir », et le confinement retombe
    entièrement sur le validateur AST sans que rien ne le signale.
    """
    async with owner_engine.begin() as conn:
        await conn.execute(text("GRANT USAGE ON SCHEMA app TO atlas_analytics_ro"))
        await conn.execute(text("GRANT SELECT ON app.sites TO atlas_analytics_ro"))
    try:
        result = await _probe_analytics_cannot_reach_base_tables(databases.analytics_engine)
        assert result.outcome is ProbeOutcome.FAIL
    finally:
        async with owner_engine.begin() as conn:
            await conn.execute(text("REVOKE ALL ON app.sites FROM atlas_analytics_ro"))
            await conn.execute(text("REVOKE ALL ON SCHEMA app FROM atlas_analytics_ro"))
    assert (
        await _probe_analytics_cannot_reach_base_tables(databases.analytics_engine)
    ).outcome is ProbeOutcome.PASS


async def test_materialized_view_probe_sees_one(
    databases: Databases, owner_engine: AsyncEngine
) -> None:
    """Une vue matérialisée dans `analytics` est un instantané non filtrable.

    PostgreSQL refuse d'y attacher une politique. Ajoutée pour la performance,
    elle serait une copie complète de toutes les organisations, et aucune des
    autres couches ne la verrait.
    """
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_tenant', gen_random_uuid()::text, true)")
        )
        await conn.execute(
            text("CREATE MATERIALIZED VIEW analytics.__m AS SELECT 1 AS x")
        )
    try:
        result = await _probe_no_materialized_views(databases.analytics_engine)
        assert result.outcome is ProbeOutcome.FAIL
        assert "__m" in result.detail
    finally:
        async with owner_engine.begin() as conn:
            await conn.execute(text("DROP MATERIALIZED VIEW analytics.__m"))
    assert (
        await _probe_no_materialized_views(databases.analytics_engine)
    ).outcome is ProbeOutcome.PASS


async def test_view_chain_probe_sees_security_invoker(
    databases: Databases, owner_engine: AsyncEngine
) -> None:
    """`security_invoker` sur une vue analytique doit être refusé.

    Mesuré en phase 0 : elle exige que le rôle analytique détienne des
    privilèges sur les tables de base, donc qu'il puisse les interroger
    directement. La vue paraît plus « propre » et détruit le confinement.
    """
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("ALTER VIEW analytics.v_sites SET (security_invoker = true)")
        )
    try:
        result = await _probe_analytics_view_chain(databases.app_engine)
        assert result.outcome is ProbeOutcome.FAIL
        assert "security_invoker" in result.detail
    finally:
        async with owner_engine.begin() as conn:
            await conn.execute(
                text("ALTER VIEW analytics.v_sites SET (security_invoker = false)")
            )
    assert (
        await _probe_analytics_view_chain(databases.app_engine)
    ).outcome is ProbeOutcome.PASS
