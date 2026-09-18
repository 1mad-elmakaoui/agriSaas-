"""Sites — exploitations, entrepôts, plateformes, clients.

Sert de démonstration exécutable des deux invariants du socle : rien ne sort
sans sa provenance, et rien n'entre en la déclarant.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentAudit, CurrentContext, CurrentRepository
from app.api.schemas import ProvenanceOut, SiteCreate, SiteOut
from app.db.base import Site
from app.domain.enums import DataOrigin, DataState
from app.domain.provenance import (
    SOURCE_MANUAL_ENTRY,
    SOURCE_SEED_DEMO,
    DataSourceRef,
    reliability_level,
    reliability_score,
)

router = APIRouter(prefix="/sites", tags=["Sites"])

#: Sources connues, par identifiant. La fiche porte l'identifiant en base ; le
#: libellé affiché vient d'ici, pour qu'un même identifiant ne soit pas nommé
#: différemment d'un écran à l'autre.
_SOURCES: dict[str, DataSourceRef] = {
    SOURCE_MANUAL_ENTRY.id: SOURCE_MANUAL_ENTRY,
    SOURCE_SEED_DEMO.id: SOURCE_SEED_DEMO,
}


def _provenance_for(site: Site) -> ProvenanceOut:
    """Provenance d'une fiche de site, **lue depuis la ligne**.

    Une fiche saisie par un exploitant et une fiche issue du jeu de
    démonstration se ressemblent trait pour trait ; seule cette paire les
    distingue, et c'est pour cela qu'elle est portée par la colonne plutôt que
    reconstituée ici. Une source inconnue est rendue visible plutôt que masquée
    par un libellé de repli : une puce de source qui invente son texte est pire
    qu'une puce qui dit ne pas savoir.
    """
    state = site.data_state
    origin = site.data_origin
    source = _SOURCES.get(site.source_id)
    return ProvenanceOut(
        state=state,
        state_label_fr=state.label_fr,
        origin=origin,
        origin_label_fr=origin.label_fr,
        source_id=site.source_id,
        source_label_fr=source.label_fr if source else "Source non répertoriée",
    )


def _to_out(site: Site) -> SiteOut:
    provenance = _provenance_for(site)
    score = reliability_score([provenance.origin])
    level = reliability_level(score)
    return SiteOut(
        id=site.id,
        code=site.code,
        name_fr=site.name_fr,
        site_type=site.site_type,
        site_type_label_fr=site.site_type.label_fr,
        region_code=site.region_code,
        latitude=site.latitude,
        longitude=site.longitude,
        elevation_m=site.elevation_m,
        capacity_tonnes=site.capacity_tonnes,
        has_cold_storage=site.has_cold_storage,
        boundary_geojson=site.boundary_geojson,
        created_at=site.created_at,
        provenance=provenance,
        reliability=level,
        reliability_label_fr=level.label_fr if level else None,
    )


@router.get("", response_model=list[SiteOut], summary="Lister les sites")
async def list_sites(repo: CurrentRepository) -> list[SiteOut]:
    return [_to_out(site) for site in await repo.list(Site)]


@router.post(
    "",
    response_model=SiteOut,
    status_code=status.HTTP_201_CREATED,
    summary="Créer un site",
)
async def create_site(
    payload: SiteCreate,
    repo: CurrentRepository,
    context: CurrentContext,
    audit: CurrentAudit,
) -> SiteOut:
    site = Site(
        id=uuid.uuid4(),
        tenant_id=context.tenant_id,
        code=payload.code,
        name_fr=payload.name_fr,
        site_type=payload.site_type,
        region_code=payload.region_code,
        latitude=payload.latitude,
        longitude=payload.longitude,
        elevation_m=payload.elevation_m,
        capacity_tonnes=payload.capacity_tonnes,
        has_cold_storage=payload.has_cold_storage,
        boundary_geojson=payload.boundary_geojson,
        # Décidée par le serveur, jamais reçue du client. `SiteCreate` refuse
        # ces champs (`extra="forbid"`), donc un appelant qui tenterait de
        # déclarer « capteur » reçoit une 422, pas un silence.
        data_state=DataState.OBSERVED,
        data_origin=DataOrigin.MANUAL_ENTRY,
        source_id=SOURCE_MANUAL_ENTRY.id,
    )
    await repo.add(site)
    await audit.record(
        context,
        action="site:create",
        resource_type="SITE",
        resource_id=str(site.id),
        outcome="SUCCESS",
    )
    return _to_out(site)


@router.get("/{site_id}", response_model=SiteOut, summary="Fiche d'un site")
async def get_site(site_id: uuid.UUID, repo: CurrentRepository) -> SiteOut:
    return _to_out(await repo.get(Site, site_id))
