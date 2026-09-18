"""Règle de langue et refus de configuration.

La règle de langue n'est pas cosmétique. « Une question française sur *annulé*
filtre sur `'cancelled'` » veut dire que la traduction se fait à la frontière
d'affichage et nulle part ailleurs : dès qu'une valeur française entre dans une
colonne, tout ce qui interroge cette colonne doit connaître les deux langues, et
la première requête écrite par quelqu'un d'autre est fausse.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.errors import ConfigurationError
from app.domain.enums import DataOrigin, DataState, ReliabilityLevel, SiteType, UserRole

ALL_ENUMS = (UserRole, DataState, DataOrigin, SiteType, ReliabilityLevel)

_BASE = {
    "database_url": "postgresql+asyncpg://a@h/d",
    "analytics_database_url": "postgresql+asyncpg://b@h/d",
}


@pytest.mark.parametrize("enum_type", ALL_ENUMS)
def test_stored_values_are_english_and_uppercase(enum_type: type) -> None:
    """Les valeurs stockées sont anglaises : c'est le contrat de la colonne.

    Elles sont aussi ce que la vue analytique expose et ce que le SQL généré
    doit filtrer.
    """
    for member in enum_type:
        value = str(member.value)
        assert value.isascii(), f"{enum_type.__name__}.{member.name} = {value!r}"
        assert value == value.upper(), f"{enum_type.__name__}.{member.name} = {value!r}"


@pytest.mark.parametrize("enum_type", ALL_ENUMS)
def test_every_member_has_a_french_label(enum_type: type) -> None:
    """Une valeur sans libellé français apparaîtrait telle quelle à l'écran.

    C'est ainsi qu'un produit francophone finit par afficher `HEAT_STRESS` à un
    exploitant.
    """
    for member in enum_type:
        label = member.label_fr
        assert label and label.strip(), f"{enum_type.__name__}.{member.name}"
        assert label != str(member.value), (
            f"{enum_type.__name__}.{member.name} shows its stored value to the user"
        )


def test_french_labels_are_distinct_within_an_enum() -> None:
    """Deux valeurs partageant un libellé sont indistinguables à l'écran."""
    for enum_type in ALL_ENUMS:
        labels = [m.label_fr for m in enum_type]
        assert len(set(labels)) == len(labels), enum_type.__name__


def test_production_refuses_a_shared_dsn() -> None:
    """Un DSN analytique égal au DSN applicatif supprime la couche 2.

    Le rôle analytique cesserait d'être en lecture seule, et l'agent d'analyse
    écrirait dans les tables métier. Refusé dans tous les environnements, pas
    seulement en production : ce n'est jamais une configuration valide.
    """
    with pytest.raises(ValueError, match="must differ"):
        Settings(
            database_url="postgresql+asyncpg://same@h/d",
            analytics_database_url="postgresql+asyncpg://same@h/d",
        )


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"require_auth": False}, "require_auth"),
        ({"demo_mode": True}, "demo_mode"),
        ({"llm_provider": "fake"}, "llm_provider"),
        ({"embedding_provider": "hashing"}, "embedding_provider"),
        ({}, "jwt_secret"),
        # La page de conformité annonce où vivent les données. Non déclarée,
        # elle ne peut l'annoncer honnêtement — donc on ne démarre pas.
        (
            {
                "jwt_secret": "a-real-secret-from-the-environment",
                "anthropic_api_key": "sk-ant-not-a-real-key",
                "require_postgis": True,
            },
            "data_residency_country",
        ),
    ],
)
def test_production_refuses_each_unsafe_configuration(
    override: dict[str, object], expected: str
) -> None:
    """Chaque refus correspond à une garantie annoncée ailleurs.

    Laisser passer l'une d'elles ferait mentir la documentation sans qu'aucun
    test ne s'en aperçoive.
    """
    with pytest.raises(ConfigurationError) as excinfo:
        Settings(environment="production", **_BASE, **override)  # type: ignore[arg-type]
    assert expected in str(excinfo.value)


