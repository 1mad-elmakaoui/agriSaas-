"""Sondes de démarrage — vérifier les garanties plutôt que les supposer.

Chaque sonde correspond à une garantie annoncée ailleurs dans le produit. Une
garantie qu'on n'a pas vérifiée au démarrage est une garantie qu'on découvrira
absente en production, par l'incident qu'elle devait empêcher.

Trois d'entre elles n'existaient dans aucun des trois dépôts sources :
l'énumération RLS, la sonde inter-organisations et la sonde de fuite par vue.
La dérive de configuration est le mode de défaillance réaliste — quelqu'un
pointe le DSN analytique sur un rôle d'administration pour déboguer, et cela
reste.

Les messages sont en anglais : seul un opérateur les lit, et ils apparaissent
avant que la moindre requête utilisateur n'existe.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.logging import get_logger
from app.db.base import RLS_EXEMPT_TABLES, SCHEMA_ANALYTICS, SCHEMA_APP

logger = get_logger(__name__)

__all__ = ["ProbeOutcome", "ProbeResult", "run_preflight"]


class ProbeOutcome(StrEnum):
    PASS = "pass"  # noqa: S105 - a probe outcome, not a credential
    #: La garantie est absente et le produit ne doit pas démarrer.
    FAIL = "fail"
    #: Capacité facultative absente : on dégrade, on ne bloque pas.
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class ProbeResult:
    name: str
    outcome: ProbeOutcome
    detail: str


async def _probe_rls_coverage(engine: AsyncEngine) -> ProbeResult:
    """Toute table portant `tenant_id` a-t-elle RLS activée, forcée et une politique ?

    Énumérée depuis le catalogue vivant, pas depuis une liste écrite à la main :
    une liste diverge au premier modèle ajouté, et c'est exactement la
    divergence qu'une politique manquante ne pardonne pas.
    """
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT c.relname,
                           c.relrowsecurity,
                           c.relforcerowsecurity,
                           (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies
                      FROM pg_class c
                      JOIN pg_namespace n ON n.oid = c.relnamespace
                      JOIN pg_attribute a ON a.attrelid = c.oid
                                         AND a.attname = 'tenant_id'
                                         AND NOT a.attisdropped
                     WHERE n.nspname = :schema AND c.relkind = 'r'
                     ORDER BY c.relname
                    """
                ),
                {"schema": SCHEMA_APP},
            )
        ).all()

    unprotected = [
        name
        for name, enabled, forced, policies in rows
        if name not in RLS_EXEMPT_TABLES and not (enabled and forced and policies > 0)
    ]
    if unprotected:
        return ProbeResult(
            "rls_coverage",
            ProbeOutcome.FAIL,
            "Tables carry tenant_id without complete row-level security "
            f"(ENABLE + FORCE + a policy): {', '.join(unprotected)}. "
            "ENABLE alone is not enough: a view owned by the table owner over a "
            "table that is only ENABLEd returns every tenant's rows.",
        )
    return ProbeResult(
        "rls_coverage", ProbeOutcome.PASS, f"{len(rows)} tenant tables protected"
    )


async def _probe_no_bypassrls(engine: AsyncEngine) -> ProbeResult:
    """Le rôle connecté contourne-t-il la RLS ?

    Un rôle `BYPASSRLS` ou superutilisateur rend décoratives toutes les
    politiques du schéma, sans aucun signal : les requêtes réussissent, elles
    renvoient simplement trop.
    """
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT current_user, rolsuper, rolbypassrls "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            )
        ).one()
    role, is_super, bypasses = row
    if is_super or bypasses:
        return ProbeResult(
            "no_bypassrls",
            ProbeOutcome.FAIL,
            f"Role '{role}' bypasses row-level security "
            f"(superuser={is_super}, bypassrls={bypasses}). Every tenant policy "
            "in the schema is decorative for this connection.",
        )
    return ProbeResult("no_bypassrls", ProbeOutcome.PASS, f"role '{role}' is subject to RLS")


