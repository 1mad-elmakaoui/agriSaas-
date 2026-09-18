"""Modèles de persistance — phase 1 : socle multi-tenant.

Deux règles structurent ce module :

1. **Toute table métier porte `tenant_id`, non nul et indexé.** L'isolation est
   dans le schéma, pas dans l'usage. Un test de migration énumère les tables et
   échoue si l'une manque la colonne, sa politique RLS, ou `FORCE`.
2. **`FORCE ROW LEVEL SECURITY`, jamais `ENABLE` seul.** Vérifié sur PostgreSQL
   16 : une vue détenue par le propriétaire de la table, au-dessus d'une table
   dont la RLS est simplement activée, renvoie **toutes** les organisations —
   politique présente, `\\d` l'affichant, aucune erreur. Voir
   `docs/decisions/0001-isolation-analytique.md`.

Les tables métier des autres domaines (parcelles, expéditions, stocks) arrivent
en phase 2. `sites` est ici parce que le socle a besoin d'au moins une table
tenant pour que l'isolation soit prouvée plutôt qu'affirmée.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, date, datetime
from typing import Any, ClassVar, TypeVar

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.domain.decision import DecisionDomain, HumanVerdict
from app.domain.enums import (
    CropCategory,
    DataOrigin,
    DataState,
    GrowthStage,
    ShipmentStatus,
    SiteType,
    TransportMode,
    UserRole,
)
from app.domain.quotas import UsageMetric

EnumT = TypeVar("EnumT", bound=enum.Enum)


class EnumValue(TypeDecorator[EnumT]):
    """Stocke la **valeur** d'une énumération, jamais son nom.

    Sans cela, une colonne annotée `Mapped[SiteType]` mais déclarée `String`
    ressort en `str` : le type Python est décoratif, et le premier `.label_fr`
    échoue au moment de l'affichage plutôt qu'au moment de l'écriture.

    On stocke la valeur et non le nom parce que c'est la valeur qui est le
    contrat public : une question française sur « annulé » filtre sur
    `'cancelled'`, qui est ce que la colonne contient et ce que la vue
    analytique expose.

    `sa.Enum` natif est écarté volontairement : il crée un type PostgreSQL dont
    l'ajout d'une valeur devient une migration à part entière, pour une
    contrainte que la couche applicative pose déjà.
    """

    impl = String
    cache_ok = True

    def __init__(self, enum_type: type[EnumT], length: int = 32) -> None:
        self._enum = enum_type
        super().__init__(length=length)

    def process_bind_param(self, value: EnumT | str | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if isinstance(value, self._enum):
            return str(value.value)
        # Une chaîne est acceptée mais **validée** : elle doit correspondre à un
        # membre. Laisser passer une valeur libre ferait entrer en base un état
        # que le domaine ne connaît pas, et que rien ne rattraperait ensuite.
        return str(self._enum(value).value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> EnumT | None:
        return None if value is None else self._enum(value)

SCHEMA_APP = "app"
SCHEMA_ANALYTICS = "analytics"
SCHEMA_META = "meta"


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata_schema = SCHEMA_APP
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class TenantScoped:
    """Marqueur des tables soumises à l'isolation.

    Hériter de ce marqueur est ce qui rend une table visible pour trois
    mécanismes à la fois : le dépôt refuse d'interroger une table qui ne
    l'hérite pas, la migration lui pose une politique RLS, et le test
    d'énumération vérifie que les deux ont bien eu lieu.
    """

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_APP}.tenants.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )


class ProvenanceMixin:
    """Le couple de provenance, au niveau de la colonne.

    Une valeur exposée sans son couple est le défaut que ce produit existe pour
    empêcher ; l'imposer dans le schéma le rend impossible à oublier, plutôt
    que possible à oublier discrètement.
    """

    data_state: Mapped[DataState] = mapped_column(EnumValue(DataState, 16), nullable=False)
    data_origin: Mapped[DataOrigin] = mapped_column(EnumValue(DataOrigin, 20), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)


class Tenant(Base, TimestampMixin):
    """Organisation cliente.

    Porte sa propre politique RLS, clé sur `id` plutôt que sur `tenant_id` :
    une organisation ne doit pas pouvoir lire la fiche d'une autre.
    """

    __tablename__ = "tenants"
    __table_args__ = {"schema": SCHEMA_APP}  # noqa: RUF012 - SQLAlchemy declares this as an instance variable

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    region_code: Mapped[str | None] = mapped_column(String(40))
    plan_code: Mapped[str] = mapped_column(String(40), default="COOPERATIVE")
    #: Un tenant de démonstration est signalé dans le schéma, et l'interface le
    #: porte sur chaque écran. Une donnée fabriquée ne doit jamais pouvoir être
    #: prise pour une mesure, même par un utilisateur pressé.
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    users: Mapped[list[User]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )


class UserDirectory(Base):
    """Annuaire courriel → organisation. **Global par conception.**

    L'authentification pose un problème que `FORCE ROW LEVEL SECURITY` rend
    visible : pour lire la fiche d'un utilisateur il faut connaître son
    organisation, et pour connaître son organisation il faut avoir lu sa fiche.

    Trois issues existent. Donner au rôle de connexion une politique
    `USING (true)` lui rendrait tous les utilisateurs lisibles. Passer par une
    fonction `SECURITY DEFINER` ne change rien, puisque `FORCE` soumet aussi le
    propriétaire de la table à ses politiques. La troisième est celle-ci :
    reconnaître que « à quelle organisation appartient ce courriel » est une
    question **intrinsèquement globale**, et lui donner une table qui l'est
    explicitement.

    Cette table ne contient donc **aucun secret et aucune donnée métier** — un
    courriel et un aiguillage. L'empreinte du mot de passe reste dans `users`,
    sous RLS. Elle n'est jamais exposée par un point d'entrée HTTP.

    Elle porte `tenant_id` sans hériter de :class:`TenantScoped`, ce qui est la
    seule exception du schéma. Un test énumère les exceptions et échoue si une
    seconde apparaît : l'exception doit rester un choix, pas une dérive.
    """

    __tablename__ = "user_directory"
    __table_args__ = {"schema": SCHEMA_APP}  # noqa: RUF012 - SQLAlchemy declares this as an instance variable

    email: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_APP}.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )


class User(Base, TenantScoped, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        {"schema": SCHEMA_APP},
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(EnumValue(UserRole, 32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    tenant: Mapped[Tenant] = relationship(back_populates="users")


class Site(Base, TenantScoped, ProvenanceMixin, TimestampMixin):
    """Site polymorphe : exploitation, entrepôt, plateforme ou client.

    Absorbe les `farms` d'`agriflow`. Ces objets partagent coordonnées, région
    et capacité ; trois tables dupliqueraient la même colonne de géolocalisation
    et obligeraient le moteur d'itinéraire à connaître les trois.
    """

    __tablename__ = "sites"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_sites_tenant_code"),
        Index("ix_sites_tenant_type", "tenant_id", "site_type"),
        {"schema": SCHEMA_APP},
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name_fr: Mapped[str] = mapped_column(String(200), nullable=False)
    site_type: Mapped[SiteType] = mapped_column(EnumValue(SiteType, 20), nullable=False)
    region_code: Mapped[str | None] = mapped_column(String(40))
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    elevation_m: Mapped[float | None] = mapped_column(Float)
    capacity_tonnes: Mapped[float | None] = mapped_column(Float)
    has_cold_storage: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: La frontière canonique reste un GeoJSON portable. Le miroir
    #: `geometry(Polygon, 4326)` est ajouté par migration quand PostGIS est
    #: présent : le produit doit démarrer sans, avec les fonctions spatiales
    #: désactivées et un avertissement — pas planter.
    boundary_geojson: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class AuditLog(Base, TenantScoped):
    """Qui a vu quoi, qui a approuvé quoi, qui a changé quel seuil.

    Écrit sur une session **séparée** de celle de la requête : `atlasagri`
    validait le journal au milieu de l'exécution d'un outil, ce qui validait
    aussi les écritures partielles de la transaction englobante.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_tenant_time", "tenant_id", "occurred_at"),
        {"schema": SCHEMA_APP},
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    actor_email: Mapped[str | None] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(60), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(120))
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    note: Mapped[str | None] = mapped_column(Text)


