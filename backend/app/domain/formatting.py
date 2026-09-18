"""Nombres en français, dans les textes que l'utilisateur lit.

Les moteurs produisent des phrases — étapes de calcul, avertissements — et ces
phrases sont de l'interface. Un panneau qui affiche « 195.0 mm » sous un titre
« 4,78 mm/j » mélange deux conventions décimales dans le même écran ; le lecteur
doit alors décider, chiffre par chiffre, si un point est un séparateur décimal ou
un séparateur de milliers. Sur un coût, cette ambiguïté vaut un facteur mille.

Le séparateur de milliers est l'espace fine insécable **U+202F**, exactement
celui que `Intl.NumberFormat('fr-FR')` produit côté interface : les deux moitiés
du produit écrivent donc le même nombre de la même façon.

`fr-MA` n'est délibérément pas suivi ici : cette locale groupe les milliers avec
un point, ce qui donne « 3.936 MAD » à côté de « 4,78 mm/j » — la seule
combinaison réellement ambiguë.

Module pur : aucune dépendance, `domain/` n'en tolérerait aucune.
"""

from __future__ import annotations

__all__ = ["fr", "fr_pct"]

#: Espace fine insécable. Un espace ordinaire autoriserait un retour à la ligne
#: au milieu d'un nombre.
NARROW_NO_BREAK_SPACE = " "


def fr(value: float, decimals: int = 1) -> str:
    """« 2 186,7 » — virgule décimale, espace fine pour les milliers."""
    rendered = f"{value:,.{decimals}f}"
    integer, _, fraction = rendered.partition(".")
    integer = integer.replace(",", NARROW_NO_BREAK_SPACE)
    return f"{integer},{fraction}" if fraction else integer


def fr_pct(value: float, decimals: int = 0) -> str:
    """« 90 % » — l'espace insécable avant le signe est la règle française."""
    return f"{fr(value * 100.0, decimals)} %"