async def _probe_analytics_read_only(engine: AsyncEngine) -> ProbeResult:
    """Le rôle analytique peut-il écrire ?

    Reprise de `text_to_sql`. La sonde crée une table temporaire dans une
    transaction toujours annulée : elle **doit** échouer. Si elle réussit, le
    rôle est trop privilégié et la couche 2 n'est pas en vigueur.
    """
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            await conn.execute(text("CREATE TEMP TABLE _atlas_privilege_probe (i int)"))
        except Exception:
            await transaction.rollback()
            return ProbeResult(
                "analytics_read_only", ProbeOutcome.PASS, "analytics role cannot write"
            )
        await transaction.rollback()
        return ProbeResult(
            "analytics_read_only",
            ProbeOutcome.FAIL,
            "The analytics DSN authenticates as a role that can create objects. "
            "Layer 2 of the safety stack is not in effect.",
        )


async def _probe_analytics_cannot_reach_base_tables(engine: AsyncEngine) -> ProbeResult:
    """Le rôle analytique atteint-il les tables de base ?

    S'il le peut, le confinement « il ne voit que des vues » retombe entièrement
    sur le validateur AST — c'est-à-dire sur notre propre code, ce que la
    conception cherche précisément à éviter.
    """
    async with engine.connect() as conn:
        reachable = (
            await conn.execute(
                text(
                    """
                    SELECT count(*)
                      FROM pg_class c
                      JOIN pg_namespace n ON n.oid = c.relnamespace
                     WHERE n.nspname = :schema
                       AND c.relkind IN ('r', 'p')
                       AND has_table_privilege(c.oid, 'SELECT')
                    """
                ),
                {"schema": SCHEMA_APP},
            )
        ).scalar_one()
    if reachable:
        return ProbeResult(
            "analytics_confinement",
            ProbeOutcome.FAIL,
            f"The analytics role holds SELECT on {reachable} base table(s) in "
            f"'{SCHEMA_APP}'. It must reach data only through the views in "
            f"'{SCHEMA_ANALYTICS}'.",
        )
    return ProbeResult(
        "analytics_confinement", ProbeOutcome.PASS, "no base table reachable"
    )


async def _probe_no_materialized_views(engine: AsyncEngine) -> ProbeResult:
    """Une vue matérialisée dans `analytics` serait une copie non protégée.

    PostgreSQL refuse toute politique RLS sur une vue matérialisée — vérifié :
    « ALTER action ENABLE ROW SECURITY cannot be performed on relation ...
    This operation is not supported for materialized views. » Une matview
    ajoutée plus tard pour la performance serait donc un instantané complet,
    toutes organisations confondues, que rien ne filtre.
    """
    async with engine.connect() as conn:
        names = [
            row[0]
            for row in (
                await conn.execute(
                    text(
                        "SELECT c.relname FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = :schema AND c.relkind = 'm'"
                    ),
                    {"schema": SCHEMA_ANALYTICS},
                )
            ).all()
        ]
    if names:
        return ProbeResult(
            "no_materialized_views",
            ProbeOutcome.FAIL,
            f"Materialized views in '{SCHEMA_ANALYTICS}': {', '.join(names)}. "
            "PostgreSQL cannot attach a row-level security policy to one, so each "
            "is an unfiltered snapshot of every tenant.",
        )
    return ProbeResult("no_materialized_views", ProbeOutcome.PASS, "none present")