#: Tables portant `tenant_id` sans être soumises à l'isolation par RLS.
#: Toute entrée ici est une dérogation à la règle centrale du produit et doit
#: porter sa justification dans la docstring du modèle. Un test vérifie que cet
#: ensemble est exactement celui-ci : une dérogation ajoutée sans y penser fait
#: échouer la suite.
RLS_EXEMPT_TABLES: frozenset[str] = frozenset({"user_directory"})


def tenant_scoped_tables() -> tuple[str, ...]:
    """Tables soumises à l'isolation, découvertes par héritage.

    Découvertes plutôt que listées : une liste écrite à la main diverge au
    premier modèle ajouté, et c'est exactement la divergence qu'une politique
    RLS manquante ne pardonne pas.
    """
    return tuple(
        sorted(
            table.name
            for table in Base.metadata.sorted_tables
            for mapper in Base.registry.mappers
            if issubclass(mapper.class_, TenantScoped) and mapper.local_table is table
        )
    )


def reference_scoped_tables() -> tuple[str, ...]:
    """Tables de référentiel : globales, surchargeables par organisation.

    Leur politique RLS **diffère** de celle des tables métier — elles laissent
    passer la ligne globale (`tenant_id IS NULL`) en lecture. Les confondre
    donnerait à l'une des deux familles la politique de l'autre : soit le
    référentiel FAO devient invisible, soit une organisation peut le réécrire
    pour tout le monde.
    """
    return tuple(
        sorted(
            table.name
            for table in Base.metadata.sorted_tables
            for mapper in Base.registry.mappers
            if issubclass(mapper.class_, ReferenceScoped) and mapper.local_table is table
        )
    )