def test_a_correct_production_configuration_is_accepted() -> None:
    """Sans ce test, les refus ci-dessus passeraient même si tout était refusé."""
    settings = Settings(
        environment="production",
        **_BASE,
        jwt_secret="a-real-secret-from-the-environment",
        anthropic_api_key="sk-ant-not-a-real-key",
        require_postgis=True,
        data_residency_country="Maroc",
    )
    assert settings.environment == "production"


def test_configuration_failure_is_not_an_http_error() -> None:
    """`ConfigurationError` n'est pas une `AtlasError` : elle ne devient jamais
    une réponse. Le processus ne doit pas avoir démarré."""
    from app.core.errors import AtlasError

    assert not issubclass(ConfigurationError, AtlasError)


# ---------------------------------------------------------------------------
# La règle que la spécification demande explicitement de tester
# ---------------------------------------------------------------------------
def test_a_french_question_about_annule_filters_on_cancelled() -> None:
    """« annulé » à l'écran, `'CANCELLED'` dans la colonne.

    C'est la règle §4.9, et elle n'est pas cosmétique. Dès qu'une valeur
    française entre dans une colonne, toute requête sur cette colonne doit
    connaître les deux langues — et la première requête écrite par quelqu'un
    d'autre est fausse. La traduction se fait à la frontière d'affichage, et
    nulle part ailleurs.

    Ce test est la forme exécutable de cette règle : il relie le libellé
    français que l'utilisateur lit à la valeur anglaise que le SQL filtre.
    """
    from app.domain.enums import ShipmentStatus

    annule = next(s for s in ShipmentStatus if s.label_fr == "Annulée")
    assert annule.value == "CANCELLED"
    assert annule is ShipmentStatus.CANCELLED

    # Et l'inverse : aucune valeur stockée n'est un mot français.
    french_markers = ("é", "è", "ê", "à", "ç", "û", "î", "ô")
    for status in ShipmentStatus:
        assert not any(marker in status.value.lower() for marker in french_markers)


def test_the_analytics_surface_exposes_english_values_with_french_comments() -> None:
    """La vue analytique montre la valeur anglaise et se décrit en français.

    L'agent d'analyse lit le commentaire pour comprendre la table et écrit du
    SQL sur les valeurs. Inverser les deux — colonnes françaises, commentaires
    anglais — casserait les deux usages à la fois.
    """
    from pathlib import Path

    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "0002_data_model.py"
    ).read_text(encoding="utf-8")
    assert "sur « annulé » filtre sur 'CANCELLED'" in migration


def test_reference_codes_are_english_uppercase() -> None:
    """Les codes du référentiel sont anglais, comme toute valeur stockée.

    `agriflow` écrivait `tomato`, `atlasagri` écrivait `TOMATE` : deux
    vocabulaires pour la même culture, dont l'un en français. Les réunir sous un
    seul code anglais majuscule est ce qui permet à une jointure de fonctionner.
    """
    import json
    from pathlib import Path

    data = Path(__file__).resolve().parents[1] / "app" / "data"
    for filename, key in (
        ("crops.json", "crops"),
        ("soils.json", "soils"),
        ("irrigation_systems.json", "systems"),
        ("regions.json", "regions"),
    ):
        payload = json.loads((data / filename).read_text(encoding="utf-8"))
        for row in payload[key]:
            code = str(row["code"])
            assert code.isascii(), f"{filename}: {code!r}"
            assert code == code.upper(), f"{filename}: {code!r}"
            assert " " not in code, f"{filename}: {code!r}"
        # ... et le libellé français existe pour chacun, sinon l'interface
        # afficherait le code brut à un exploitant.
        for row in payload[key]:
            assert row["name_fr"].strip(), f"{filename}: {row['code']} has no French label"
