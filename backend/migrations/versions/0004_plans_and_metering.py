"""Plans, quotas et comptage d'usage.

**Deux natures de compteur, et les confondre casse les quotas.**

Une parcelle est un **stock** : la question est « combien en existe-t-il
maintenant ». Un message au copilote est un **flux** : la question est « combien
en ont été consommés ce mois-ci ». Un quota de stock se vérifie en comptant des
lignes ; un quota de flux se vérifie en comptant des événements sur une période.

Les traiter pareil produit deux défauts opposés et tous deux silencieux : une
parcelle supprimée puis recréée consommerait deux unités de quota si le stock
était compté en événements, et un message consommerait éternellement une unité
si le flux était compté en lignes vivantes.

**Des événements, pas des compteurs.** Un compteur entretenu à côté de ses
événements dérive — c'est le défaut classique de la facturation, et il se
découvre le jour où un client conteste une facture. Le quota se calcule donc par
agrégation, et l'agrégation est la donnée qu'une intégration de facturation
consommerait plus tard.

**Les jetons sont réels, le coût est dérivé.** La §6 demande que la dépense soit
« de la vraie donnée, pas une estimation ». Les jetons le sont : ils viennent de
la réponse de l'API. Le coût, lui, est le produit des jetons par une grille
tarifaire recopiée — aucun total n'a jamais été rapproché d'une facture
(registre d'honnêteté, ligne 22). Les deux colonnes existent donc séparément,
pour que personne ne présente la seconde comme la première.

Revision ID: 0004_plans_and_metering
Revises: 0003_recommendations
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_plans_and_metering"
down_revision: str | None = "0003_recommendations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP = "app"
ANALYTICS = "analytics"
OWNER = "atlas_owner"
APP_ROLE = "atlas_app"
ANALYTICS_OWNER = "atlas_analytics_owner"
ANALYTICS_RO = "atlas_analytics_ro"

UUID = sa.dialects.postgresql.UUID

#: Les trois plans de la §6. `NULL` signifie **illimité**, pas zéro : un plan
#: Entreprise sans plafond de parcelles porte `NULL`, et un zéro s'y lirait
#: comme une interdiction totale.
PLANS: tuple[dict[str, object], ...] = (
    {
        "code": "COOPERATIVE",
        "name_fr": "Coopérative",
        "description_fr": (
            "Pour une coopérative ou une petite exploitation qui suit quelques "
            "parcelles et n'expédie pas encore."
        ),
        "max_fields": 25,
        "max_shipments": 10,
        "max_agent_messages_per_month": 100,
        "max_analytics_queries_per_month": 50,
        "max_llm_spend_usd_per_month": 5.0,
        "sort_order": 1,
    },
    {
        "code": "EXPLOITATION",
        "name_fr": "Exploitation",
        "description_fr": (
            "Pour un domaine qui pilote son irrigation et ses expéditions au "
            "quotidien."
        ),
        "max_fields": 200,
        "max_shipments": 500,
        "max_agent_messages_per_month": 1000,
        "max_analytics_queries_per_month": 500,
        "max_llm_spend_usd_per_month": 50.0,
        "sort_order": 2,
    },
    {
        "code": "ENTREPRISE",
        "name_fr": "Entreprise",
        "description_fr": (
            "Pour un groupe multi-sites. Aucun plafond de volume ; la dépense "
            "du modèle reste plafonnée, parce qu'un plafond absent n'est pas un "
            "budget."
        ),
        "max_fields": None,
        "max_shipments": None,
        "max_agent_messages_per_month": None,
        "max_analytics_queries_per_month": None,
        "max_llm_spend_usd_per_month": 500.0,
        "sort_order": 3,
    },
)

VIEW_BODY = """
    SELECT u.tenant_id,
           u.metric,
           date_trunc('month', u.occurred_at)::date AS period_month,
           count(*)                                  AS events,
           sum(u.quantity)                           AS quantity,
           sum(u.input_tokens)                       AS input_tokens,
           sum(u.output_tokens)                      AS output_tokens,
           -- Somme des coûts **dérivés**. NULL dès qu'un seul événement de la
           -- période n'est pas tarifé : un total partiel présenté comme complet
           -- est pire qu'un total absent.
           CASE WHEN bool_or(u.cost_usd IS NULL) THEN NULL
                ELSE round(sum(u.cost_usd)::numeric, 6)
           END                                       AS estimated_cost_usd
    FROM app.usage_events u
    GROUP BY u.tenant_id, u.metric, date_trunc('month', u.occurred_at)