def tables_with_tenant_column() -> tuple[str, ...]:
    """Tables portant une colonne `tenant_id`, quel que soit leur héritage."""
    return tuple(
        sorted(
            table.name
            for table in Base.metadata.sorted_tables
            if "tenant_id" in table.columns
        )
    )


# ---------------------------------------------------------------------------
# Référentiel agronomique — global, surchargeable par organisation
# ---------------------------------------------------------------------------
class ReferenceScoped:
    """Marqueur des tables de référentiel.

    `tenant_id` nul signifie « ligne de référence globale, en lecture seule ».
    Une organisation peut poser sa propre ligne portant le même `code` : elle
    **masque** la ligne globale sans la modifier. La calibration locale doit
    rester une tâche de données, jamais une modification de code.

    La politique RLS de ces tables diffère donc de celle des tables métier :

        USING      (tenant_id IS NULL OR tenant_id = organisation courante)
        WITH CHECK (tenant_id = organisation courante)

    Lecture : le global plus le sien. Écriture : le sien seulement — une
    organisation ne peut pas altérer une valeur FAO pour tout le monde.
    """

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_APP}.tenants.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    #: Vrai uniquement pour une surcharge issue d'une mesure locale. Une ligne
    #: globale est une valeur **publiée**, pas une mesure : la distinction
    #: décide de ce que le score de fiabilité accorde à la valeur.
    is_measured: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Crop(Base, ReferenceScoped, TimestampMixin):
    """Culture : agronomie FAO-56/33 et contraintes logistiques réunies.

    `agriflow` portait les coefficients, `atlasagri` portait la chaîne du froid
    et la sensibilité au risque. Deux tables auraient laissé le moteur
    d'irrigation et le moteur d'alternatives travailler sur deux notions de
    « tomate » qui divergent à la première correction.

    Chaque valeur agronomique porte **sa table FAO**. Une valeur sans citation
    n'entre pas : un Kc faux mais plausible n'est rattrapable par aucun test en
    aval, et un agronome qui relit doit pouvoir remonter à la publication.
    """

    __tablename__ = "crops"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_crops_tenant_code"),
        {"schema": SCHEMA_APP},
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name_fr: Mapped[str] = mapped_column(String(120), nullable=False)
    name_en: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[CropCategory] = mapped_column(EnumValue(CropCategory, 20), nullable=False)
    is_perennial: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # -- FAO-56 Table 12 : coefficients culturaux
    kc_initial: Mapped[float] = mapped_column(Float, nullable=False)
    kc_mid: Mapped[float] = mapped_column(Float, nullable=False)
    kc_end: Mapped[float] = mapped_column(Float, nullable=False)
    kc_source: Mapped[str] = mapped_column(Text, nullable=False)

    # -- FAO-56 Table 11 : longueurs de stades
    stage_lengths_source: Mapped[str] = mapped_column(Text, nullable=False)
    cycle_start_month: Mapped[int | None] = mapped_column(Integer)

    # -- FAO-56 Table 22 : enracinement et fraction d'épuisement
    root_depth_min_m: Mapped[float] = mapped_column(Float, nullable=False)
    root_depth_max_m: Mapped[float] = mapped_column(Float, nullable=False)
    depletion_fraction_p: Mapped[float] = mapped_column(Float, nullable=False)
    root_and_depletion_source: Mapped[str] = mapped_column(Text, nullable=False)

    # -- FAO-33 : réponse du rendement à l'eau.
    #    Nullable **par conception**. Aucun Ky documenté → aucune estimation de
    #    rendement, jamais une valeur voisine ni une moyenne de famille.
    yield_response_factor_ky: Mapped[float | None] = mapped_column(Float)
    ky_source: Mapped[str | None] = mapped_column(Text)
    ky_absent_reason_fr: Mapped[str | None] = mapped_column(Text)

    # -- attributs logistiques, repris d'`atlasagri`
    requires_cold_chain: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    notes_fr: Mapped[str | None] = mapped_column(Text)

    stages: Mapped[list[CropGrowthStage]] = relationship(
        back_populates="crop", cascade="all, delete-orphan", order_by="CropGrowthStage.sequence"
    )


