"""Crop evapotranspiration and growth-stage handling.

ETc = ET0 x Kc  (FAO-56 eq. 31, single crop coefficient approach)

Kc depends on the crop and its growth stage. FAO-56 tabulates three values
(Kc_ini, Kc_mid, Kc_end) and interpolates linearly across the development and
late-season stages — that interpolation is implemented here.

The growth stage can be either declared by the farmer (authoritative) or
estimated from the planting date and the crop's stage lengths. An estimate is
always labelled as such; it is never presented as an observation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.domain.enums import GrowthStage, StageSource
from app.domain.formatting import fr, fr_pct

__all__ = [
    "STAGE_ORDER",
    "CropParameters",
    "CropWaterRequirement",
    "GrowthStage",
    "GrowthStageInfo",
    "calculate_crop_evapotranspiration",
    "crop_coefficient",
    "estimate_growth_stage",
    "root_depth_for_stage",
]


#: Ordre canonique des stades, dérivé de `GrowthStage.sequence`.
#:
#: Dérivé plutôt que réécrit : un tuple parallèle diverge le jour où un stade est
#: ajouté ou déplacé, et l'interpolation de Kc se ferait alors sur la mauvaise
#: durée — en produisant un résultat parfaitement plausible.
STAGE_ORDER: tuple[GrowthStage, ...] = tuple(
    sorted(GrowthStage, key=lambda stage: stage.sequence)
)


# `GrowthStage` et son libellé viennent de `app.domain.enums`.
#
# Le module portait sa propre copie, avec les mêmes membres et des **valeurs
# différentes** — `"initial"` contre `"INITIAL"`. La colonne
# `fields.declared_growth_stage` stocke la seconde : deux énumérations
# homonymes auraient donc produit un stade que la base ne reconnaît pas, sans
# aucune erreur au moment de l'écriture du code.


@dataclass(frozen=True)
class CropParameters:
    """Agronomic parameters of a crop, loaded from the crop database."""

    code: str
    name_fr: str
    kc_initial: float
    kc_mid: float
    kc_end: float
    #: Longueurs de stade, clées par la **valeur** de `GrowthStage`
    #: (`"INITIAL"`, `"DEVELOPMENT"`, …), donc par ce que la base stocke.
    stage_lengths_days: dict[str, int]
    root_depth_min_m: float
    root_depth_max_m: float
    depletion_fraction_p: float
    perennial: bool = False
    cycle_start_month: int | None = None
    yield_response_factor_ky: float | None = None
    kc_basis: str = ""
    stage_lengths_source: str = ""

    @property
    def total_cycle_days(self) -> int:
        return sum(self.stage_lengths_days.get(s.value, 0) for s in STAGE_ORDER)


@dataclass
class GrowthStageInfo:
    stage: GrowthStage
    #: Comment le stade a été obtenu. Un stade **déclaré** par l'exploitant fait
    #: foi ; un stade **estimé** depuis la date de plantation est une hypothèse
    #: qui se trompe dès qu'une saison est atypique. Les confondre ferait
    #: disparaître l'information au moment où elle compte : l'affichage du Kc.
    source: StageSource
    days_after_planting: int | None = None
    fraction_of_stage: float = 0.0
    reference_date: date | None = None
    note_fr: str = ""

    @property
    def stage_label_fr(self) -> str:
        return self.stage.label_fr

    @property
    def is_estimate(self) -> bool:
        return self.source is StageSource.ESTIMATED

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "stage_label_fr": self.stage_label_fr,
            "source": self.source.value,
            "is_estimate": self.is_estimate,
            "days_after_planting": self.days_after_planting,
            "fraction_of_stage": round(self.fraction_of_stage, 3),
            "note_fr": self.note_fr,
        }


def estimate_growth_stage(
    crop: CropParameters,
    *,
    on_date: date,
    planting_date: date | None = None,
) -> GrowthStageInfo:
    """Estimate the growth stage from the planting date (or the annual cycle).

    For annual crops the reference is the planting date supplied by the farmer.
    For perennials (olive, citrus, grape) there is no planting date that drives
    the yearly cycle, so the crop's ``cycle_start_month`` is used instead.

    The result is ALWAYS flagged as an estimate — FAO stage lengths are
    tabulated for standard conditions, not for a particular plot.
    """
    reference = planting_date
    note = ""

    if reference is None:
        if crop.perennial and crop.cycle_start_month:
            year = on_date.year if on_date.month >= crop.cycle_start_month else on_date.year - 1
            reference = date(year, crop.cycle_start_month, 1)
            note = (
                "Culture pérenne : stade estimé à partir du cycle annuel de référence "
                f"(début du cycle : mois {crop.cycle_start_month})."
            )
        else:
            raise ValueError(
                "Date de plantation manquante : le stade de développement ne peut pas être estimé."
            )
    else:
        note = "Stade estimé à partir de la date de plantation et des longueurs de stades FAO-56."

    days = (on_date - reference).days
    if days < 0:
        raise ValueError("La date de plantation est postérieure à la date du calcul.")

    # Perennials cycle every year.
    if crop.perennial and crop.total_cycle_days > 0:
        days = days % max(crop.total_cycle_days, 1)

    cumulative = 0
    for stage in STAGE_ORDER:
        length = crop.stage_lengths_days.get(stage.value, 0)
        if length <= 0:
            continue
        if days < cumulative + length:
            fraction = (days - cumulative) / length
            return GrowthStageInfo(
                stage=stage,
                source=StageSource.ESTIMATED,
                days_after_planting=days,
                fraction_of_stage=fraction,
                reference_date=reference,
                note_fr=note,
            )
        cumulative += length

    # Past the end of the documented cycle.
    return GrowthStageInfo(
        stage=GrowthStage.LATE_SEASON,
        source=StageSource.ESTIMATED,
        days_after_planting=days,
        fraction_of_stage=1.0,
        reference_date=reference,
        note_fr=note + " Le cycle documenté est dépassé : le stade arrière-saison est retenu.",
    )


def crop_coefficient(crop: CropParameters, stage_info: GrowthStageInfo) -> tuple[float, str]:
    """Return (Kc, explanation_fr) for the given stage.

    FAO-56 (fig. 21): Kc is constant at Kc_ini during the initial stage and at
    Kc_mid during mid-season, and varies linearly during the development and
    late-season stages.
    """
    f = max(0.0, min(1.0, stage_info.fraction_of_stage))
    stage = stage_info.stage

    if stage == GrowthStage.INITIAL:
        return crop.kc_initial, "Stade initial : Kc constant = Kc_ini."
    if stage == GrowthStage.DEVELOPMENT:
        kc = crop.kc_initial + f * (crop.kc_mid - crop.kc_initial)
        return kc, (
            "Stade développement : Kc interpolé linéairement entre Kc_ini "
            f"({crop.kc_initial}) et Kc_mid ({crop.kc_mid}), avancement {fr_pct(f, 0)}."
        )
    if stage == GrowthStage.MID_SEASON:
        return crop.kc_mid, "Stade mi-saison : Kc constant = Kc_mid (besoin maximal)."
    kc = crop.kc_mid + f * (crop.kc_end - crop.kc_mid)
    return kc, (
        "Stade arrière-saison : Kc interpolé linéairement entre Kc_mid "
        f"({crop.kc_mid}) et Kc_fin ({crop.kc_end}), avancement {fr_pct(f, 0)}."
    )


def root_depth_for_stage(crop: CropParameters, stage_info: GrowthStageInfo) -> tuple[float, str]:
    """Effective rooting depth in metres.

    FAO-56 (chapter 8) describes the root zone as growing from a shallow depth
    at emergence to the maximum depth around the start of mid-season. We
    interpolate linearly over the initial + development stages and use the
    maximum afterwards. Perennials always use the maximum depth.
    """
    if crop.perennial:
        return crop.root_depth_max_m, "Culture pérenne : profondeur racinaire maximale retenue."

    stage = stage_info.stage
    f = max(0.0, min(1.0, stage_info.fraction_of_stage))
    if stage == GrowthStage.INITIAL:
        progress = 0.0
    elif stage == GrowthStage.DEVELOPMENT:
        progress = f
    else:
        progress = 1.0

    depth = crop.root_depth_min_m + progress * (crop.root_depth_max_m - crop.root_depth_min_m)
    return depth, (
        f"Profondeur racinaire interpolée entre {crop.root_depth_min_m} m (levée) et "
        f"{crop.root_depth_max_m} m (plein développement) : {fr(depth, 2)} m."
    )


@dataclass
class CropWaterRequirement:
    """ETc for one day, with its full derivation."""

    etc_mm_day: float
    et0_mm_day: float
    kc: float
    stage: GrowthStage
    stage_source: StageSource
    kc_explanation_fr: str
    root_depth_m: float
    root_depth_explanation_fr: str
    depletion_fraction_p: float
    #: Limites connues du chiffre, énoncées avec lui.
    #:
    #: Une limite passée sous silence est une limite que personne ne corrige.
    #: Celle-ci est nommée : l'ajustement climatique du Kc n'est pas appliqué, et il biaise
    #: dans le sens de la **sous-irrigation**, ce qui coûte du rendement sans
    #: rien faire apparaître à l'écran.
    caveats_fr: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "caveats_fr": list(self.caveats_fr),
            "etc_mm_day": round(self.etc_mm_day, 3),
            "et0_mm_day": round(self.et0_mm_day, 3),
            "kc": round(self.kc, 3),
            "stage": self.stage.value,
            "stage_label_fr": self.stage.label_fr,
            "stage_source": self.stage_source.value,
            "kc_explanation_fr": self.kc_explanation_fr,
            "root_depth_m": round(self.root_depth_m, 3),
            "root_depth_explanation_fr": self.root_depth_explanation_fr,
            "depletion_fraction_p": self.depletion_fraction_p,
            "formula": "ETc = ET0 × Kc",
        }


#: Les Kc tabulés de la FAO-56 supposent un climat sub-humide — RHmin ≈ 45 % et
#: u₂ ≈ 2 m s⁻¹. L'intérieur marocain viole régulièrement les deux, et l'équation
#: 62 corrige le Kc en conséquence.
#:
#: Elle n'est pas appliquée ici, pour une raison précise : elle demande la
#: hauteur moyenne de la culture par stade, que le référentiel ne porte pas — et
#: la renseigner de mémoire violerait la règle qui interdit toute constante
#: agronomique non citée. Le manque est donc **déclaré avec le chiffre** plutôt
#: que corrigé approximativement.
#:
#: Le sens du biais compte : sans correction, l'ETc est **sous-estimée** dans le
#: Souss et sur les plateaux de l'Est, donc la dose aussi. Une sous-irrigation ne
#: se voit pas — la parcelle a l'air normale et le rendement baisse.
KC_CLIMATE_ADJUSTMENT_CAVEAT_FR = (
    "Le coefficient cultural n'est pas ajusté au climat local (FAO-56 éq. 62 : "
    "correction pour humidité minimale et vent hors conditions sub-humides). "
    "En conditions sèches et ventées, le besoin réel est donc probablement "
    "supérieur au chiffre indiqué."
)


def calculate_crop_evapotranspiration(
    *,
    et0_mm_day: float,
    crop: CropParameters,
    stage_info: GrowthStageInfo,
) -> CropWaterRequirement:
    """ETc = ET0 x Kc (FAO-56 eq. 31)."""
    if et0_mm_day < 0:
        raise ValueError("ET0 ne peut pas être négative.")

    kc, kc_explanation = crop_coefficient(crop, stage_info)
    root_depth, root_explanation = root_depth_for_stage(crop, stage_info)

    return CropWaterRequirement(
        caveats_fr=(KC_CLIMATE_ADJUSTMENT_CAVEAT_FR,),
        etc_mm_day=et0_mm_day * kc,
        et0_mm_day=et0_mm_day,
        kc=kc,
        stage=stage_info.stage,
        stage_source=stage_info.source,
        kc_explanation_fr=kc_explanation,
        root_depth_m=root_depth,
        root_depth_explanation_fr=root_explanation,
        depletion_fraction_p=crop.depletion_fraction_p,
    )
