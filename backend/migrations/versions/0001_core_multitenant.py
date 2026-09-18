"""Socle multi-tenant : schémas, rôles, tables, RLS forcée, vues analytiques.

Cette migration établit la disposition d'isolation décidée en phase 0
(`docs/decisions/0001-isolation-analytique.md`) et vérifiée sur PostgreSQL 16 :

* les politiques vivent sur les tables de base de `app` ;
* `analytics` ne contient que des **vues**, détenues par un rôle distinct du
  propriétaire des tables et **sans** `security_invoker` ;
* le rôle analytique ne détient aucun privilège sur `app`, si bien qu'un
  contournement du validateur AST échoue au privilège et non au validateur.

`FORCE` n'est pas une précaution : une vue détenue par le propriétaire de la
table, au-dessus d'une table dont la RLS est seulement `ENABLE`, renvoie
**toutes** les organisations. Mesuré, pas supposé.

Revision ID: 0001_core
Revises:
"""

import contextlib
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_core"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP = "app"
ANALYTICS = "analytics"
META = "meta"

#: Rôles, et ce que chacun a le droit de faire.
OWNER = "atlas_owner"            # propriétaire des tables ; exécute les migrations
APP_ROLE = "atlas_app"           # rôle applicatif : CRUD sur app, sous RLS
ANALYTICS_OWNER = "atlas_analytics_owner"  # propriétaire des vues analytiques
ANALYTICS_RO = "atlas_analytics_ro"        # SELECT sur les vues, rien d'autre

#: Tables soumises à l'isolation, avec la colonne qui porte l'organisation.
#: `tenants` s'isole sur `id` : une organisation ne lit pas la fiche d'une autre.
RLS_TABLES: tuple[tuple[str, str], ...] = (
    ("tenants", "id"),
    ("users", "tenant_id"),
    ("sites", "tenant_id"),
    ("audit_log", "tenant_id"),
)

#: Vues analytiques : la seule surface que l'agent text-to-SQL peut voir.
#: Nommées et commentées en français parce que le panneau de schéma les montre
#: à l'utilisateur ; les colonnes restent en anglais, comme le reste du SQL.
ANALYTICS_VIEWS: tuple[tuple[str, str, str], ...] = (
    (
        "v_sites",
        """
        SELECT s.id,
               s.code,
               s.name_fr,
               s.site_type,
               s.region_code,
               s.latitude,
               s.longitude,
               s.capacity_tonnes,
               s.has_cold_storage,
               s.data_state,
               s.data_origin,
               s.created_at
          FROM app.sites s
        """,
        "Sites de l'organisation : exploitations, entrepôts, plateformes, clients.",
    ),
    (
        "v_users",
        """
        SELECT u.id,
               u.full_name,
               u.role,
               u.is_active,
               u.created_at
          FROM app.users u
        """,
        "Utilisateurs de l'organisation. Le courriel et l'empreinte du mot de "
        "passe sont volontairement absents de la surface analytique.",
    ),
)


#: Rôles auxquels l'application se connecte réellement.
LOGIN_ROLES: frozenset[str] = frozenset({APP_ROLE, ANALYTICS_RO})


def _current_db(conn: sa.engine.Connection) -> str:
    """Nom de la base courante, cité — `REVOKE ... ON DATABASE` n'accepte pas
    de paramètre lié."""
    name = conn.execute(sa.text("SELECT current_database()")).scalar_one()
    return '"' + str(name).replace('"', '""') + '"'


def _role(name: str) -> str:
    """Crée le rôle s'il manque, et **réaffirme ses attributs s'il existe**.

    Les rôles sont des objets de grappe : ils survivent à la suppression de la
    base. Un `CREATE ROLE IF NOT EXISTS` seul laisserait donc les attributs d'un
    rôle homonyme créé par une installation précédente — y compris, dans le pire
    cas, un `BYPASSRLS` qui rendrait toutes les politiques de cette migration
    décoratives sans le moindre signal. Le `ALTER` est le point important.
    """
    login = "LOGIN" if name in LOGIN_ROLES else "NOLOGIN"
    attributes = f"{login} NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
    # Interpolation de constantes de ce module dans du DDL : PostgreSQL
    # n'accepte pas de paramètre lié dans une instruction DDL, et aucune de ces
    # valeurs ne provient d'une entrée (cf. per-file-ignores S608).
    return f"""
    DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{name}') THEN
            CREATE ROLE {name} {attributes};
        ELSE
            ALTER ROLE {name} {attributes};
        END IF;
    END $$;
    """