class CropGrowthStage(Base, ReferenceScoped):
    """Un stade FAO-56 d'une culture, avec sa durée et son Kc.

    Table séparée plutôt que quatre colonnes : le nombre de stades est fixe
    aujourd'hui, mais un Kc par stade **et par région** est la première
    calibration qu'un agronome marocain voudra faire, et une ligne se surcharge
    là où une colonne se migre.
    """

    __tablename__ = "crop_growth_stages"
    __table_args__ = (
        UniqueConstraint("crop_id", "stage", name="uq_stage_crop_stage"),
        {"schema": SCHEMA_APP},
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    crop_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_APP}.crops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage: Mapped[GrowthStage] = mapped_column(EnumValue(GrowthStage, 20), nullable=False)
    #: Rang explicite. L'ordre d'une énumération réordonnée par mégarde
    #: changerait l'interpolation de Kc sans que rien ne le signale.
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    length_days: Mapped[int] = mapped_column(Integer, nullable=False)
    kc: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)

    crop: Mapped[Crop] = relationship(back_populates="stages")


class SoilProfile(Base, ReferenceScoped, TimestampMixin):
    """Profil hydrique de sol par classe texturale.

    Les valeurs par défaut sont les **milieux des plages** de la FAO-56 table 19,
    et les plages elles-mêmes sont conservées : afficher 0,23 sans dire que la
    table donne 0,18–0,28 laisserait croire à une précision qui n'existe pas.

    Une organisation qui fait analyser un sol pose sa propre ligne avec
    `is_measured = true` : le moteur lit la base, donc remplacer une valeur de
    catalogue par une mesure ne demande aucune modification de code.
    """

    __tablename__ = "soil_profiles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_soils_tenant_code"),
        {"schema": SCHEMA_APP},
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name_fr: Mapped[str] = mapped_column(String(120), nullable=False)
    name_en: Mapped[str] = mapped_column(String(120), nullable=False)

    theta_fc_m3_m3: Mapped[float] = mapped_column(Float, nullable=False)
    theta_wp_m3_m3: Mapped[float] = mapped_column(Float, nullable=False)
    theta_fc_range_low: Mapped[float | None] = mapped_column(Float)
    theta_fc_range_high: Mapped[float | None] = mapped_column(Float)
    theta_wp_range_low: Mapped[float | None] = mapped_column(Float)
    theta_wp_range_high: Mapped[float | None] = mapped_column(Float)
    hydraulic_source: Mapped[str] = mapped_column(Text, nullable=False)

    #: Sert uniquement à plafonner la profondeur d'un apport unique, jamais à
    #: calculer une demande en eau. Le noter ici évite qu'un futur calcul s'en
    #: serve « parce que la colonne existe ».
    infiltration_rate_mm_per_hour: Mapped[float | None] = mapped_column(Float)
    infiltration_source: Mapped[str | None] = mapped_column(Text)

    description_fr: Mapped[str | None] = mapped_column(Text)


