"""Outillage en ligne de commande.

Deux commandes, deux niveaux de privilège, et la séparation est le point :

* `seed-reference` écrit les lignes **globales** du référentiel agronomique.
  Elles n'appartiennent à aucune organisation, donc la politique d'isolation ne
  peut pas les écrire : la commande prend le rôle `atlas_owner`, pour lequel une
  politique d'administration est déclarée. C'est une opération d'exploitation,
  au même titre qu'une migration.

* `seed-demo` écrit de la donnée d'organisation ordinaire, sous politique,
  exactement comme une saisie.

Les messages sont en anglais : seul un opérateur les lit.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.seed import DEMO_TENANT_SLUG, ensure_demo_tenant, seed_demo_tenant, seed_reference
from app.db.session import Databases, _normalise_dsn


def _admin_dsn(settings: Settings) -> str:
    import os

    dsn = os.environ.get("ATLAS_MIGRATION_DATABASE_URL")
    if not dsn:
        raise SystemExit(
            "ATLAS_MIGRATION_DATABASE_URL is required for this command: seeding the "
            "global reference tables needs the administrative role, not the "
            "application role."
        )
    return _normalise_dsn(dsn)


async def _seed_reference(settings: Settings) -> None:
    engine = create_async_engine(_admin_dsn(settings))
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with factory() as session, session.begin():
            conn = await session.connection()
            # Le rôle d'administration porte la politique qui autorise l'écriture
            # d'une ligne globale. Sans ce `SET ROLE`, l'insertion échoue sur le
            # `WITH CHECK` — ce qui est le comportement voulu pour tout le reste.
            await conn.execute(text("SET LOCAL ROLE atlas_owner"))
            counts = await seed_reference(session)
        for table, count in sorted(counts.items()):
            print(f"  {table:24} {count}")  # noqa: T201 - sortie de commande
    finally:
        await engine.dispose()


async def _seed_demo(settings: Settings) -> None:
    engine = create_async_engine(_admin_dsn(settings))
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with factory() as session, session.begin():
            conn = await session.connection()
            await conn.execute(text("SET LOCAL ROLE atlas_owner"))
            tenant_id = await ensure_demo_tenant(session)
    finally:
        await engine.dispose()

    databases = Databases(settings)
    try:
        async with databases.for_tenant(tenant_id).begin() as session:
            counts = await seed_demo_tenant(session, tenant_id)
        print(f"  tenant {DEMO_TENANT_SLUG} = {tenant_id}")  # noqa: T201
        for table, count in sorted(counts.items()):
            print(f"  {table:24} {count}")  # noqa: T201
    finally:
        await databases.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas", description="AtlasAgri operations")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed-reference", help="load the global FAO reference tables")
    sub.add_parser("seed-demo", help="create and populate the demonstration tenant")
    args = parser.parse_args(argv)

    configure_logging(json_output=False)
    settings = get_settings()

    if args.command == "seed-reference":
        asyncio.run(_seed_reference(settings))
    elif args.command == "seed-demo":
        asyncio.run(_seed_demo(settings))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
