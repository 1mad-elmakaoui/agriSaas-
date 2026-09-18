"""Contexte Alembic.

Les migrations tournent avec le rôle **propriétaire**, pas avec le rôle
applicatif : elles créent des schémas, des politiques et des vues, ce que le
rôle applicatif n'a pas le droit de faire — et ne doit pas l'avoir.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.db.base import SCHEMA_APP, Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_MIGRATION_URL = os.environ.get("ATLAS_MIGRATION_DATABASE_URL") or os.environ.get(
    "ATLAS_DATABASE_URL", "postgresql+asyncpg://postgres@127.0.0.1:5432/atlas"
)
config.set_main_option("sqlalchemy.url", _MIGRATION_URL)


def _configure(connection: Connection) -> None:
    # Alembic pose sa table de version dans `app`, donc le schéma doit exister
    # avant la première migration — y compris sur une base entièrement vide.
    connection.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA_APP}")
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=SCHEMA_APP,
        include_schemas=True,
        compare_type=True,
    )


def run_migrations_offline() -> None:
    context.configure(
        url=_MIGRATION_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        version_table_schema=SCHEMA_APP,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(lambda sync_conn: (_configure(sync_conn), context.run_migrations()))
        await connection.commit()
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
