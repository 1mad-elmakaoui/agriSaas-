"""Plans, métriques d'usage et arithmétique des quotas.

Pur : aucune base, aucun réseau. Ce module décide **ce que veut dire dépasser**,
et la couche service se charge de compter.

La distinction qui gouverne tout le reste :

* une parcelle est un **stock** — « combien en existe-t-il maintenant » ;
* un message au copilote est un **flux** — « combien en ont été consommés cette
  période ».

Les confondre produit deux défauts opposés et tous deux silencieux. Une parcelle
supprimée puis recréée consommerait deux unités si le stock était compté en
événements ; un message consommerait éternellement une unité si le flux était
compté en lignes vivantes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "PlanLimits",
    "QuotaDecision",
    "QuotaKind",
    "UsageMetric",
    "check_quota",
]


class QuotaKind(StrEnum):
    """Ce qu'on compte."""

    #: Ce qui existe à l'instant présent. Se libère quand on supprime.
    STOCK = "STOCK"
    #: Ce qui a été consommé sur la période. Ne se libère jamais.
    FLOW = "FLOW"


class UsageMetric(StrEnum):
    """Les grandeurs mesurées.

    Seuls les **flux** produisent des événements. Les stocks se comptent en
    interrogeant la table concernée : entretenir un second compteur pour des
    parcelles qui existent déjà dans `app.fields` créerait deux vérités.
    """

    AGENT_MESSAGE = "AGENT_MESSAGE"
    ANALYTICS_QUERY = "ANALYTICS_QUERY"

    @property
    def label_fr(self) -> str:
        return {
            UsageMetric.AGENT_MESSAGE: "Question au copilote",
            UsageMetric.ANALYTICS_QUERY: "Question d'analyse",
        }[self]


@dataclass(frozen=True, slots=True)
class PlanLimits:
    """Les plafonds d'un plan. `None` signifie **illimité**, jamais zéro."""

    code: str
    name_fr: str
    description_fr: str
    max_fields: int | None = None
    max_shipments: int | None = None
    max_agent_messages_per_month: int | None = None
    max_analytics_queries_per_month: int | None = None
    max_llm_spend_usd_per_month: float | None = None


@dataclass(frozen=True, slots=True)
class QuotaDecision:
    """Le verdict, avec de quoi l'expliquer.

    `message_fr` et `remedy_fr` sont rendus ici plutôt qu'à la frontière HTTP :
    un plafond dépassé doit dire **lequel**, **combien** et **quoi faire**, et
    trois appelants qui rédigeraient chacun leur phrase finiraient par en dire
    trois choses différentes.
    """

    allowed: bool
    label_fr: str
    used: float
    limit: float | None
    unit_fr: str
    message_fr: str | None = None
    remedy_fr: str | None = None

    @property
    def unlimited(self) -> bool:
        return self.limit is None

    @property
    def remaining(self) -> float | None:
        return None if self.limit is None else max(0.0, self.limit - self.used)

    @property
    def fraction(self) -> float | None:
        """Part du plafond consommée, pour une jauge. `None` si illimité."""
        if self.limit is None or self.limit <= 0:
            return None
        return min(1.0, round(self.used / self.limit, 4))


def check_quota(
    *,
    label_fr: str,
    used: float,
    limit: float | None,
    unit_fr: str,
    plan_name_fr: str,
    kind: QuotaKind,
) -> QuotaDecision:
    """Le plafond est-il atteint ?

    **`used >= limit` refuse, et non `used > limit`.** `used` est la
    consommation *avant* l'acte demandé : autoriser à égalité laisserait passer
    une unité de plus que le plan n'en vend, à chaque fois.

    Le message nomme le plan. « Quota atteint » sans le nom du plan n'apprend
    rien à quelqu'un qui ignore lequel il a.
    """
    if limit is None:
        return QuotaDecision(
            allowed=True, label_fr=label_fr, used=used, limit=None, unit_fr=unit_fr
        )

    if used < limit:
        return QuotaDecision(
            allowed=True, label_fr=label_fr, used=used, limit=limit, unit_fr=unit_fr
        )

    remedy = (
        "Supprimez une entrée devenue inutile, ou demandez un plan supérieur à "
        "votre administrateur."
        if kind is QuotaKind.STOCK
        else "Le compteur repart au début du mois prochain. Pour l'augmenter "
        "maintenant, demandez un plan supérieur à votre administrateur."
    )
    return QuotaDecision(
        allowed=False,
        label_fr=label_fr,
        used=used,
        limit=limit,
        unit_fr=unit_fr,
        message_fr=(
            f"Plafond atteint : {label_fr.lower()}, {_amount(used, unit_fr)} sur "
            f"{_amount(limit, unit_fr)} pour le plan « {plan_name_fr} »."
        ),
        remedy_fr=remedy,
    )


def _amount(value: float, unit_fr: str) -> str:
    """Un entier s'écrit sans décimale ; une dépense en porte deux.

    « 100,0 questions » se lit comme une mesure ; « 100 questions » se lit comme
    un décompte, et c'en est un.

    L'unité s'accorde : « 1 expédition », pas « 1 expéditions ». Le produit est
    en français complet, et un accord fautif dans un message d'erreur est ce qui
    fait qu'une interface professionnelle se met à ressembler à un brouillon.
    """
    if value == int(value) and unit_fr != "USD":
        return f"{int(value)} {_agreed(unit_fr, value)}".strip()
    return f"{value:.2f} {_agreed(unit_fr, value)}".strip()


def _agreed(unit_fr: str, value: float) -> str:
    """Accord en nombre. Singulier sous 2 — « 0 expédition » est correct.

    Une unité en capitales est une abréviation (USD) et ne s'accorde pas.
    """
    if abs(value) >= 2 or not unit_fr.islower() or not unit_fr.endswith("s"):
        return unit_fr
    return unit_fr[:-1]