class IrrigationSystem(Base, ReferenceScoped, TimestampMixin):
    """Système d'irrigation et son efficience d'application."""

    __tablename__ = "irrigation_systems"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_systems_tenant_code"),
        {"schema": SCHEMA_APP},
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name_fr: Mapped[str] = mapped_column(String(120), nullable=False)
    name_en: Mapped[str] = mapped_column(String(120), nullable=False)
    efficiency: Mapped[float] = mapped_column(Float, nullable=False)
    efficiency_range_low: Mapped[float | None] = mapped_column(Float)
    efficiency_range_high: Mapped[float | None] = mapped_column(Float)
    efficiency_source: Mapped[str] = mapped_column(Text, nullable=False)
    #: Sous goutte-à-goutte, seule une fraction de la surface est humectée. Le
    #: moteur actuel calcule la dose sur toute la parcelle et **surestime** donc
    #: le besoin : conservatisme documenté, pas oubli. Cette colonne est ce qui
    #: permettra de le corriger par le Kc dual sans deviner le système.
    wets_whole_surface: Mapped[bool] = mapped_column(Boolean, nullable=False)
    description_fr: Mapped[str | None] = mapped_column(Text)


class Region(Base):
    """Région administrative marocaine. Globale, sans surcharge par organisation."""

    __tablename__ = "regions"
    __table_args__ = {"schema": SCHEMA_APP}  # noqa: RUF012 - SQLAlchemy declares this as an instance variable

    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    name_fr: Mapped[str] = mapped_column(String(120), nullable=False)
    name_ar: Mapped[str | None] = mapped_column(String(120))


