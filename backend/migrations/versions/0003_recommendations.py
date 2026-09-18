"""Recommandations : là où une décision cesse d'être éphémère.

Jusqu'ici, une décision vivait le temps d'une réponse HTTP. Le moteur la
produisait, l'écran l'affichait, et elle disparaissait. Trois conséquences que
cette table referme :

* **« Combien de recommandations ont été acceptées ce mois-ci ? »** — la question
  de clôture de la §12 — n'avait rien à compter ;
* `create_recommendation`, le seul outil d'écriture du registre, rendait
  `recorded=true` sans rien enregistrer. Un outil qui ment sur son effet est pire
  qu'un outil absent ;
* aucune trace ne permettait de dire, des mois plus tard, *ce que le moteur avait
  conseillé* face à *ce que l'exploitant avait fait*. C'est la seule base possible
  d'une quantification ultérieure des pertes évitées, et elle ne se reconstitue
  pas après coup : la météo et les paramètres de culture auront changé.

**La décision est figée, pas référencée.** `payload` porte le `Decision.to_dict()`
complet — entrées avec leur provenance, étapes de calcul, hypothèses, options
écartées. Recalculer la recommandation six mois plus tard donnerait un autre
chiffre, sur d'autres données, et l'audit porterait sur une décision qui n'a
jamais été prise.

**Le verdict est distinct de la recommandation.** `verdict` part à `PENDING` :
aucun outil n'exécute d'action irréversible, et enregistrer une proposition n'est
pas l'appliquer. La colonne ne peut passer à un état décidé sans porter *qui* et
*quand* — une contrainte de table, pas une convention.

Revision ID: 0003_recommendations
Revises: 0002_data_model
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_recommendations"
down_revision: str | None = "0002_data_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP = "app"
ANALYTICS = "analytics"
OWNER = "atlas_owner"
APP_ROLE = "atlas_app"
ANALYTICS_OWNER = "atlas_analytics_owner"
ANALYTICS_RO = "atlas_analytics_ro"

UUID = sa.dialects.postgresql.UUID
JSONB = sa.dialects.postgresql.JSONB

#: La vue analytique : ce qu'un agent d'analyse peut compter.
#:
#: `payload` n'y figure pas. Il contient la décision entière, y compris des
#: libellés saisis par un exploitant, et l'exposer sur la surface analytique
#: mettrait du texte libre dans le chemin du résumeur sans raison métier — on
#: compte des verdicts, on ne fouille pas des blobs.
VIEW_BODY = """
    SELECT r.id,
           r.domain,
           r.subject_id,
           r.headline_fr,
           r.outcome_code,
           r.verdict,
           r.created_at,
           r.decided_at,
           -- Délai de décision : la grandeur qui dit si une recommandation est
           -- arrivée à temps pour servir. Calculée ici plutôt qu'à la question,
           -- parce qu'une soustraction de dates est exactement le genre de
           -- chose qu'un modèle écrit de travers.
           CASE WHEN r.decided_at IS NULL THEN NULL
                ELSE round(
                    EXTRACT(EPOCH FROM (r.decided_at - r.created_at)) / 3600.0, 2
                )
           END AS hours_to_decision,
           u.full_name AS decided_by_name,
           r.data_state,
           r.data_origin
    FROM app.recommendations r
    LEFT JOIN app.users u ON u.id = r.decided_by
