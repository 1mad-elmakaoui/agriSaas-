"""Discipline de provenance.

Le produit existe pour empêcher qu'une valeur simulée soit prise pour une
mesure. Ces tests vérifient que la règle est portée par le schéma et le type,
pas par la vigilance.
"""

from __future__ import annotations

import pytest

from app.api.schemas import SiteCreate
from app.db.base import (
    RLS_EXEMPT_TABLES,
    reference_scoped_tables,
    tables_with_tenant_column,
    tenant_scoped_tables,
)
from app.domain.enums import ORIGIN_BY_RELIABILITY, DataOrigin, DataState
from app.domain.provenance import (
    SOURCE_REFERENCE_FAO,
    Measure,
    reliability_level,
    reliability_score,
)


def test_no_input_schema_accepts_a_provenance_field() -> None:
    """Aucun point d'entrée n'accepte l'état ni l'origine d'une valeur.

    Un client ne doit pas pouvoir déclarer qu'une saisie manuelle vient d'un
    capteur : c'est la seule façon de garantir qu'une puce « Capteur » à l'écran
    veut dire quelque chose.
    """
    assert "data_state" not in SiteCreate.model_fields
    assert "data_origin" not in SiteCreate.model_fields
    assert "source_id" not in SiteCreate.model_fields


def test_an_input_schema_rejects_an_unknown_field_rather_than_ignoring_it() -> None:
    """`extra="forbid"` : envoyer `data_origin` est une erreur, pas un silence.

    Ignorer le champ serait pire que le refuser — l'appelant croirait avoir
    renseigné la provenance.
    """
    with pytest.raises(ValueError, match="data_origin"):
        SiteCreate(
            code="P1",
            name_fr="Parcelle",
            site_type="FARM",
            latitude=30.0,
            longitude=-9.0,
            data_origin="SENSOR",
        )


def test_a_measurement_cannot_claim_a_non_measuring_origin() -> None:
    """« Observé » exige que quelque chose ait mesuré.

    Le couple le plus dangereux est `(OBSERVED, MODEL)` : il présente un calcul
    comme un constat, et rien en aval ne peut le rattraper.
    """
    with pytest.raises(ValueError, match="rien n'a été mesuré"):
        Measure(
            value=27.0,
            unit="%",
            state=DataState.OBSERVED,
            origin=DataOrigin.MODEL,
            source=SOURCE_REFERENCE_FAO,
        )


def test_reliability_weights_are_strictly_decreasing() -> None:
    """Deux origines de même poids rendraient le score aveugle à une dégradation."""
    weights = [o.reliability_weight for o in ORIGIN_BY_RELIABILITY]
    assert weights == sorted(weights, reverse=True)
    assert len(set(weights)) == len(weights), "two origins share a weight"


def test_the_demo_tenant_cannot_report_high_reliability() -> None:
    """Un jeu de démonstration ne doit jamais s'annoncer « Fiabilité : Élevée ».

    C'est ce qui fait qu'une démonstration reste lisible comme une
    démonstration, même par quelqu'un qui arrive en cours de réunion.
    """
    level = reliability_level(reliability_score([DataOrigin.SEED_DEMO] * 5))
    assert level is not None
    assert level.label_fr == "Faible"


def test_no_reading_is_not_zero_reliability() -> None:
    """Aucune entrée n'est une fiabilité *inconnue*, pas une fiabilité nulle.

    Renvoyer 0.0 afficherait « Faible » là où il faut afficher « indisponible ».
    """
    assert reliability_score([]) is None
    assert reliability_level(None) is None


def test_every_tenant_table_falls_into_exactly_one_family() -> None:
    """Trois familles, une partition, aucune table entre deux chaises.

    Une table métier a la politique stricte, une table de référentiel la
    politique qui laisse passer la ligne globale, et `user_directory` n'en a
    aucune. Une table qui n'appartient à aucune famille recevrait la politique
    de personne ; une table qui appartiendrait à deux recevrait la mauvaise.
    Le test échoue dans les deux cas.
    """
    business = set(tenant_scoped_tables())
    reference = set(reference_scoped_tables())
    exempt = set(RLS_EXEMPT_TABLES)
    everything = set(tables_with_tenant_column())

    assert not (business & reference), f"tables in two families: {business & reference}"
    unclassified = everything - business - reference - exempt
    assert not unclassified, (
        "a table carries tenant_id without belonging to any family, so no "
        f"policy applies to it: {sorted(unclassified)}"
    )
    assert business | reference | exempt == everything