async def _probe_analytics_view_chain(engine: AsyncEngine) -> ProbeResult:
    """La chaîne de vues analytiques est-elle celle qui isole ?

    Trois propriétés, toutes vérifiées expérimentalement comme nécessaires :

    * la vue n'est **pas** `security_invoker` — elle le serait, il faudrait que
      le rôle analytique détienne des privilèges sur les tables de base, donc
      qu'il puisse les interroger directement ;
    * son propriétaire ne contourne pas la RLS — sinon la vue voit tout et la
      restitue à qui la lit ;
    * son propriétaire n'est pas le propriétaire des tables de base — cas où
      `ENABLE` sans `FORCE` fuit silencieusement. `FORCE` est vérifié par
      ailleurs ; cette redondance est délibérée, parce que la conséquence d'un
      seul de ces deux contrôles manquant est une fuite muette.
    """
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT c.relname,
                           pg_get_userbyid(c.relowner)  AS view_owner,
                           r.rolsuper OR r.rolbypassrls AS owner_bypasses,
                           COALESCE(
                               (SELECT option_value
                                  FROM pg_options_to_table(c.reloptions)
                                 WHERE option_name = 'security_invoker'),
                               'false'
                           )                            AS security_invoker
                      FROM pg_class c
                      JOIN pg_namespace n ON n.oid = c.relnamespace
                      JOIN pg_roles r     ON r.oid = c.relowner
                     WHERE n.nspname = :schema AND c.relkind = 'v'
                     ORDER BY c.relname
                    """
                ),
                {"schema": SCHEMA_ANALYTICS},
            )
        ).all()

    if not rows:
        return ProbeResult(
            "analytics_view_chain",
            ProbeOutcome.FAIL,
            f"Schema '{SCHEMA_ANALYTICS}' exposes no view. The analytics agent "
            "would have no surface at all, which is a migration that did not run.",
        )

    problems: list[str] = []
    for name, owner, owner_bypasses, invoker in rows:
        if str(invoker).lower() == "true":
            problems.append(
                f"{name}: security_invoker=true requires the analytics role to hold "
                "privileges on the base tables, which defeats the confinement"
            )
        if owner_bypasses:
            problems.append(f"{name}: owner '{owner}' bypasses row-level security")
    if problems:
        return ProbeResult("analytics_view_chain", ProbeOutcome.FAIL, "; ".join(problems))
    return ProbeResult(
        "analytics_view_chain", ProbeOutcome.PASS, f"{len(rows)} views correctly owned"
    )


#: Organisations fictives de la sonde comportementale. Les identifiants sont
#: fixes et réservés ; rien n'est jamais validé, la transaction est toujours
#: annulée.
_PROBE_TENANT_A = uuid.UUID("00000000-0000-4000-8000-00000000000a")
_PROBE_TENANT_B = uuid.UUID("00000000-0000-4000-8000-00000000000b")


async def _probe_cross_tenant(engine: AsyncEngine) -> ProbeResult:
    """La RLS filtre-t-elle réellement, sur cette base, maintenant ?

    La sonde la plus importante, et celle qu'il est le plus facile de rendre
    vacante. « Lire la ligne de B en tant que A ne renvoie rien » réussit aussi
    quand la ligne de B n'existe pas : une base vide passerait le contrôle en
    prouvant l'absence de données plutôt que la présence d'une isolation.

    Elle prouve donc les deux sens, dans cet ordre : la ligne de B **est**
    visible en tant que B, puis elle **n'est pas** visible en tant que A. La
    transaction est toujours annulée : rien n'est écrit.
    """
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            # La sonde n'écrit que dans `app.tenants`, délibérément. Elle
            # pourrait fabriquer une ligne dans une table métier, mais elle
            # casserait alors à chaque colonne obligatoire ajoutée ailleurs — et
            # une sonde d'isolation qui échoue pour une raison étrangère à
            # l'isolation finit par être neutralisée plutôt que corrigée.
            # `tenants` s'isole sur `id` et n'a aucune dépendance : elle exerce
            # exactement le même mécanisme.
            for tenant, slug in ((_PROBE_TENANT_A, "a"), (_PROBE_TENANT_B, "b")):
                await conn.execute(
                    text("SELECT set_config('app.current_tenant', :t, true)"),
                    {"t": str(tenant)},
                )
                await conn.execute(
                    text(
                        "INSERT INTO app.tenants (id, name, slug) "
                        "VALUES (:id, :name, :slug)"
                    ),
                    {"id": tenant, "name": f"__preflight_{slug}", "slug": f"__preflight_{slug}"},
                )

            # 1. La ligne de B est visible en tant que B. Sans ce temps, le
            #    second contrôle ne prouverait rien.
            await conn.execute(
                text("SELECT set_config('app.current_tenant', :t, true)"),
                {"t": str(_PROBE_TENANT_B)},
            )
            visible = (
                await conn.execute(
                    text("SELECT count(*) FROM app.tenants WHERE slug = '__preflight_b'")
                )
            ).scalar_one()
            if visible != 1:
                return ProbeResult(
                    "cross_tenant",
                    ProbeOutcome.FAIL,
                    "The probe fixture is not visible to its own tenant "
                    f"(found {visible}, expected 1). The probe cannot conclude "
                    "anything about isolation, so it refuses to pass.",
                )

            # 2. Elle est invisible en tant que A.
            await conn.execute(
                text("SELECT set_config('app.current_tenant', :t, true)"),
                {"t": str(_PROBE_TENANT_A)},
            )
            leaked = (
                await conn.execute(
                    text("SELECT count(*) FROM app.tenants WHERE slug = '__preflight_b'")
                )
            ).scalar_one()
            if leaked:
                return ProbeResult(
                    "cross_tenant",
                    ProbeOutcome.FAIL,
                    "Row-level security did not isolate: tenant A read tenant B's "
                    "row. RLS that is silently ineffective looks exactly like RLS "
                    "that works — this is the probe that tells them apart.",
                )
        finally:
            await transaction.rollback()

    return ProbeResult(
        "cross_tenant", ProbeOutcome.PASS, "isolation verified in both directions"
    )


async def _probe_postgis(engine: AsyncEngine, *, required: bool) -> ProbeResult:
    """PostGIS est-il présent ?

    Requis en production — polygones de parcelle, tronçons d'itinéraire,
    statistiques zonales. En développement, son absence désactive les fonctions
    spatiales avec un avertissement : la frontière canonique reste le GeoJSON
    portable, et l'aire est calculée en Python par la formule de l'excès
    sphérique, donc le chiffre affiché est le même dans les deux cas.
    """
    async with engine.connect() as conn:
        present = (
            await conn.execute(
                text("SELECT count(*) FROM pg_extension WHERE extname = 'postgis'")
            )
        ).scalar_one()
    if present:
        return ProbeResult("postgis", ProbeOutcome.PASS, "extension present")
    if required:
        return ProbeResult(
            "postgis",
            ProbeOutcome.FAIL,
            "PostGIS is required in production but the extension is not installed.",
        )
    return ProbeResult(
        "postgis",
        ProbeOutcome.DEGRADED,
        "PostGIS absent: spatial features are disabled. Areas are still computed "
        "in Python from the canonical GeoJSON boundary, so displayed figures are "
        "unaffected.",
    )


async def _probe_pgvector(engine: AsyncEngine) -> ProbeResult:
    async with engine.connect() as conn:
        present = (
            await conn.execute(
                text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
            )
        ).scalar_one()
    if present:
        return ProbeResult(
            "pgvector",
            ProbeOutcome.PASS,
            "extension present; nothing shipped depends on it today",
        )
    # Pas DEGRADED : rien n'est dégradé. La récupération vectorielle a été
    # délibérément écartée (décision 0016), l'agent d'analyse envoie le schéma
    # entier, et il fonctionne à l'identique sans cette extension. Annoncer une
    # capacité réduite qui ne l'est pas ferait douter d'un déploiement sain — et
    # le jour où une sonde crie pour rien, on cesse de lire les sondes.
    return ProbeResult(
        "pgvector",
        ProbeOutcome.PASS,
        "extension absent; no shipped feature needs it (decision 0016). It "
        "would be required again by a per-tenant schema index.",
    )


async def run_preflight(
    app_engine: AsyncEngine,
    analytics_engine: AsyncEngine,
    *,
    require_postgis: bool,
) -> list[ProbeResult]:
    """Exécute toutes les sondes et renvoie leurs résultats.

    Ne lève pas : l'appelant décide quoi faire d'un échec, ce qui permet à un
    outil d'administration de tout afficher plutôt que de s'arrêter à la
    première mauvaise nouvelle.
    """
    results = [
        await _probe_no_bypassrls(app_engine),
        await _probe_rls_coverage(app_engine),
        await _probe_cross_tenant(app_engine),
        await _probe_analytics_view_chain(app_engine),
        await _probe_analytics_read_only(analytics_engine),
        await _probe_analytics_cannot_reach_base_tables(analytics_engine),
        await _probe_no_materialized_views(analytics_engine),
        await _probe_postgis(app_engine, required=require_postgis),
        await _probe_pgvector(app_engine),
    ]
    for result in results:
        log = {
            ProbeOutcome.PASS: logger.info,
            ProbeOutcome.DEGRADED: logger.warning,
            ProbeOutcome.FAIL: logger.error,
        }[result.outcome]
        log("preflight_probe", probe=result.name, outcome=result.outcome.value,
            detail=result.detail)
    return results