# ---------------------------------------------------------------------------
# Tables métier — soumises à l'isolation
# ---------------------------------------------------------------------------
class Field(Base, TenantScoped, TimestampMixin):
    """Parcelle. Appartient à un `Site` de type exploitation.

    Trois colonnes sont **nullables par conception**, et ce n'est pas de la
    permissivité : sans débit il n'y a pas de durée, sans tarif il n'y a pas de
    coût, sans date de plantation il n'y a pas de stade estimé. Le moteur
    déclare la sortie indisponible plutôt que de substituer une valeur
    plausible. Un défaut non nul ici produirait des durées et des coûts faux et
    crédibles.
    """

    __tablename__ = "fields"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_fields_tenant_code"),
        Index("ix_fields_tenant_site", "tenant_id", "site_id"),
        CheckConstraint("area_ha > 0", name="ck_fields_area_positive"),
        {"schema": SCHEMA_APP},
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    site_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_APP}.sites.id", ondelete="CASCADE"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name_fr: Mapped[str] = mapped_column(String(200), nullable=False)
    area_ha: Mapped[float] = mapped_column(Float, nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    elevation_m: Mapped[float | None] = mapped_column(Float)
    #: Distance à la côte : sélectionne le coefficient Krs de Hargreaves-Samani
    #: (FAO-56 éq. 50) quand le rayonnement manque. Nullable — sans elle, le
    #: repli utilise le coefficient continental et le dit.
    distance_to_coast_km: Mapped[float | None] = mapped_column(Float)

    #: Frontière canonique : GeoJSON portable. Le miroir PostGIS est ajouté par
    #: migration quand l'extension est présente ; l'aire affichée est calculée
    #: en Python par l'excès sphérique, donc identique avec ou sans PostGIS.
    boundary_geojson: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    crop_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA_APP}.crops.id", ondelete="SET NULL")
    )
    soil_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA_APP}.soil_profiles.id", ondelete="SET NULL")
    )
    irrigation_system_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_APP}.irrigation_systems.id", ondelete="SET NULL"),
    )

    planting_date: Mapped[date | None] = mapped_column(Date)
    #: Stade déclaré par l'exploitant. Renseigné, il **fait foi** et prime sur
    #: l'estimation depuis la date de plantation.
    declared_growth_stage: Mapped[GrowthStage | None] = mapped_column(EnumValue(GrowthStage, 20))

    flow_rate_m3_per_hour: Mapped[float | None] = mapped_column(Float)
    water_cost_per_m3: Mapped[float | None] = mapped_column(Float)
    #: Quota saisonnier en m³, quand le périmètre en impose un. C'est une
    #: **contrainte** de dose, pas un poids d'arbitrage : voir la décision 0002.
    seasonal_quota_m3: Mapped[float | None] = mapped_column(Float)


class SoilMoistureReading(Base, TenantScoped, ProvenanceMixin):
    """Mesure d'humidité du sol, avec sa provenance.

    C'est la table où la discipline de provenance se voit le mieux : la même
    valeur — 27 % — vaut selon qu'elle vient d'une sonde, d'un carnet, d'un
    modèle ou d'un jeu de démonstration, et l'interface doit le dire. Le couple
    n'est jamais fourni par le client : le point d'entrée qui enregistre décide.
    """

    __tablename__ = "soil_moisture_readings"
    __table_args__ = (
        Index("ix_moisture_tenant_field_time", "tenant_id", "field_id", "recorded_at"),
        CheckConstraint(
            "value_pct >= 0 AND value_pct <= 100", name="ck_moisture_percentage"
        ),
        {"schema": SCHEMA_APP},
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    field_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_APP}.fields.id", ondelete="CASCADE"),
        nullable=False,
    )
    value_pct: Mapped[float] = mapped_column(Float, nullable=False)
    depth_cm: Mapped[float | None] = mapped_column(Float)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sensor_id: Mapped[str | None] = mapped_column(String(80))
    note_fr: Mapped[str | None] = mapped_column(Text)


class WeatherObservation(Base, TenantScoped, ProvenanceMixin):
    """Observation météo **horaire**.

    L'horaire est le grain de stockage et le journalier une vue dérivée — pas
    l'inverse. `agriflow` stockait au jour, `atlasagri` récupérait à l'heure ;
    or la FAO-56 définit RHmax et RHmin comme les extrema *horaires* de la
    journée, que le stockage journalier ne peut pas reconstituer. Passer à
    l'horaire améliore donc l'ET0 au lieu de la dégrader.
    """

    __tablename__ = "weather_observations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "site_id", "observed_at", "provider", name="uq_weather_point"
        ),
        Index("ix_weather_tenant_site_time", "tenant_id", "site_id", "observed_at"),
        {"schema": SCHEMA_APP},
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    site_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_APP}.sites.id", ondelete="CASCADE"),
        nullable=False,
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)

    temperature_c: Mapped[float | None] = mapped_column(Float)
    relative_humidity_pct: Mapped[float | None] = mapped_column(Float)
    wind_speed_m_s: Mapped[float | None] = mapped_column(Float)
    wind_gust_kmh: Mapped[float | None] = mapped_column(Float)
    precipitation_mm: Mapped[float | None] = mapped_column(Float)
    precipitation_probability_pct: Mapped[float | None] = mapped_column(Float)
    solar_radiation_mj_m2: Mapped[float | None] = mapped_column(Float)
    surface_pressure_hpa: Mapped[float | None] = mapped_column(Float)
    #: ET0 telle que le fournisseur la calcule. Conservée comme **contrôle
    #: croisé** de notre Penman-Monteith, jamais affichée comme une ET0
    #: concurrente : un écart au-delà du seuil configuré est un signal de
    #: qualité de données (station aberrante, coordonnées fausses, unité de vent
    #: erronée), pas un second avis.
    provider_et0_mm: Mapped[float | None] = mapped_column(Float)


