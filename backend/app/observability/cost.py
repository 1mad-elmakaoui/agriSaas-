"""Comptabilité des jetons et du coût.

Une seule règle porte tout le module : **un modèle absent de la table de prix
rapporte un coût inconnu, jamais zéro.** Un zéro se propage dans une somme sans
laisser de trace, et la facturation par organisation (§6) le lirait comme une
consommation nulle. Un `None` casse la somme et se remarque.

Les prix sont ceux de l'API Anthropic de première main, en dollars par million
de jetons. Ils changent : c'est une table de configuration versionnée, pas une
constante.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["MODEL_PRICING", "TokenUsage", "estimate_cost_usd"]

#: Prix par million de jetons, en dollars. Source : documentation tarifaire
#: Anthropic, relevée le 2026-06-24.
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-fable-5": (10.00, 50.00),
}


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Consommation d'un appel de modèle.

    `cost_usd` vaut `None` — et non `0.0` — quand le modèle n'est pas tarifé.
    L'interface affiche alors « coût inconnu » plutôt qu'un total faux.
    """

    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None

    @property
    def is_priced(self) -> bool:
        return self.cost_usd is not None

    @property
    def cost_label_fr(self) -> str:
        if self.cost_usd is None:
            return "Coût inconnu : modèle absent de la table tarifaire."
        return f"{self.cost_usd:.4f} USD"


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Coût d'un appel, ou `None` si le modèle n'est pas tarifé."""
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        return None
    input_price, output_price = pricing
    return (input_tokens / 1_000_000) * input_price + (
        output_tokens / 1_000_000
    ) * output_price


def total_cost_usd(usages: list[TokenUsage]) -> float | None:
    """Somme des coûts, ou `None` si **au moins un** appel n'est pas tarifé.

    Additionner en ignorant l'inconnu produirait un total qui a l'air complet et
    ne l'est pas. Une somme partielle doit se déclarer partielle.
    """
    if any(u.cost_usd is None for u in usages):
        return None
    return sum(u.cost_usd or 0.0 for u in usages)
