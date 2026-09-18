"""Lecture du catalogue analytique.

Un instantané des **vues** de `analytics`, et de rien d'autre. Trois écarts
délibérés avec la lecture de catalogue du système d'origine, tous dictés par la
multi-location :

1. **Un seul schéma.** La requête ne demande jamais `app`. Une table de base ne
   peut donc pas entrer dans le catalogue par accident, ce qui rend la liste
   blanche de relations cohérente avec ce que le validateur sait résoudre.
2. **Aucune valeur d'exemple tirée des données.** Elles viennent du registre
   déclaré (`enums.py`). Voir la ligne 14 du registre d'honnêteté.
3. **Le graphe de jointure est déclaré, pas lu.** Une vue ne porte pas de clé
   étrangère : PostgreSQL n'en attache pas. Sans graphe, l'expansion par clés
   étrangères de la récupération de schéma n'a rien à parcourir, et le modèle
   invente des chemins de jointure entre des vues qui n'en ont pas.

`has_table_privilege` filtre côté serveur : ce que le rôle analytique ne peut pas
lire n'apparaît pas dans l'instantané, même si ce fichier se trompait de schéma.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.analytics.catalog import (
    CatalogSnapshot,
    ColumnInfo,
    ColumnRef,
    DescriptionSource,
    ForeignKey,
    TableInfo,
    TableKind,
    TableRef,
)
from app.analytics.enums import ENUM_VALUES
from app.analytics.validation.relations import ANALYTICS_SCHEMA
from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = ["JOIN_GRAPH", "read_catalog"]

#: Chemins de jointure entre vues, déclarés parce qu'aucune vue ne porte de clé
#: étrangère. Volontairement court : les vues sont dénormalisées — `v_fields`
#: porte déjà `site_code` et `crop_code` — donc la plupart des questions ne
#: demandent aucune jointure. Celles qui restent en demandent une vraie.
JOIN_GRAPH: tuple[tuple[str, tuple[str, ...], str, tuple[str, ...]], ...] = (
    ("v_soil_moisture_readings", ("field_code",), "v_fields", ("code",)),
    ("v_fields", ("site_code",), "v_sites", ("code",)),
    ("v_shipments", ("origin_site_code",), "v_sites", ("code",)),
    ("v_shipments", ("destination_site_code",), "v_sites", ("code",)),
    ("v_shipments", ("source_field_code",), "v_fields", ("code",)),
    ("v_weather_daily", ("site_id",), "v_sites", ("id",)),
)

_VIEWS_SQL = text(
    """
    SELECT c.relname                          AS view_name,
           obj_description(c.oid, 'pg_class') AS comment
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = :schema
      AND c.relkind = 'v'
      AND has_table_privilege(c.oid, 'SELECT')
    ORDER BY c.relname
    """
)

_COLUMNS_SQL = text(
    """
    SELECT c.relname                              AS view_name,
           a.attname                              AS column_name,
           format_type(a.atttypid, a.atttypmod)   AS data_type,
           NOT a.attnotnull                       AS is_nullable,
           a.attnum                               AS ordinal,
           col_description(c.oid, a.attnum)       AS comment
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.oid
    WHERE n.nspname = :schema
      AND c.relkind = 'v'
      AND a.attnum > 0
      AND NOT a.attisdropped
      AND has_table_privilege(c.oid, 'SELECT')
    ORDER BY c.relname, a.attnum
    """
)


async def read_catalog(
    connection: AsyncConnection, *, schema: str = ANALYTICS_SCHEMA
) -> CatalogSnapshot:
    """Instantané des vues lisibles par le rôle courant."""
    views = (await connection.execute(_VIEWS_SQL, {"schema": schema})).mappings().all()
    columns = (await connection.execute(_COLUMNS_SQL, {"schema": schema})).mappings().all()

    by_view: dict[str, list[ColumnInfo]] = {}
    for row in columns:
        view = row["view_name"]
        by_view.setdefault(view, []).append(
            ColumnInfo(
                ref=ColumnRef(schema_name=schema, table=view, column=row["column_name"]),
                data_type=row["data_type"],
                is_nullable=bool(row["is_nullable"]),
                ordinal=int(row["ordinal"]),
                comment=row["comment"],
                sample_values=ENUM_VALUES.get(f"{view}.{row['column_name']}", ()),
            )
        )

    names = {row["view_name"] for row in views}
    foreign_keys = _declared_foreign_keys(schema, names)

    tables = tuple(
        TableInfo(
            ref=TableRef(schema_name=schema, table=row["view_name"]),
            kind=TableKind.VIEW,
            comment=row["comment"],
            description=row["comment"],
            description_source=(
                DescriptionSource.COMMENT if row["comment"] else DescriptionSource.TEMPLATE
            ),
            columns=tuple(by_view.get(row["view_name"], ())),
            foreign_keys=tuple(foreign_keys.get(row["view_name"], ())),
        )
        for row in views
    )

    logger.info("analytics_catalog_read", views=len(tables), schema=schema)
    return CatalogSnapshot(
        fetched_at=datetime.now(UTC), tables=tables, search_path=(schema,)
    )


def _declared_foreign_keys(
    schema: str, present: set[str]
) -> dict[str, list[ForeignKey]]:
    """Traduit `JOIN_GRAPH` en contraintes, pour les vues réellement présentes.

    Filtrer sur les vues présentes n'est pas une précaution de style : un
    chemin déclaré vers une vue absente ferait construire un `TableRef` que le
    catalogue ne connaît pas, et l'expansion de récupération suivrait une arête
    menant nulle part.
    """
    out: dict[str, list[ForeignKey]] = {}
    for source, source_columns, target, target_columns in JOIN_GRAPH:
        if source not in present or target not in present:
            continue
        out.setdefault(source, []).append(
            ForeignKey(
                constraint_name=f"declared_{source}_{'_'.join(source_columns)}",
                source=TableRef(schema_name=schema, table=source),
                source_columns=source_columns,
                target=TableRef(schema_name=schema, table=target),
                target_columns=target_columns,
            )
        )
    return out