class Product(Base, TenantScoped, TimestampMixin):
    """Produit expédié. Rattaché à une culture du référentiel quand il en a une."""

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_products_tenant_code"),
        {"schema": SCHEMA_APP},
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name_fr: Mapped[str] = mapped_column(String(200), nullable=False)
    crop_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA_APP}.crops.id", ondelete="SET NULL")
    )
    unit_price_mad_per_tonne: Mapped[float | None] = mapped_column(Float)


class Supplier(Base, TenantScoped, TimestampMixin):
    __tablename__ = "suppliers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_suppliers_tenant_code"),
        {"schema": SCHEMA_APP},
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name_fr: Mapped[str] = mapped_column(String(200), nullable=False)
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA_APP}.sites.id", ondelete="SET NULL")
    )
    lead_time_days: Mapped[float | None] = mapped_column(Float)
    daily_capacity_tonnes: Mapped[float | None] = mapped_column(Float)
    #: Fiabilité **observée**, non calibrée : nullable tant qu'aucun historique
    #: suffisant n'existe. Une valeur par défaut de 0,9 serait une invention.
    observed_reliability: Mapped[float | None] = mapped_column(Float)


class Shipment(Base, TenantScoped, TimestampMixin):
    __tablename__ = "shipments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "reference", name="uq_shipments_tenant_ref"),
        Index("ix_shipments_tenant_status", "tenant_id", "status"),
        CheckConstraint("volume_tonnes > 0", name="ck_shipments_volume_positive"),
        {"schema": SCHEMA_APP},
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    reference: Mapped[str] = mapped_column(String(40), nullable=False)
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA_APP}.products.id", ondelete="SET NULL")
    )
    origin_site_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_APP}.sites.id", ondelete="CASCADE"),
        nullable=False,
    )
    destination_site_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_APP}.sites.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Parcelles d'origine de la marchandise. C'est ce lien qui rend la
    #: démonstration cohérente : les tomates de EXP-1842 viennent des parcelles
    #: que le moteur d'irrigation conseille, et non d'un jeu de données voisin.
    source_field_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA_APP}.fields.id", ondelete="SET NULL")
    )
    volume_tonnes: Mapped[float] = mapped_column(Float, nullable=False)
    transport_mode: Mapped[TransportMode] = mapped_column(
        EnumValue(TransportMode, 24), nullable=False
    )
    status: Mapped[ShipmentStatus] = mapped_column(EnumValue(ShipmentStatus, 20), nullable=False)
    optimization_profile: Mapped[str | None] = mapped_column(String(40))
    departure_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sla_deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Recommendation(Base, TenantScoped, ProvenanceMixin, TimestampMixin):
    """Une décision produite par un moteur, et ce que l'humain en a fait.

    `payload` porte la décision **figée** — entrées avec leur provenance, étapes
    de calcul, hypothèses, options écartées. Pas une référence à recalculer :
    six mois plus tard, la météo et les paramètres de culture auront changé, et
    l'audit porterait sur une décision qui n'a jamais été prise.

    Une contrainte de table impose qu'un verdict rendu porte sa date. Sans elle,
    une recommandation « acceptée » sans décideur ni horodatage serait
    représentable — et c'est exactement la ligne qu'un audit cherche.
    """

    __tablename__ = "recommendations"
    __table_args__ = (
        Index("ix_recommendations_tenant_verdict", "tenant_id", "verdict"),
        Index("ix_recommendations_tenant_subject", "tenant_id", "domain", "subject_id"),
        CheckConstraint(
            "verdict IN ('PENDING', 'ACCEPTED', 'REJECTED', 'MODIFIED')",
            name="ck_recommendations_verdict",
        ),
        CheckConstraint(
            "(verdict = 'PENDING' AND decided_at IS NULL AND decided_by IS NULL) "
            "OR (verdict <> 'PENDING' AND decided_at IS NOT NULL)",
            name="ck_recommendations_verdict_is_attributed",
        ),
        {"schema": SCHEMA_APP},
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    domain: Mapped[DecisionDomain] = mapped_column(EnumValue(DecisionDomain, 20), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(120), nullable=False)
    subject_label_fr: Mapped[str] = mapped_column(String(300), nullable=False)
    headline_fr: Mapped[str] = mapped_column(Text, nullable=False)
    outcome_code: Mapped[str] = mapped_column(String(40), nullable=False)
    rationale_fr: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    verdict: Mapped[HumanVerdict] = mapped_column(
        EnumValue(HumanVerdict, 16), nullable=False, default=HumanVerdict.PENDING
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA_APP}.users.id", ondelete="SET NULL")
    )
    decision_note_fr: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA_APP}.users.id", ondelete="SET NULL")
    )