def upgrade() -> None:
    conn = op.get_bind()

    # -- rôles ----------------------------------------------------------
    # NOBYPASSRLS sur chacun, explicitement : un rôle BYPASSRLS rendrait toutes
    # les politiques de cette migration décoratives, sans aucun signal.
    for role in (OWNER, APP_ROLE, ANALYTICS_OWNER, ANALYTICS_RO):
        conn.execute(sa.text(_role(role)))
    # Le rôle qui exécute la migration doit pouvoir devenir le propriétaire pour
    # lui transférer les tables. En production c'est un rôle d'administration
    # dédié ; en développement, le superutilisateur.
    conn.execute(sa.text(f"GRANT {OWNER}, {ANALYTICS_OWNER} TO CURRENT_USER"))

    # -- schémas --------------------------------------------------------
    conn.execute(sa.text(f"CREATE SCHEMA IF NOT EXISTS {APP} AUTHORIZATION CURRENT_USER"))
    conn.execute(sa.text(f"CREATE SCHEMA IF NOT EXISTS {ANALYTICS} AUTHORIZATION CURRENT_USER"))
    conn.execute(sa.text(f"CREATE SCHEMA IF NOT EXISTS {META} AUTHORIZATION CURRENT_USER"))

    # Les schémas appartiennent aux rôles qui les portent, pas au rôle qui a
    # exécuté la migration.
    conn.execute(sa.text(f"ALTER SCHEMA {APP} OWNER TO {OWNER}"))
    conn.execute(sa.text(f"ALTER SCHEMA {ANALYTICS} OWNER TO {ANALYTICS_OWNER}"))

    # PUBLIC ne conserve rien : on regrante ensuite le minimum, explicitement.
    conn.execute(sa.text(f"REVOKE ALL ON SCHEMA {APP}, {ANALYTICS}, {META} FROM PUBLIC"))
    # `CREATE TEMP TABLE` reste ouvert à PUBLIC par défaut sur toute base : sans
    # ce retrait, la sonde de démarrage qui vérifie que le rôle analytique ne
    # peut rien écrire réussirait à écrire, et le rôle disposerait d'un espace
    # de travail persistant pour la durée de sa session.
    conn.execute(sa.text("REVOKE TEMPORARY ON DATABASE " + _current_db(conn) + " FROM PUBLIC"))
    conn.execute(sa.text(f"GRANT USAGE ON SCHEMA {APP} TO {OWNER}"))

    # -- extensions -----------------------------------------------------
    # pgvector porte l'index de schéma de l'agent d'analyse (phase 6).
    conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
    # PostGIS est requis en production. Son absence en développement désactive
    # les fonctions spatiales avec un avertissement, elle ne casse pas le
    # démarrage : la frontière canonique reste le GeoJSON portable.
    with contextlib.suppress(sa.exc.DBAPIError):  # pragma: no cover - selon l'installation
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS postgis"))

    # -- tables ---------------------------------------------------------
    op.create_table(
        "tenants",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False, unique=True),
        sa.Column("region_code", sa.String(40)),
        sa.Column("plan_code", sa.String(40), nullable=False, server_default="COOPERATIVE"),
        sa.Column("is_demo", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        schema=APP,
    )

    op.create_table(
        "user_directory",
        sa.Column("email", sa.String(255), primary_key=True),
        sa.Column("user_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{APP}.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        schema=APP,
    )

    op.create_table(
        "users",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{APP}.tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("email", name="uq_users_email"),
        schema=APP,
    )

    op.create_table(
        "sites",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{APP}.tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name_fr", sa.String(200), nullable=False),
        sa.Column("site_type", sa.String(20), nullable=False),
        sa.Column("region_code", sa.String(40)),
        sa.Column("latitude", sa.Float, nullable=False),
        sa.Column("longitude", sa.Float, nullable=False),
        sa.Column("elevation_m", sa.Float),
        sa.Column("capacity_tonnes", sa.Float),
        sa.Column("has_cold_storage", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("boundary_geojson", sa.JSON),
        # Provenance : non nulle, sans valeur par défaut au niveau du schéma.
        # Un défaut ferait qu'une insertion oublieuse produirait une ligne
        # « observée » plausible — exactement la confusion que ces colonnes
        # existent pour empêcher. Une insertion qui n'y pense pas doit échouer.
        sa.Column("data_state", sa.String(16), nullable=False),
        sa.Column("data_origin", sa.String(20), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "code", name="uq_sites_tenant_code"),
        schema=APP,
    )
    op.create_index("ix_sites_tenant_type", "sites", ["tenant_id", "site_type"], schema=APP)

    op.create_table(
        "audit_log",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{APP}.tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("run_id", sa.String(64), index=True),
        sa.Column("actor_user_id", sa.dialects.postgresql.UUID(as_uuid=True)),
        sa.Column("actor_email", sa.String(255)),
        sa.Column("action", sa.String(120), nullable=False),
        sa.Column("resource_type", sa.String(60), nullable=False),
        sa.Column("resource_id", sa.String(120)),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("detail", sa.JSON),
        sa.Column("note", sa.Text),
        schema=APP,
    )
    op.create_index("ix_audit_tenant_time", "audit_log", ["tenant_id", "occurred_at"], schema=APP)

    # -- propriété des tables --------------------------------------------
    # Les tables appartiennent à `atlas_owner`, qui n'est **ni** le rôle
    # applicatif **ni** un superutilisateur. C'est ce qui rend `FORCE` autre
    # chose qu'une décoration : un superutilisateur contourne la RLS quoi qu'il
    # arrive, donc des tables lui appartenant n'exerceraient jamais la
    # politique du propriétaire.
    for table in ("tenants", "user_directory", "users", "sites", "audit_log"):
        conn.execute(sa.text(f"ALTER TABLE {APP}.{table} OWNER TO {OWNER}"))

    # -- row level security ---------------------------------------------
    for table, column in RLS_TABLES:
        conn.execute(sa.text(f"ALTER TABLE {APP}.{table} ENABLE ROW LEVEL SECURITY"))
        # Sans FORCE, le propriétaire échappe à la politique — et une vue qu'il
        # détient la contourne pour tout le monde. Mesuré sur PostgreSQL 16.
        conn.execute(sa.text(f"ALTER TABLE {APP}.{table} FORCE ROW LEVEL SECURITY"))
        # `current_setting` sans `missing_ok` : un GUC absent lève une erreur.
        # Avec `missing_ok`, il rendrait NULL, la politique serait fausse pour
        # toutes les lignes, et l'appelant recevrait un jeu **vide** — que
        # l'agent d'analyse raconterait comme « vous n'avez rien ce mois-ci ».
        # Une panne muette est pire ici qu'une panne bruyante.
        conn.execute(
            sa.text(
                f"""
                CREATE POLICY tenant_isolation ON {APP}.{table}
                    USING ({column} = current_setting('app.current_tenant')::uuid)
                    WITH CHECK ({column} = current_setting('app.current_tenant')::uuid)
                """
            )
        )

    # -- privilèges : rôle applicatif ------------------------------------
    conn.execute(sa.text(f"GRANT USAGE ON SCHEMA {APP} TO {APP_ROLE}"))
    conn.execute(
        sa.text(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {APP} TO {APP_ROLE}"
        )
    )
    conn.execute(
        sa.text(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {APP} "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
        )
    )

    # -- privilèges : chaîne analytique ----------------------------------
    # `analytics_owner` lit les tables de base ; il n'est pas leur propriétaire,
    # donc la politique s'applique à lui. Le GUC est lu dans la **session**, qui
    # reste celle du rôle analytique : le filtrage est donc bien celui du
    # demandeur, malgré le changement de rôle effectif à la traversée de la vue.
    conn.execute(sa.text(f"GRANT USAGE ON SCHEMA {APP} TO {ANALYTICS_OWNER}"))
    conn.execute(sa.text(f"GRANT SELECT ON {APP}.sites, {APP}.users TO {ANALYTICS_OWNER}"))
    conn.execute(sa.text(f"GRANT USAGE, CREATE ON SCHEMA {ANALYTICS} TO {ANALYTICS_OWNER}"))

    for name, body, comment in ANALYTICS_VIEWS:
        # Vues créées **par** analytics_owner : c'est son rôle qui traversera
        # vers les tables de base. `security_invoker` est délibérément absent —
        # il exigerait que le rôle analytique détienne des privilèges sur `app`,
        # donc qu'il puisse interroger les tables directement, ce qui ferait
        # retomber tout le confinement sur le validateur AST.
        conn.execute(sa.text(f"SET LOCAL ROLE {ANALYTICS_OWNER}"))
        conn.execute(sa.text(f"CREATE VIEW {ANALYTICS}.{name} AS {body}"))
        conn.execute(sa.text("RESET ROLE"))
        # PostgreSQL n'accepte pas de paramètre lié dans une instruction DDL,
        # donc le commentaire est littéralisé. Ce sont des constantes de ce
        # module, jamais une entrée : le doublement des apostrophes est là pour
        # que l'apostrophe française — « l'organisation » — ne casse pas le SQL.
        literal = "'" + comment.replace("'", "''") + "'"
        conn.execute(sa.text(f"COMMENT ON VIEW {ANALYTICS}.{name} IS {literal}"))

    conn.execute(sa.text(f"GRANT USAGE ON SCHEMA {ANALYTICS} TO {ANALYTICS_RO}"))
    for name, _body, _comment in ANALYTICS_VIEWS:
        conn.execute(sa.text(f"GRANT SELECT ON {ANALYTICS}.{name} TO {ANALYTICS_RO}"))

    # Explicitement retiré, pour qu'un futur GRANT large ne le rétablisse pas
    # discrètement : le rôle analytique n'a aucun chemin vers `app`.
    conn.execute(sa.text(f"REVOKE ALL ON SCHEMA {APP} FROM {ANALYTICS_RO}"))
    conn.execute(sa.text(f"REVOKE ALL ON ALL TABLES IN SCHEMA {APP} FROM {ANALYTICS_RO}"))
    conn.execute(sa.text(f"REVOKE CREATE ON SCHEMA {ANALYTICS} FROM {ANALYTICS_RO}"))
    conn.execute(
        sa.text(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {ANALYTICS} "
            f"GRANT SELECT ON TABLES TO {ANALYTICS_RO}"
        )
    )

    # Défauts de session du rôle analytique. Le `SET LOCAL` par transaction
    # existe aussi ; ceci rend l'état sûr même pour une connexion qui le
    # sauterait.
    conn.execute(sa.text(f"ALTER ROLE {ANALYTICS_RO} SET default_transaction_read_only = on"))
    conn.execute(sa.text(f"ALTER ROLE {ANALYTICS_RO} SET statement_timeout = '30s'"))
    conn.execute(
        sa.text(f"ALTER ROLE {ANALYTICS_RO} SET idle_in_transaction_session_timeout = '60s'")
    )
    conn.execute(sa.text(f"ALTER ROLE {ANALYTICS_RO} SET lock_timeout = '5s'"))

    # -- schéma meta -----------------------------------------------------
    conn.execute(sa.text(f"GRANT USAGE, CREATE ON SCHEMA {META} TO {APP_ROLE}"))


def downgrade() -> None:
    conn = op.get_bind()
    for name, _body, _comment in ANALYTICS_VIEWS:
        conn.execute(sa.text(f"DROP VIEW IF EXISTS {ANALYTICS}.{name}"))
    for table in ("audit_log", "sites", "users", "user_directory", "tenants"):
        op.drop_table(table, schema=APP)
    conn.execute(sa.text(f"DROP SCHEMA IF EXISTS {ANALYTICS} CASCADE"))
    conn.execute(sa.text(f"DROP SCHEMA IF EXISTS {META} CASCADE"))
    conn.execute(sa.text(f"DROP SCHEMA IF EXISTS {APP} CASCADE"))
