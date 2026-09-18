"""Le référentiel agronomique : citations, absences, cohérence interne.

Un Kc faux mais plausible n'est rattrapable par aucun test en aval — un
exploitant ne verra pas qu'une dose est 12 % trop haute. Ces tests ne peuvent
pas vérifier qu'une valeur est *juste* : seule la publication le peut. Ils
vérifient ce qui est vérifiable et qui, en pratique, attrape les vraies erreurs :
que chaque valeur porte sa table, qu'aucune absence n'est silencieuse, et que
les valeurs sont mutuellement cohérentes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

DATA = Path(__file__).resolve().parents[1] / "app" / "data"


def _load(name: str) -> dict[str, Any]:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


CROPS = _load("crops.json")
SOILS = _load("soils.json")
SYSTEMS = _load("irrigation_systems.json")

#: Toute citation doit nommer une publication, pas se contenter d'un adjectif.
#: « valeur standard » n'est pas une source.
CITATION_MARKERS = ("FAO-56", "FAO-33", "FAO Irrigation", "FAO Training", "No. 24", "No. 5")


def _cites_a_publication(text: str) -> bool:
    return any(marker in text for marker in CITATION_MARKERS)


@pytest.mark.parametrize("crop", CROPS["crops"], ids=lambda c: str(c["code"]))
def test_every_agronomic_value_carries_its_fao_table(crop: dict[str, Any]) -> None:
    """Kc, longueurs de stades, enracinement et p citent leur table.

    Sans la citation, un agronome qui conteste une valeur ne peut pas remonter à
    la publication, et la valeur devient indiscutable par accident.
    """
    for field in ("kc_source", "stage_lengths_source", "root_and_depletion_source"):
        source = crop[field]
        assert source, f"{crop['code']}.{field} is empty"
        assert _cites_a_publication(source), f"{crop['code']}.{field} = {source!r}"


@pytest.mark.parametrize("crop", CROPS["crops"], ids=lambda c: str(c["code"]))
def test_a_missing_ky_is_explained_rather_than_silent(crop: dict[str, Any]) -> None:
    """Ky présent → source ; Ky absent → raison. Jamais ni l'un ni l'autre.

    C'est l'invariant qui empêche une absence de devenir un oubli. La base porte
    la même contrainte (`ck_crops_ky_documented_or_explained`) : ce test attrape
    l'erreur dans les données, la contrainte l'attrape à l'écriture.
    """
    ky = crop["yield_response_factor_ky"]
    if ky is None:
        reason = crop["ky_absent_reason_fr"]
        assert reason, f"{crop['code']} has no Ky and no reason given"
        assert "Ky" in reason or "rendement" in reason
    else:
        assert crop["ky_source"], f"{crop['code']} has Ky={ky} with no source"
        assert 0 < ky < 3, f"{crop['code']} Ky={ky} is outside any published range"


def test_at_least_one_crop_has_no_documented_ky() -> None:
    """Le cas « pas de Ky » doit exister dans le jeu, sinon rien ne l'exerce.

    Si toutes les cultures avaient un Ky, le chemin « estimation de rendement
    indisponible » ne serait jamais parcouru — ni par les tests, ni par la
    démonstration — et il casserait le jour où une culture sans Ky arrive.
    """
    without = [c["code"] for c in CROPS["crops"] if c["yield_response_factor_ky"] is None]
    assert without, "no crop exercises the missing-Ky path"


@pytest.mark.parametrize("crop", CROPS["crops"], ids=lambda c: str(c["code"]))
def test_crop_values_are_internally_coherent(crop: dict[str, Any]) -> None:
    assert 0 < crop["depletion_fraction_p"] < 1
    assert 0 < crop["root_depth_min_m"] <= crop["root_depth_max_m"]
    for kc_field in ("kc_initial", "kc_mid", "kc_end"):
        kc = crop[kc_field]
        # FAO-56 Table 12 ne publie aucun Kc hors de cette plage ; une valeur
        # au-delà signale une erreur de saisie, pas une culture inhabituelle.
        assert 0.1 <= kc <= 1.35, f"{crop['code']}.{kc_field} = {kc}"


def test_each_crop_has_the_four_fao_stages_in_order() -> None:
    """Quatre stades, dans l'ordre, pour chaque culture.

    Un stade manquant ferait porter à l'interpolation de Kc une durée fausse, et
    le résultat resterait parfaitement plausible.
    """
    by_crop: dict[str, list[dict[str, Any]]] = {}
    for stage in CROPS["stages"]:
        by_crop.setdefault(str(stage["crop_code"]), []).append(stage)

    codes = {str(c["code"]) for c in CROPS["crops"]}
    assert set(by_crop) == codes

    for code, stages in by_crop.items():
        ordered = sorted(stages, key=lambda s: int(s["sequence"]))
        assert [s["stage"] for s in ordered] == [
            "INITIAL",
            "DEVELOPMENT",
            "MID_SEASON",
            "LATE_SEASON",
        ], code
        assert [int(s["sequence"]) for s in ordered] == [1, 2, 3, 4], code
        assert all(int(s["length_days"]) > 0 for s in ordered), code
        assert all(s["source"] for s in ordered), code


def test_stage_lengths_match_the_crop_cycle() -> None:
    """La somme des stades est un cycle plausible.

    Une longueur aberrante déplacerait le stade estimé, donc le Kc retenu, donc
    la dose — sans qu'aucune valeur affichée ne paraisse fausse.
    """
    totals: dict[str, int] = {}
    for stage in CROPS["stages"]:
        totals[str(stage["crop_code"])] = totals.get(str(stage["crop_code"]), 0) + int(
            stage["length_days"]
        )
    for code, total in totals.items():
        assert 60 <= total <= 400, f"{code} cycle totals {total} days"


@pytest.mark.parametrize("soil", SOILS["soils"], ids=lambda s: str(s["code"]))
def test_soil_hydraulics_are_physical_and_cited(soil: dict[str, Any]) -> None:
    """Capacité au champ au-dessus du point de flétrissement, et dans sa plage.

    Un sol inversé donnerait une réserve utile négative et un Ks aberrant. La
    base porte la même contrainte ; ce test attrape l'erreur dans les données.
    """
    fc, wp = soil["theta_fc_m3_m3"], soil["theta_wp_m3_m3"]
    assert fc > wp, soil["code"]
    assert 0 < wp < fc < 0.6
    assert _cites_a_publication(soil["hydraulic_source"])
    assert soil["theta_fc_range_low"] <= fc <= soil["theta_fc_range_high"]
    assert soil["theta_wp_range_low"] <= wp <= soil["theta_wp_range_high"]


@pytest.mark.parametrize("system", SYSTEMS["systems"], ids=lambda s: str(s["code"]))
def test_irrigation_efficiency_is_a_fraction_within_its_range(
    system: dict[str, Any]
) -> None:
    efficiency = system["efficiency"]
    assert 0 < efficiency <= 1, system["code"]
    assert system["efficiency_range_low"] <= efficiency <= system["efficiency_range_high"]
    assert system["efficiency_source"], system["code"]


def test_localised_systems_are_flagged_as_not_wetting_the_whole_surface() -> None:
    """Le goutte-à-goutte n'humecte pas toute la surface, et la base le sait.

    Le moteur calcule aujourd'hui la dose sur la parcelle entière et **surestime**
    donc le besoin sous irrigation localisée. C'est un conservatisme documenté, et
    cette colonne est ce qui permettra de le corriger par le Kc dual sans avoir à
    deviner le système. Si elle devenait fausse, la correction future serait
    fausse aussi.
    """
    by_code = {s["code"]: s for s in SYSTEMS["systems"]}
    assert by_code["DRIP"]["wets_whole_surface"] is False
    assert by_code["MICRO_SPRINKLER"]["wets_whole_surface"] is False
    assert by_code["FLOOD"]["wets_whole_surface"] is True


def test_the_reference_files_say_they_are_not_measurements() -> None:
    """Chaque fichier porte son avertissement.

    Ce sont des valeurs **publiées pour des conditions standard**, pas des
    mesures marocaines. Le jour où quelqu'un les prend pour des mesures locales,
    c'est ce texte qui l'en empêche.
    """
    for payload in (CROPS, SOILS, SYSTEMS):
        meta = payload["_meta"]
        blob = " ".join(str(v) for v in meta.values()).lower()
        assert "warning" in meta or "note" in blob
        assert "calibr" in blob or "not measurements" in blob or "reference" in blob
