"""Classement multicritère, indépendamment de tout domaine.

Le module est testé sur des candidats fictifs plutôt que sur des expéditions :
c'est ce qui prouve qu'il est réellement générique, et c'est ce que la décision
0002 promet — un classement partagé par la logistique, l'approvisionnement et
les stocks, et **pas** par l'irrigation.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.domain.decision import RejectionReason
from app.domain.ranking import (
    CriterionSpec,
    OptimizationProfile,
    rank_candidates,
)

CRITERIA = (
    CriterionSpec("risk", "Risque de perturbation", "score"),
    CriterionSpec("cost", "Coût estimé", "MAD"),
    CriterionSpec("duration", "Durée", "h"),
    CriterionSpec("reliability", "Fiabilité de l'axe", "ratio", higher_is_worse=False),
)

COLD_CHAIN = OptimizationProfile(
    code="PERISSABLE_FROID",
    label_fr="Périssable sous chaîne du froid",
    rationale_fr=(
        "La valeur marchande dépend de l'intégrité du produit à l'arrivée : le "
        "risque et le délai priment sur le coût."
    ),
    weights={"risk": 0.45, "cost": 0.10, "duration": 0.35, "reliability": 0.10},
)

BULK = OptimizationProfile(
    code="VRAC_FAIBLE_MARGE",
    label_fr="Vrac à faible marge",
    rationale_fr="La marge unitaire ne peut pas absorber un surcoût logistique.",
    weights={"risk": 0.20, "cost": 0.60, "duration": 0.10, "reliability": 0.10},
)


@dataclass
class Candidate:
    id: str
    label_fr: str
    values: dict[str, float]
    blocking: tuple[RejectionReason, ...] = ()
    is_current_plan: bool = False
    description_fr: str = "option de test"

    def blocking_reasons(self) -> tuple[RejectionReason, ...]:
        return self.blocking

    def criterion_value(self, key: str) -> float | None:
        return self.values.get(key)


def _cheap_but_risky() -> Candidate:
    return Candidate("cheap", "Bon marché, exposé",
                     {"risk": 0.9, "cost": 10_000, "duration": 10, "reliability": 0.6})


def _safe_but_dear() -> Candidate:
    return Candidate("safe", "Sûr, coûteux",
                     {"risk": 0.1, "cost": 40_000, "duration": 8, "reliability": 0.95})


def test_the_profile_decides_the_winner_not_the_engine() -> None:
    """Le même lot d'options, deux profils, deux gagnants.

    C'est la propriété qui justifie les profils : sans elle, la pondération
    serait décorative et le produit prétendrait arbitrer alors qu'il trancherait
    toujours pareil.
    """
    options = [_cheap_but_risky(), _safe_but_dear()]

    cold = rank_candidates(options, criteria=CRITERIA, profile=COLD_CHAIN)
    bulk = rank_candidates(options, criteria=CRITERIA, profile=BULK)

    assert cold.recommended is not None and cold.recommended.id == "safe"
    assert bulk.recommended is not None and bulk.recommended.id == "cheap"


def test_infeasible_options_are_returned_with_their_reason_but_never_ranked() -> None:
    """Une option qui viole une contrainte dure sort du classement.

    La garder, même en dernier, la placerait dans un tableau où l'utilisateur la
    lit comme un choix possible — et recommander un plan qui ne respecte pas
    l'engagement de service est pire qu'inutile.
    """
    blocked = Candidate(
        "blocked",
        "Entrepôt sans froid",
        {"risk": 0.0, "cost": 1.0, "duration": 1.0, "reliability": 1.0},
        blocking=(
            RejectionReason(
                code="NO_COLD_STORAGE",
                message_fr="Ce site ne dispose pas de stockage frigorifique.",
            ),
        ),
    )
    result = rank_candidates(
        [_cheap_but_risky(), _safe_but_dear(), blocked],
        criteria=CRITERIA,
        profile=COLD_CHAIN,
    )

    rejected = [a for a in result.alternatives if not a.is_feasible]
    assert [a.id for a in rejected] == ["blocked"]
    assert rejected[0].rank is None
    assert rejected[0].rejection_reasons[0].code == "NO_COLD_STORAGE"
    # ... et malgré ses valeurs parfaites, elle n'est pas recommandée.
    assert result.recommended is not None and result.recommended.id != "blocked"


def test_a_criterion_where_higher_is_better_is_not_ranked_backwards() -> None:
    """La fiabilité n'est pas un coût.

    C'est la faute la plus banale du classement multicritère, et elle range les
    options exactement à l'envers sur ce critère sans rendre le score global
    absurde.
    """
    high = Candidate("high", "Axe fiable", {"reliability": 0.95})
    low = Candidate("low", "Axe peu fiable", {"reliability": 0.40})
    profile = OptimizationProfile(
        code="RELIABILITY_ONLY",
        label_fr="Fiabilité seule",
        rationale_fr="Profil de test.",
        weights={"reliability": 1.0},
    )
    result = rank_candidates([low, high], criteria=CRITERIA, profile=profile)
    assert result.recommended is not None and result.recommended.id == "high"


def test_when_no_option_is_feasible_the_engine_refuses_rather_than_picking() -> None:
    """Zéro option applicable n'est pas « la moins mauvaise ».

    Le moteur le dit et renvoie la décision à l'humain, avec les motifs.
    """
    reason = (RejectionReason(code="SLA", message_fr="Arrivée après l'échéance."),)
    result = rank_candidates(
        [
            Candidate("a", "A", {"cost": 1.0}, blocking=reason),
            Candidate("b", "B", {"cost": 2.0}, blocking=reason),
        ],
        criteria=CRITERIA,
        profile=COLD_CHAIN,
    )
    assert result.recommended is None
    assert all(not a.is_feasible for a in result.alternatives)
    assert any("décision humaine" in c for c in result.caveats_fr)


def test_two_near_identical_options_are_flagged_as_indistinguishable() -> None:
    """Un écart sous la précision du modèle ne fait pas une recommandation.

    Présenter 0,61 contre 0,60 comme un arbitrage donnerait une fausse
    impression de précision ; le dire laisse l'exploitant trancher sur un
    critère que le modèle ne connaît pas.
    """
    a = Candidate("a", "A", {"risk": 0.50, "cost": 20_000, "duration": 9, "reliability": 0.8})
    b = Candidate("b", "B", {"risk": 0.51, "cost": 20_100, "duration": 9, "reliability": 0.8})
    result = rank_candidates([a, b], criteria=CRITERIA, profile=COLD_CHAIN)
    assert any("très proches" in c for c in result.caveats_fr)


def test_a_missing_criterion_does_not_score_as_zero() -> None:
    """Un critère absent est absent, pas nul.

    Le noter à zéro ferait *gagner* le candidat sur ce critère — un candidat
    dont on ignore le coût deviendrait le moins cher.
    """
    known = Candidate("known", "Coût connu", {"risk": 0.5, "cost": 50_000})
    unknown = Candidate("unknown", "Coût inconnu", {"risk": 0.5})
    profile = OptimizationProfile(
        code="COST_HEAVY",
        label_fr="Coût dominant",
        rationale_fr="Profil de test.",
        weights={"risk": 0.2, "cost": 0.8},
    )
    result = rank_candidates([known, unknown], criteria=CRITERIA, profile=profile)

    # Le candidat sans coût n'a pas de score de coût du tout.
    assert all(s.key != "cost" for s in result.scores["unknown"])
    # ... et il ne remporte donc pas le classement par forfait.
    assert result.recommended is not None
    assert result.recommended.id == "known"


def test_weights_that_do_not_sum_to_one_are_refused() -> None:
    """Des poids qui ne somment pas à 1 rendraient les scores incomparables
    d'un profil à l'autre, et le produit compare les profils à l'écran."""
    with pytest.raises(ValueError, match="sommer à 1"):
        OptimizationProfile(
            code="BROKEN", label_fr="Cassé", rationale_fr="…",
            weights={"risk": 0.5, "cost": 0.9},
        )