class Plan(Base):
    """Un plan tarifaire et ses plafonds.

    Référentiel de plateforme : aucune colonne `tenant_id`, donc aucune
    politique RLS. Lisible par toutes les organisations — chacune doit pouvoir
    lire les limites de son plan, et celles des autres pour savoir ce qu'un
    changement lui apporterait — et modifiable par le seul propriétaire.

    **`None` signifie illimité, jamais zéro.** Un plan Entreprise sans plafond de
    parcelles porte `None` ; un zéro s'y lirait comme une interdiction totale, et
    la vérification de quota refuserait la première parcelle.
    """

    __tablename__ = "plans"
    __table_args__ = {"schema": SCHEMA_APP}  # noqa: RUF012 - SQLAlchemy declares this as an instance variable

    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    name_fr: Mapped[str] = mapped_column(String(80), nullable=False)
    description_fr: Mapped[str] = mapped_column(Text, nullable=False)
    max_fields: Mapped[int | None] = mapped_column(Integer)
    max_shipments: Mapped[int | None] = mapped_column(Integer)
    max_agent_messages_per_month: Mapped[int | None] = mapped_column(Integer)
    max_analytics_queries_per_month: Mapped[int | None] = mapped_column(Integer)
    max_llm_spend_usd_per_month: Mapped[float | None] = mapped_column(Float)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class UsageEvent(Base, TenantScoped):
    """Un acte consommant du quota.

    Des événements plutôt qu'un compteur : un compteur entretenu à côté de ses
    événements dérive, et la dérive se découvre le jour où un client conteste
    une facture.

    `input_tokens` et `output_tokens` sont **réels** — ils viennent de la réponse
    de l'API. `cost_usd` est **dérivé** d'une grille tarifaire recopiée, et vaut
    `None` pour un modèle non tarifé : jamais zéro, qui se lirait « gratuit ».
    Les deux vivent dans des colonnes distinctes pour que personne ne présente le
    second comme le premier.
    """

    __tablename__ = "usage_events"
    __table_args__ = (
        Index("ix_usage_tenant_metric_time", "tenant_id", "metric", "occurred_at"),
        CheckConstraint("quantity > 0", name="ck_usage_quantity_positive"),
        {"schema": SCHEMA_APP},
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    metric: Mapped[UsageMetric] = mapped_column(EnumValue(UsageMetric, 30), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    model: Mapped[str | None] = mapped_column(String(80))
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA_APP}.users.id", ondelete="SET NULL")
    )