"""

VIEW_COMMENT = (
    "Recommandations produites par les moteurs et ce que l'humain en a fait. "
    "verdict : PENDING (en attente), ACCEPTED (acceptée), REJECTED (refusée), "
    "MODIFIED (appliquée avec modification)."
)


def upgrade() -> None:
    conn = op.get_bind()

    op.create_table(
        "recommendations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{APP}.tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # Le domaine qui a produit la décision. Chaîne plutôt qu'énumération
        # PostgreSQL : un quatrième domaine ne doit pas demander de migration.
        sa.Column("domain", sa.String(20), nullable=False),
        sa.Column("subject_id", sa.String(120), nullable=False),
        sa.Column("subject_label_fr", sa.String(300), nullable=False),
        sa.Column("headline_fr", sa.Text, nullable=False),
        sa.Column("outcome_code", sa.String(40), nullable=False),
        sa.Column("rationale_fr", sa.Text),
        # La décision figée. Voir l'en-tête du module.
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("verdict", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column(
            "decided_by",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{APP}.users.id", ondelete="SET NULL"),
        ),
        sa.Column("decision_note_fr", sa.Text),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{APP}.users.id", ondelete="SET NULL"),
        ),
        # Provenance : une recommandation est une valeur affichée comme les
        # autres. Une proposition issue d'un jeu de démonstration ne doit pas se
        # confondre avec une proposition issue de données mesurées.
        sa.Column("data_state", sa.String(16), nullable=False),
        sa.Column("data_origin", sa.String(20), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint(
            "verdict IN ('PENDING', 'ACCEPTED', 'REJECTED', 'MODIFIED')",
            name="ck_recommendations_verdict",
        ),
        # Un verdict rendu porte qui et quand. En attente, ni l'un ni l'autre.
        #
        # Sans cette contrainte, une recommandation « acceptée » sans décideur
        # ni date serait possible — et c'est exactement la ligne qu'un audit
        # cherche : celle où personne n'a approuvé mais où l'action a eu lieu.
        sa.CheckConstraint(
            "(verdict = 'PENDING' AND decided_at IS NULL AND decided_by IS NULL) "
            "OR (verdict <> 'PENDING' AND decided_at IS NOT NULL)",
            name="ck_recommendations_verdict_is_attributed",
        ),
        schema=APP,
    )

    op.create_index(
        "ix_recommendations_tenant_verdict",
        "recommendations",
        ["tenant_id", "verdict"],
        schema=APP,
    )
    op.create_index(
        "ix_recommendations_tenant_subject",
        "recommendations",
        ["tenant_id", "domain", "subject_id"],
        schema=APP,
    )

    conn.execute(sa.text(f"ALTER TABLE {APP}.recommendations OWNER TO {OWNER}"))
    conn.execute(sa.text(f"ALTER TABLE {APP}.recommendations ENABLE ROW LEVEL SECURITY"))
    conn.execute(sa.text(f"ALTER TABLE {APP}.recommendations FORCE ROW LEVEL SECURITY"))
    conn.execute(
        sa.text(
            f"""
            CREATE POLICY tenant_isolation ON {APP}.recommendations
                USING (tenant_id = current_setting('app.current_tenant')::uuid)
                WITH CHECK (tenant_id = current_setting('app.current_tenant')::uuid)
            """
        )
    )
    conn.execute(
        sa.text(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {APP}.recommendations "
            f"TO {APP_ROLE}"
        )
    )

    # -- surface analytique ------------------------------------------------
    conn.execute(sa.text(f"GRANT SELECT ON {APP}.recommendations TO {ANALYTICS_OWNER}"))
    conn.execute(sa.text(f"GRANT SELECT ON {APP}.users TO {ANALYTICS_OWNER}"))
    conn.execute(sa.text(f"SET LOCAL ROLE {ANALYTICS_OWNER}"))
    conn.execute(sa.text(f"CREATE VIEW {ANALYTICS}.v_recommendations AS {VIEW_BODY}"))
    conn.execute(sa.text("RESET ROLE"))
    literal = "'" + VIEW_COMMENT.replace("'", "''") + "'"
    conn.execute(
        sa.text(f"COMMENT ON VIEW {ANALYTICS}.v_recommendations IS {literal}")
    )
    conn.execute(
        sa.text(f"GRANT SELECT ON {ANALYTICS}.v_recommendations TO {ANALYTICS_RO}")
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(f"DROP VIEW IF EXISTS {ANALYTICS}.v_recommendations"))
    op.drop_table("recommendations", schema=APP)
