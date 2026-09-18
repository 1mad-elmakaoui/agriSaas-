"""Modèle de données fusionné : référentiel agronomique et tables métier.

Deux familles de tables, deux politiques d'isolation, et c'est le point de
cette migration.

**Tables métier** — politique stricte : `tenant_id = organisation courante`, en
lecture comme en écriture.

**Tables de référentiel** — `crops`, `crop_growth_stages`, `soil_profiles`,
`irrigation_systems`. Une ligne globale porte `tenant_id IS NULL` ; une
organisation peut poser sa propre ligne de même `code`, qui masque la globale
sans la modifier :

    USING      (tenant_id IS NULL OR tenant_id = organisation courante)
    WITH CHECK (tenant_id = organisation courante)

Lecture : le référentiel FAO plus ses propres mesures. Écriture : les siennes
seulement. Une organisation ne peut pas réécrire un Kc pour tout le monde, et la
calibration locale reste une tâche de données plutôt qu'une modification de code.

Donner à ces tables la politique stricte rendrait le référentiel invisible ;
donner aux tables métier la politique du référentiel ouvrirait une fuite. Le
test de partition dans `test_provenance.py` échoue si une table se retrouve dans
la mauvaise famille — ou dans aucune.

Revision ID: 0002_data_model
Revises: 0001_core
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_data_model"
down_revision: str | None = "0001_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP = "app"
ANALYTICS = "analytics"
OWNER = "atlas_owner"
APP_ROLE = "atlas_app"
ANALYTICS_OWNER = "atlas_analytics_owner"
ANALYTICS_RO = "atlas_analytics_ro"

UUID = sa.dialects.postgresql.UUID

#: Tables métier ajoutées ici : politique stricte.
BUSINESS_TABLES: tuple[str, ...] = (
    "fields",
    "soil_moisture_readings",
    "weather_observations",
    "products",
    "suppliers",
    "shipments",
)

#: Tables de référentiel : politique laissant passer la ligne globale.
REFERENCE_TABLES: tuple[str, ...] = (
    "crops",
    "crop_growth_stages",
    "soil_profiles",
    "irrigation_systems",
)

#: Vues analytiques ajoutées. Les colonnes restent anglaises ; le commentaire
#: est français parce que le panneau de schéma le montre à l'utilisateur.
ANALYTICS_VIEWS: tuple[tuple[str, str, str], ...] = (
    (
        "v_fields",
        """
        SELECT f.id, f.code, f.name_fr, f.area_ha, f.latitude, f.longitude,
               f.planting_date, f.declared_growth_stage,
               f.flow_rate_m3_per_hour, f.water_cost_per_m3, f.seasonal_quota_m3,
               s.code       AS site_code,
               s.name_fr    AS site_name_fr,
               c.code       AS crop_code,
               c.name_fr    AS crop_name_fr,
               sp.code      AS soil_code,
               isy.code     AS irrigation_system_code,
               isy.efficiency AS irrigation_efficiency,
               f.created_at
          FROM app.fields f
          JOIN app.sites  s   ON s.id = f.site_id
          LEFT JOIN app.crops c              ON c.id  = f.crop_id
          LEFT JOIN app.soil_profiles sp     ON sp.id = f.soil_profile_id
          LEFT JOIN app.irrigation_systems isy ON isy.id = f.irrigation_system_id
        """,
        "Parcelles de l'organisation, avec culture, sol et système d'irrigation.",
    ),
    (
        "v_soil_moisture_readings",
        """
        SELECT r.id, r.field_id, f.code AS field_code, r.value_pct, r.depth_cm,
               r.recorded_at, r.data_state, r.data_origin, r.source_id
          FROM app.soil_moisture_readings r
          JOIN app.fields f ON f.id = r.field_id
        """,
        "Mesures d'humidité du sol. `data_state` et `data_origin` disent si la "
        "valeur a été mesurée, saisie, modélisée ou simulée : une moyenne qui "
        "les mélange n'a pas de sens.",
    ),
    (
        "v_shipments",
        """
        SELECT sh.id, sh.reference, sh.volume_tonnes, sh.transport_mode, sh.status,
               sh.departure_at, sh.sla_deadline_at,
               p.code    AS product_code,
               p.name_fr AS product_name_fr,
               o.code    AS origin_site_code,
               o.name_fr AS origin_site_name_fr,
               d.code    AS destination_site_code,
               d.name_fr AS destination_site_name_fr,
               fl.code   AS source_field_code,
               sh.created_at
          FROM app.shipments sh
          JOIN app.sites o ON o.id = sh.origin_site_id
          JOIN app.sites d ON d.id = sh.destination_site_id
          LEFT JOIN app.products p ON p.id = sh.product_id
          LEFT JOIN app.fields  fl ON fl.id = sh.source_field_id
        """,
        "Expéditions. Le statut est stocké en anglais : une question française "
        "sur « annulé » filtre sur 'CANCELLED'.",
    ),
    (
        "v_weather_daily",
        # Vue dérivée : l'horaire est le grain de stockage, le journalier en
        # découle. RHmax et RHmin sont les extrema *horaires* de la journée, ce
        # que la FAO-56 demande et qu'un stockage journalier ne peut pas
        # reconstituer.
        """
        SELECT w.site_id,
               (w.observed_at AT TIME ZONE 'UTC')::date        AS observation_date,
               w.provider,
               max(w.temperature_c)                            AS temp_max_c,
               min(w.temperature_c)                            AS temp_min_c,
               avg(w.temperature_c)                            AS temp_mean_c,
               max(w.relative_humidity_pct)                    AS rh_max_pct,
               min(w.relative_humidity_pct)                    AS rh_min_pct,
               avg(w.relative_humidity_pct)                    AS rh_mean_pct,
               avg(w.wind_speed_m_s)                           AS wind_speed_mean_m_s,
               max(w.wind_gust_kmh)                            AS wind_gust_max_kmh,
               sum(w.precipitation_mm)                         AS precipitation_mm,
               sum(w.solar_radiation_mj_m2)                    AS solar_radiation_mj_m2_day,
               sum(w.provider_et0_mm)                          AS provider_et0_mm_day,
               count(*)                                        AS hours_observed
          FROM app.weather_observations w
         GROUP BY w.site_id, (w.observed_at AT TIME ZONE 'UTC')::date, w.provider
        """,
        "Agrégat journalier dérivé des observations horaires. `hours_observed` "
        "dit combien d'heures composent la journée : un agrégat sur 5 heures "
        "n'est pas une journée et ne doit pas être lu comme telle.",
    ),
)


def _reference_policy(table: str) -> str:
    """Politique de lecture/écriture d'une table de référentiel.

    Lecture : la ligne globale **plus** les siennes. Écriture : les siennes
    seulement — une organisation ne réécrit pas un Kc pour tout le monde.
    """
    return f"""
    CREATE POLICY tenant_isolation ON {APP}.{table}
        USING (tenant_id IS NULL
               OR tenant_id = current_setting('app.current_tenant')::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant')::uuid)
    """


def _reference_admin_policy(table: str) -> str:
    """Écriture des lignes **globales**, réservée au rôle propriétaire.

    Sans cela, plus personne ne peut alimenter le référentiel : le `WITH CHECK`
    ci-dessus exige une organisation, et une ligne globale n'en a pas. Trois
    issues existaient. Un rôle `BYPASSRLS` rendrait décoratives toutes les
    politiques du schéma. Un `SET row_security = off` échoue précisément parce
    que `FORCE` s'applique aussi au propriétaire. Reste celle-ci : **déclarer**
    l'exception, pour le seul rôle d'administration, dans le catalogue plutôt
    que dans un privilège invisible.

    Les politiques permissives se combinent par OU : `atlas_owner` reçoit donc
    l'accès complet, et `atlas_app` conserve la politique restrictive. Le rôle
    n'a pas de droit de connexion — on ne l'atteint que par `SET ROLE`, depuis
    une session d'administration.
    """
    return f"""
    CREATE POLICY reference_admin ON {APP}.{table}
        TO {OWNER}
        USING (true)
        WITH CHECK (true)
    """


def _business_policy(table: str) -> str:
    return f"""
    CREATE POLICY tenant_isolation ON {APP}.{table}
        USING (tenant_id = current_setting('app.current_tenant')::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant')::uuid)
    """


def _tenant_column(nullable: bool = False) -> sa.Column:
    return sa.Column(
        "tenant_id",
        UUID(as_uuid=True),
        sa.ForeignKey(f"{APP}.tenants.id", ondelete="CASCADE"),
        nullable=nullable,
        index=True,
    )


def _reference_columns() -> list[sa.Column]:
    return [
        _tenant_column(nullable=True),
        sa.Column("is_measured", sa.Boolean, nullable=False, server_default=sa.false()),
    ]


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    ]


def _provenance_columns() -> list[sa.Column]:
    """Provenance : non nulle, **sans valeur par défaut**.

    Un défaut ferait qu'une insertion oublieuse produirait une ligne « observée »
    plausible — la confusion exacte que ces colonnes existent pour empêcher.
    """
    return [
        sa.Column("data_state", sa.String(16), nullable=False),
        sa.Column("data_origin", sa.String(20), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
    ]


def upgrade() -> None:
    conn = op.get_bind()

    # -- référentiel : régions -------------------------------------------
    op.create_table(
        "regions",
        sa.Column("code", sa.String(40), primary_key=True),
        sa.Column("name_fr", sa.String(120), nullable=False),
        sa.Column("name_ar", sa.String(120)),
        schema=APP,
    )

    # -- référentiel : cultures ------------------------------------------
    op.create_table(
        "crops",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        *_reference_columns(),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name_fr", sa.String(120), nullable=False),
        sa.Column("name_en", sa.String(120), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("is_perennial", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("kc_initial", sa.Float, nullable=False),
        sa.Column("kc_mid", sa.Float, nullable=False),
        sa.Column("kc_end", sa.Float, nullable=False),
        # Citation obligatoire : une valeur agronomique sans sa table FAO n'est
        # pas vérifiable, et un Kc faux mais plausible n'est rattrapable par
        # aucun test en aval.
        sa.Column("kc_source", sa.Text, nullable=False),
        sa.Column("stage_lengths_source", sa.Text, nullable=False),
        sa.Column("cycle_start_month", sa.Integer),
        sa.Column("root_depth_min_m", sa.Float, nullable=False),
        sa.Column("root_depth_max_m", sa.Float, nullable=False),
        sa.Column("depletion_fraction_p", sa.Float, nullable=False),
        sa.Column("root_and_depletion_source", sa.Text, nullable=False),
        # Ky nullable par conception : aucun coefficient documenté → aucune
        # estimation de rendement. Voir `ck_crops_ky_documented_or_explained`.
        sa.Column("yield_response_factor_ky", sa.Float),
        sa.Column("ky_source", sa.Text),
        sa.Column("ky_absent_reason_fr", sa.Text),
        sa.Column("requires_cold_chain", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("notes_fr", sa.Text),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_crops_tenant_code"),
        # Un Ky présent porte sa source ; un Ky absent porte sa raison. Le
        # silence — ni valeur, ni explication — est ce que cette contrainte
        # interdit : c'est ainsi qu'une absence devient un oubli.
        sa.CheckConstraint(
            "(yield_response_factor_ky IS NOT NULL AND ky_source IS NOT NULL) "
            "OR (yield_response_factor_ky IS NULL AND ky_absent_reason_fr IS NOT NULL)",
            name="ck_crops_ky_documented_or_explained",
        ),
        sa.CheckConstraint(
            "root_depth_min_m > 0 AND root_depth_max_m >= root_depth_min_m",
            name="ck_crops_root_depth_ordered",
        ),
        sa.CheckConstraint(
            "depletion_fraction_p > 0 AND depletion_fraction_p < 1",
            name="ck_crops_depletion_fraction_range",
        ),
        schema=APP,
    )

    op.create_table(
        "crop_growth_stages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        *_reference_columns(),
        sa.Column(
            "crop_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{APP}.crops.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("length_days", sa.Integer, nullable=False),
        sa.Column("kc", sa.Float, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.UniqueConstraint("crop_id", "stage", name="uq_stage_crop_stage"),
        sa.CheckConstraint("length_days > 0", name="ck_stage_length_positive"),
        schema=APP,
    )

    # -- référentiel : sols ----------------------------------------------
    op.create_table(
        "soil_profiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        *_reference_columns(),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name_fr", sa.String(120), nullable=False),
        sa.Column("name_en", sa.String(120), nullable=False),
        sa.Column("theta_fc_m3_m3", sa.Float, nullable=False),
        sa.Column("theta_wp_m3_m3", sa.Float, nullable=False),
        sa.Column("theta_fc_range_low", sa.Float),
        sa.Column("theta_fc_range_high", sa.Float),
        sa.Column("theta_wp_range_low", sa.Float),
        sa.Column("theta_wp_range_high", sa.Float),
        sa.Column("hydraulic_source", sa.Text, nullable=False),
        sa.Column("infiltration_rate_mm_per_hour", sa.Float),
        sa.Column("infiltration_source", sa.Text),
        sa.Column("description_fr", sa.Text),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_soils_tenant_code"),
        # Un sol dont le point de flétrissement dépasse la capacité au champ
        # donnerait une réserve utile négative, donc un Ks aberrant. La base
        # refuse plutôt que le moteur ne le découvre.
        sa.CheckConstraint(
            "theta_fc_m3_m3 > theta_wp_m3_m3", name="ck_soils_fc_above_wp"
        ),
        schema=APP,
    )

    op.create_table(
        "irrigation_systems",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        *_reference_columns(),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name_fr", sa.String(120), nullable=False),
        sa.Column("name_en", sa.String(120), nullable=False),
        sa.Column("efficiency", sa.Float, nullable=False),
        sa.Column("efficiency_range_low", sa.Float),
        sa.Column("efficiency_range_high", sa.Float),
        sa.Column("efficiency_source", sa.Text, nullable=False),
        sa.Column("wets_whole_surface", sa.Boolean, nullable=False),
        sa.Column("description_fr", sa.Text),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_systems_tenant_code"),
        sa.CheckConstraint(
            "efficiency > 0 AND efficiency <= 1", name="ck_systems_efficiency_fraction"
        ),
        schema=APP,
    )

    # -- métier : parcelles ----------------------------------------------
    op.create_table(
        "fields",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        _tenant_column(),
        sa.Column(
            "site_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{APP}.sites.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name_fr", sa.String(200), nullable=False),
        sa.Column("area_ha", sa.Float, nullable=False),
        sa.Column("latitude", sa.Float, nullable=False),
        sa.Column("longitude", sa.Float, nullable=False),
        sa.Column("elevation_m", sa.Float),
        sa.Column("distance_to_coast_km", sa.Float),
        sa.Column("boundary_geojson", sa.JSON),
        sa.Column("crop_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{APP}.crops.id", ondelete="SET NULL")),
        sa.Column("soil_profile_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{APP}.soil_profiles.id", ondelete="SET NULL")),
        sa.Column("irrigation_system_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{APP}.irrigation_systems.id", ondelete="SET NULL")),
        sa.Column("planting_date", sa.Date),
        sa.Column("declared_growth_stage", sa.String(20)),
        # Nullables par conception : pas de débit → pas de durée ; pas de tarif
        # → pas de coût. Un défaut non nul produirait des chiffres faux et
        # crédibles.
        sa.Column("flow_rate_m3_per_hour", sa.Float),
        sa.Column("water_cost_per_m3", sa.Float),
        sa.Column("seasonal_quota_m3", sa.Float),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_fields_tenant_code"),
        sa.CheckConstraint("area_ha > 0", name="ck_fields_area_positive"),
        schema=APP,
    )
    op.create_index("ix_fields_tenant_site", "fields", ["tenant_id", "site_id"], schema=APP)

    op.create_table(
        "soil_moisture_readings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        _tenant_column(),
        sa.Column(
            "field_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{APP}.fields.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("value_pct", sa.Float, nullable=False),
        sa.Column("depth_cm", sa.Float),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sensor_id", sa.String(80)),
        sa.Column("note_fr", sa.Text),
        *_provenance_columns(),
        sa.CheckConstraint(
            "value_pct >= 0 AND value_pct <= 100", name="ck_moisture_percentage"
        ),
        schema=APP,
    )
    op.create_index(
        "ix_moisture_tenant_field_time",
        "soil_moisture_readings",
        ["tenant_id", "field_id", "recorded_at"],
        schema=APP,
    )

    op.create_table(
        "weather_observations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        _tenant_column(),
        sa.Column(
            "site_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{APP}.sites.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("temperature_c", sa.Float),
        sa.Column("relative_humidity_pct", sa.Float),
        sa.Column("wind_speed_m_s", sa.Float),
        sa.Column("wind_gust_kmh", sa.Float),
        sa.Column("precipitation_mm", sa.Float),
        sa.Column("precipitation_probability_pct", sa.Float),
        sa.Column("solar_radiation_mj_m2", sa.Float),
        sa.Column("surface_pressure_hpa", sa.Float),
        sa.Column("provider_et0_mm", sa.Float),
        *_provenance_columns(),
        sa.UniqueConstraint(
            "tenant_id", "site_id", "observed_at", "provider", name="uq_weather_point"
        ),
        schema=APP,
    )
    op.create_index(
        "ix_weather_tenant_site_time",
        "weather_observations",
        ["tenant_id", "site_id", "observed_at"],
        schema=APP,
    )

    # -- métier : logistique ---------------------------------------------
    op.create_table(
        "products",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        _tenant_column(),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name_fr", sa.String(200), nullable=False),
        sa.Column("crop_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{APP}.crops.id", ondelete="SET NULL")),
        sa.Column("unit_price_mad_per_tonne", sa.Float),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_products_tenant_code"),
        schema=APP,
    )

    op.create_table(
        "suppliers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        _tenant_column(),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name_fr", sa.String(200), nullable=False),
        sa.Column("site_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{APP}.sites.id", ondelete="SET NULL")),
        sa.Column("lead_time_days", sa.Float),
        sa.Column("daily_capacity_tonnes", sa.Float),
        # Nullable : une fiabilité par défaut de 0,9 serait une invention, et
        # elle entrerait directement dans un arbitrage de sourcing.
        sa.Column("observed_reliability", sa.Float),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_suppliers_tenant_code"),
        schema=APP,
    )

    op.create_table(
        "shipments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        _tenant_column(),
        sa.Column("reference", sa.String(40), nullable=False),
        sa.Column("product_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{APP}.products.id", ondelete="SET NULL")),
        sa.Column("origin_site_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{APP}.sites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("destination_site_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{APP}.sites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_field_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{APP}.fields.id", ondelete="SET NULL")),
        sa.Column("volume_tonnes", sa.Float, nullable=False),
        sa.Column("transport_mode", sa.String(24), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("optimization_profile", sa.String(40)),
        sa.Column("departure_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sla_deadline_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "reference", name="uq_shipments_tenant_ref"),
        sa.CheckConstraint("volume_tonnes > 0", name="ck_shipments_volume_positive"),
        schema=APP,
    )
    op.create_index(
        "ix_shipments_tenant_status", "shipments", ["tenant_id", "status"], schema=APP
    )

    # -- propriété, politiques, privilèges --------------------------------
    all_new = (*BUSINESS_TABLES, *REFERENCE_TABLES, "regions")
    for table in all_new:
        conn.execute(sa.text(f"ALTER TABLE {APP}.{table} OWNER TO {OWNER}"))

    for table in BUSINESS_TABLES:
        conn.execute(sa.text(f"ALTER TABLE {APP}.{table} ENABLE ROW LEVEL SECURITY"))
        conn.execute(sa.text(f"ALTER TABLE {APP}.{table} FORCE ROW LEVEL SECURITY"))
        conn.execute(sa.text(_business_policy(table)))

    for table in REFERENCE_TABLES:
        conn.execute(sa.text(f"ALTER TABLE {APP}.{table} ENABLE ROW LEVEL SECURITY"))
        conn.execute(sa.text(f"ALTER TABLE {APP}.{table} FORCE ROW LEVEL SECURITY"))
        conn.execute(sa.text(_reference_policy(table)))
        conn.execute(sa.text(_reference_admin_policy(table)))

    # `regions` est un référentiel purement public : aucune colonne `tenant_id`,
    # donc aucune politique. Lisible par tous, modifiable par le seul
    # propriétaire.
    conn.execute(sa.text(f"GRANT SELECT ON {APP}.regions TO {APP_ROLE}"))

    conn.execute(
        sa.text(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {APP} "
            f"TO {APP_ROLE}"
        )
    )

    # -- surface analytique ------------------------------------------------
    for name, body, comment in ANALYTICS_VIEWS:
        for table in _tables_in(body):
            conn.execute(sa.text(f"GRANT SELECT ON {APP}.{table} TO {ANALYTICS_OWNER}"))
        conn.execute(sa.text(f"SET LOCAL ROLE {ANALYTICS_OWNER}"))
        conn.execute(sa.text(f"CREATE VIEW {ANALYTICS}.{name} AS {body}"))
        conn.execute(sa.text("RESET ROLE"))
        literal = "'" + comment.replace("'", "''") + "'"
        conn.execute(sa.text(f"COMMENT ON VIEW {ANALYTICS}.{name} IS {literal}"))
        conn.execute(sa.text(f"GRANT SELECT ON {ANALYTICS}.{name} TO {ANALYTICS_RO}"))


def _tables_in(body: str) -> set[str]:
    """Tables de `app` référencées par le corps d'une vue.

    Extrait du SQL plutôt que listé à côté : une liste écrite à la main diverge
    dès qu'une jointure est ajoutée, et la vue échouerait alors à la création
    avec un « permission denied » dont la cause serait loin.
    """
    import re

    return set(re.findall(r"\bapp\.(\w+)", body))


def downgrade() -> None:
    conn = op.get_bind()
    for name, _body, _comment in ANALYTICS_VIEWS:
        conn.execute(sa.text(f"DROP VIEW IF EXISTS {ANALYTICS}.{name}"))
    for table in (
        "shipments",
        "suppliers",
        "products",
        "weather_observations",
        "soil_moisture_readings",
        "fields",
        "irrigation_systems",
        "soil_profiles",
        "crop_growth_stages",
        "crops",
        "regions",
    ):
        op.drop_table(table, schema=APP)
