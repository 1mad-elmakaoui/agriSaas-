"""Valeurs d'exemple déclarées, jamais échantillonnées.

Un agent text-to-SQL a besoin de savoir que `status` vaut `'IN_TRANSIT'` et non
`'in transit'` : sans cela il génère un filtre qui ne correspond à rien, la
requête s'exécute, renvoie zéro ligne, et l'explication annonce sereinement
qu'aucune expédition n'est en transit.

Le système d'origine tirait ces valeurs de `pg_stats`. Ici, les vues portent sur
des tables multi-locataires : une valeur venue du planificateur serait une donnée
appartenant à quelqu'un — un code de parcelle, une référence d'expédition — et
elle se retrouverait dans l'index de schéma comme dans le SQL généré.

Ce registre ne contient donc que des **valeurs stockées** : des membres
d'énumération, qui n'appartiennent à aucune organisation. Il est dérivé des
énumérations du domaine, ce qui garantit qu'il ne peut pas dériver d'elles : un
statut ajouté au domaine sans l'être ici ferait échouer un test.

C'est aussi ce qui rend vraie la règle de langue : une question française sur
« annulé » doit filtrer sur `'CANCELLED'`, parce que c'est ce que contient la
colonne.
"""

from __future__ import annotations

from app.domain.enums import (
    DataOrigin,
    DataState,
    GrowthStage,
    ShipmentStatus,
    SiteType,
    TransportMode,
    UserRole,
)

__all__ = ["ENUM_VALUES", "french_glossary"]

#: `vue.colonne` → valeurs stockées possibles.
ENUM_VALUES: dict[str, tuple[str, ...]] = {
    "v_shipments.status": tuple(s.value for s in ShipmentStatus),
    "v_shipments.transport_mode": tuple(m.value for m in TransportMode),
    "v_sites.site_type": tuple(t.value for t in SiteType),
    "v_sites.data_state": tuple(s.value for s in DataState),
    "v_sites.data_origin": tuple(o.value for o in DataOrigin),
    "v_soil_moisture_readings.data_state": tuple(s.value for s in DataState),
    "v_soil_moisture_readings.data_origin": tuple(o.value for o in DataOrigin),
    "v_fields.declared_growth_stage": tuple(g.value for g in GrowthStage),
    "v_users.role": tuple(r.value for r in UserRole),
}


def french_glossary() -> tuple[str, ...]:
    """Libellé français → valeur stockée, pour l'invite de génération.

    C'est la seule façon qu'une question posée en français produise le bon
    filtre. Sans ce pont, « les expéditions annulées » devient
    `status = 'annulé'`, qui s'exécute sans erreur et ne trouve rien — le pire
    des trois résultats possibles, parce qu'il ressemble à une réponse.
    """
    lines: list[str] = []
    for enum in (ShipmentStatus, TransportMode, SiteType, DataState, DataOrigin, UserRole):
        pairs = ", ".join(f"« {member.label_fr} » = '{member.value}'" for member in enum)
        lines.append(f"{enum.__name__} : {pairs}")
    return tuple(lines)