def test_the_current_plan_absence_is_disclosed() -> None:
    """Sans plan actuel, les écarts affichés n'ont pas de référence."""
    result = rank_candidates(
        [_cheap_but_risky(), _safe_but_dear()], criteria=CRITERIA, profile=COLD_CHAIN
    )
    assert any("plan actuel" in c for c in result.caveats_fr)


def test_irrigation_is_deliberately_not_rankable() -> None:
    """L'irrigation n'implémente pas ce protocole, et c'est la décision 0002.

    Ce test est une garde d'intention : si quelqu'un fait un jour porter
    `blocking_reasons` et `criterion_value` au moteur d'irrigation, c'est que la
    pondération agronomique est revenue par la fenêtre.
    """
    from app.domain.irrigation import irrigation as irrigation_module

    exported = {name for name in dir(irrigation_module) if not name.startswith("_")}
    assert "criterion_value" not in exported
    assert "blocking_reasons" not in exported


def test_min_max_normalisation_exaggerates_a_two_option_lot() -> None:
    """Sur deux options, la normalisation étale toujours à 0 et 1.

    Ce n'est pas un défaut à corriger — c'est ce que fait un min-max, et il n'y
    a pas de meilleure normalisation relative sur un lot de deux. Ce test fige
    l'artefact pour que la raison d'être de l'avertissement « très proches »
    reste lisible : sans lui, un écart de coût de 0,5 % s'afficherait comme un
    avantage maximal.

    Le test existe surtout pour que personne ne « corrige » l'avertissement en
    le rebranchant sur le score composite, où l'artefact le rendrait muet.
    """
    a = Candidate("a", "A", {"cost": 20_000})
    b = Candidate("b", "B", {"cost": 20_100})
    profile = OptimizationProfile(
        code="COST_ONLY", label_fr="Coût seul", rationale_fr="…", weights={"cost": 1.0}
    )
    result = rank_candidates([a, b], criteria=CRITERIA, profile=profile)

    normalised = {cid: scores[0].normalized for cid, scores in result.scores.items()}
    assert sorted(normalised.values()) == [0.0, 1.0], normalised

    # ... et pourtant l'écart réel est de 0,5 %, donc l'avertissement tombe.
    assert any("très proches" in c for c in result.caveats_fr)
