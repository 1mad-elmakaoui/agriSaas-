"""Outillage en ligne de commande.

Quatre commandes, et la séparation des privilèges est le point :

* `seed-reference` écrit les lignes **globales** du référentiel agronomique.
  Elles n'appartiennent à aucune organisation, donc la politique d'isolation ne
  peut pas les écrire : la commande prend le rôle `atlas_owner`, pour lequel une
  politique d'administration est déclarée. C'est une opération d'exploitation,
  au même titre qu'une migration.

* `seed-demo` écrit de la donnée d'organisation ordinaire, sous politique,
  exactement comme une saisie.

* `set-role-passwords` donne un mot de passe aux deux rôles de connexion. La
  migration les crée **sans** mot de passe — un mot de passe dans une migration
  est un mot de passe versionné, donc public. Tant que personne ne lance cette
  commande, les rôles n'ouvrent une session que là où l'authentification est
  locale et fait confiance à l'utilisateur système ; par TCP, ils sont refusés.
  C'est ce qui a fait échouer la première pile Docker jamais démarrée.

* `create-user` crée un compte. Nécessaire parce que `seed-demo` peuple une
  organisation **sans personne dedans** : une pile fraîchement démarrée offrait
  jusqu'ici un produit complet que personne ne pouvait ouvrir. Le mot de passe
  vient de l'environnement, jamais d'un argument — un argument se lit dans `ps`
  et dans l'historique du shell.

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
from app.domain.enums import UserRole


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
            # Idempotente, et vérifiée avant d'écrire plutôt que rattrapée après.
            # Une pile qu'on redémarre rejoue cette commande : sans ce contrôle,
            # le second `docker compose up` échouait sur la contrainte d'unicité
            # des sites, et la commande suivante — celle qui crée le compte de
            # démonstration — ne s'exécutait jamais.
            already = (
                await session.execute(text("SELECT count(*) FROM app.sites"))
            ).scalar_one()
            if already:
                print(  # noqa: T201
                    f"  tenant {DEMO_TENANT_SLUG} already populated "
                    f"({already} sites), unchanged"
                )
                return
            counts = await seed_demo_tenant(session, tenant_id)
        print(f"  tenant {DEMO_TENANT_SLUG} = {tenant_id}")  # noqa: T201
        for table, count in sorted(counts.items()):
            print(f"  {table:24} {count}")  # noqa: T201
    finally:
        await databases.dispose()


#: Les deux rôles qui ouvrent une session. `atlas_owner` et `analytics_owner`
#: sont NOLOGIN par conception : on ne leur donne pas de mot de passe, il n'y
#: aurait rien à en faire.
def _roles() -> tuple[UserRole, ...]:
    """Les rôles applicatifs, lus depuis l'énumération du domaine.

    Lus plutôt que recopiés : une liste écrite ici serait fausse le jour où un
    rôle est ajouté, et l'erreur serait un choix impossible à taper.
    """
    return tuple(UserRole)


LOGIN_ROLES: tuple[tuple[str, str], ...] = (
    ("atlas_app", "ATLAS_APP_ROLE_PASSWORD"),
    ("atlas_analytics_ro", "ATLAS_ANALYTICS_ROLE_PASSWORD"),
)


async def _set_role_passwords(settings: Settings) -> None:
    """Pose le mot de passe des rôles de connexion, depuis l'environnement.

    Depuis l'environnement et non depuis un argument : un mot de passe passé en
    argument apparaît dans `ps`, dans l'historique du shell et dans les journaux
    du superviseur.

    Un rôle dont la variable est absente est **laissé tel quel** et signalé. Lui
    poser un mot de passe vide le rendrait inutilisable sans rien annoncer.
    """
    import os

    pending = [(role, os.environ.get(var), var) for role, var in LOGIN_ROLES]
    missing = [var for _, value, var in pending if not value]
    if len(missing) == len(pending):
        raise SystemExit(
            "No role password given. Set " + " and ".join(missing) + "."
        )

    engine = create_async_engine(_admin_dsn(settings))
    try:
        async with engine.begin() as conn:
            for role, value, var in pending:
                if not value:
                    print(f"  {role:24} unchanged ({var} is not set)")  # noqa: T201
                    continue
                # Le mot de passe est une **valeur littérale** dans du DDL :
                # PostgreSQL n'accepte pas de paramètre lié ici. Il est donc
                # échappé comme une chaîne SQL, et le nom du rôle vient de la
                # constante ci-dessus, jamais d'une entrée.
                quoted = "'" + value.replace("'", "''") + "'"
                await conn.execute(text(f"ALTER ROLE {role} PASSWORD {quoted}"))
                print(f"  {role:24} password set")  # noqa: T201
    finally:
        await engine.dispose()


async def _create_user(settings: Settings, args: argparse.Namespace) -> None:
    """Crée un compte dans une organisation existante, désignée par son slug.

    Idempotente : un courriel déjà présent n'est pas une erreur. `docker compose
    up` relancé deux fois ne doit pas échouer sur la seconde.
    """
    import os

    from app.services.auth_service import AuthService

    password = os.environ.get("ATLAS_NEW_USER_PASSWORD")
    if not password:
        raise SystemExit(
            "ATLAS_NEW_USER_PASSWORD is required: a password given as an argument "
            "is visible in ps and in the shell history."
        )

    engine = create_async_engine(_admin_dsn(settings))
    try:
        async with engine.begin() as conn:
            # Pas de `SET LOCAL ROLE atlas_owner` ici, contrairement au semis :
            # retrouver une organisation par son slug est une lecture
            # **inter-organisations**, et `atlas_owner` y est soumis à la
            # politique d'isolation comme tout le monde (`FORCE`). C'est donc le
            # rôle d'exploitation qui lit — celui qui crée déjà les schémas, les
            # rôles et les extensions, et dont le guide de déploiement dit qu'il
            # est privilégié.
            tenant_id = (
                await conn.execute(
                    text("SELECT id FROM app.tenants WHERE slug = :slug"),
                    {"slug": args.tenant_slug},
                )
            ).scalar_one_or_none()
            if tenant_id is None:
                raise SystemExit(f"No organisation with slug '{args.tenant_slug}'.")
            existing = (
                await conn.execute(
                    text("SELECT 1 FROM app.user_directory WHERE email = :e"),
                    {"e": args.email.lower()},
                )
            ).scalar_one_or_none()
    finally:
        await engine.dispose()

    if existing is not None:
        print(f"  {args.email:32} already exists, unchanged")  # noqa: T201
        return

    databases = Databases(settings)
    try:
        service = AuthService(
            databases,
            jwt_secret=settings.jwt_secret,
            ttl_minutes=settings.jwt_ttl_minutes,
        )
        user_id = await service.provision_user(
            tenant_id=tenant_id,
            email=args.email,
            full_name=args.name,
            password=password,
            role=UserRole(args.role),
        )
        print(f"  {args.email:32} created as {args.role} ({user_id})")  # noqa: T201
    finally:
        await databases.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas", description="AtlasAgri operations")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed-reference", help="load the global FAO reference tables")
    sub.add_parser("seed-demo", help="create and populate the demonstration tenant")
    sub.add_parser(
        "set-role-passwords",
        help="set the login roles' passwords from ATLAS_*_ROLE_PASSWORD",
    )
    user = sub.add_parser(
        "create-user", help="create an account (password from ATLAS_NEW_USER_PASSWORD)"
    )
    user.add_argument("--email", required=True)
    user.add_argument("--name", required=True)
    user.add_argument("--role", required=True, choices=[r.value for r in _roles()])
    user.add_argument("--tenant-slug", required=True, dest="tenant_slug")
    args = parser.parse_args(argv)

    configure_logging(json_output=False)
    settings = get_settings()

    if args.command == "seed-reference":
        asyncio.run(_seed_reference(settings))
    elif args.command == "seed-demo":
        asyncio.run(_seed_demo(settings))
    elif args.command == "set-role-passwords":
        asyncio.run(_set_role_passwords(settings))
    elif args.command == "create-user":
        asyncio.run(_create_user(settings, args))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