"""

VIEW_COMMENT = (
    "Usage mensuel par organisation et par métrique. "
    "metric : AGENT_MESSAGE (question au copilote), ANALYTICS_QUERY (question "
    "d'analyse). estimated_cost_usd est DÉRIVÉ d'une grille tarifaire, pas relevé "
    "sur une facture ; il vaut NULL si un appel de la période n'est pas tarifé."
)


def upgrade() -> None:
    conn = op.get_bind()

    # -- plans : référentiel de plateforme, sans organisation ---------------
    #
    # Comme `regions` : aucune colonne `tenant_id`, donc aucune politique RLS.
    # Lisible par tous — une organisation doit pouvoir lire les limites de son
    # propre plan — et modifiable par le seul propriétaire. Un plan est
    # provisionné par un administrateur de la plateforme (§6 : pas de paiement
    # dans ce périmètre).
    op.create_table(
        "plans",
        sa.Column("code", sa.String(40), primary_key=True),
        sa.Column("name_fr", sa.String(80), nullable=False),
        sa.Column("description_fr", sa.Text, nullable=False),
        # `NULL` = illimité. Voir le commentaire de PLANS.
        sa.Column("max_fields", sa.Integer),
        sa.Column("max_shipments", sa.Integer),
        sa.Column("max_agent_messages_per_month", sa.Integer),
        sa.Column("max_analytics_queries_per_month", sa.Integer),
        sa.Column("max_llm_spend_usd_per_month", sa.Float),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.CheckConstraint(
            "max_fields IS NULL OR max_fields > 0", name="ck_plans_fields_positive"
        ),
        sa.CheckConstraint(
            "max_shipments IS NULL OR max_shipments > 0",
            name="ck_plans_shipments_positive",
        ),
        schema=APP,
    )

    # -- événements d'usage -------------------------------------------------
    op.create_table(
        "usage_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{APP}.tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("metric", sa.String(30), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("quantity", sa.Integer, nullable=False, server_default="1"),
        # Jetons : **réels**, tels que l'API les a rendus.
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        # Coût : **dérivé** d'une grille tarifaire. NULL quand le modèle n'est
        # pas tarifé — jamais zéro, qui se lirait « gratuit ».
        sa.Column("cost_usd", sa.Float),
        sa.Column("model", sa.String(80)),
        sa.Column("run_id", sa.String(64), index=True),
        sa.Column(
            "actor_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{APP}.users.id", ondelete="SET NULL"),
        ),
        sa.CheckConstraint("quantity > 0", name="ck_usage_quantity_positive"),
        schema=APP,
    )
    op.create_index(
        "ix_usage_tenant_metric_time",
        "usage_events",
        ["tenant_id", "metric", "occurred_at"],
        schema=APP,
    )

    # -- propriété, politiques, privilèges ----------------------------------
    for table in ("plans", "usage_events"):
        conn.execute(sa.text(f"ALTER TABLE {APP}.{table} OWNER TO {OWNER}"))

    conn.execute(sa.text(f"ALTER TABLE {APP}.usage_events ENABLE ROW LEVEL SECURITY"))
    conn.execute(sa.text(f"ALTER TABLE {APP}.usage_events FORCE ROW LEVEL SECURITY"))
    conn.execute(
        sa.text(
            f"""
            CREATE POLICY tenant_isolation ON {APP}.usage_events
                USING (tenant_id = current_setting('app.current_tenant')::uuid)
                WITH CHECK (tenant_id = current_setting('app.current_tenant')::uuid)
            """
        )
    )

    # Les plans sont publics en lecture : une organisation lit les limites de
    # son plan, et celles des autres plans pour savoir ce qu'un changement lui
    # apporterait. Ils ne contiennent aucune donnée d'organisation.
    conn.execute(sa.text(f"GRANT SELECT ON {APP}.plans TO {APP_ROLE}"))
    conn.execute(
        sa.text(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {APP}.usage_events TO {APP_ROLE}"
        )
    )

    # -- amorçage des plans -------------------------------------------------
    #
    # Sous `SET ROLE` propriétaire : `plans` n'a pas de politique, mais la table
    # appartient à `atlas_owner` et l'amorçage est une opération d'exploitation,
    # au même titre qu'une migration.
    columns = (
        "code, name_fr, description_fr, max_fields, max_shipments, "
        "max_agent_messages_per_month, max_analytics_queries_per_month, "
        "max_llm_spend_usd_per_month, sort_order"
    )
    for plan in PLANS:
        conn.execute(
            sa.text(
                f"INSERT INTO {APP}.plans ({columns}) VALUES "
                "(:code, :name_fr, :description_fr, :max_fields, :max_shipments, "
                ":max_agent_messages_per_month, :max_analytics_queries_per_month, "
                ":max_llm_spend_usd_per_month, :sort_order)"
            ),
            plan,
        )

    # -- le plan de l'organisation ------------------------------------------
    #
    # Posée **après** l'amorçage : `tenants.plan_code` a la valeur par défaut
    # « COOPERATIVE » depuis la migration 0001, et des organisations existent
    # déjà. Déclarer la contrainte avant d'insérer les plans la ferait échouer
    # sur des lignes parfaitement valides.
    op.create_foreign_key(
        "fk_tenants_plan_code",
        "tenants",
        "plans",
        ["plan_code"],
        ["code"],
        source_schema=APP,
        referent_schema=APP,
        ondelete="RESTRICT",
    )

    # -- surface analytique --------------------------------------------------
    conn.execute(sa.text(f"GRANT SELECT ON {APP}.usage_events TO {ANALYTICS_OWNER}"))
    conn.execute(sa.text(f"SET LOCAL ROLE {ANALYTICS_OWNER}"))
    conn.execute(sa.text(f"CREATE VIEW {ANALYTICS}.v_usage_monthly AS {VIEW_BODY}"))
    conn.execute(sa.text("RESET ROLE"))
    literal = "'" + VIEW_COMMENT.replace("'", "''") + "'"
    conn.execute(sa.text(f"COMMENT ON VIEW {ANALYTICS}.v_usage_monthly IS {literal}"))
    conn.execute(
        sa.text(f"GRANT SELECT ON {ANALYTICS}.v_usage_monthly TO {ANALYTICS_RO}")
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(f"DROP VIEW IF EXISTS {ANALYTICS}.v_usage_monthly"))
    op.drop_constraint("fk_tenants_plan_code", "tenants", schema=APP, type_="foreignkey")
    op.drop_table("usage_events", schema=APP)
    op.drop_table("plans", schema=APP)
