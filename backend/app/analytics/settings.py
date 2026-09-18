"""Réglages de la couche 1 du socle SQL.

Séparés de `app.core.config` : ce sont des **décisions de sécurité**, pas des
préférences d'exploitation, et elles doivent se lire d'un seul endroit lorsqu'on
audite ce qui est autorisé à atteindre la base.

Une valeur par défaut change ici le comportement du validateur pour toutes les
organisations. Élargir `extra_allowed_functions` est une décision de sécurité :
voir `app/analytics/validation/functions.py` pour la raison du refus par défaut.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["ValidationSettings"]


@dataclass(frozen=True, slots=True)
class ValidationSettings:
    #: Une jointure sur des colonnes de types compatibles mais sans clé
    #: étrangère déclarée est un avertissement, pas une erreur. Les entrepôts
    #: laissent souvent tomber les contraintes, et les jointures sur dimension
    #: de date, clé métier ou plage n'ont aucune FK à désigner : les rejeter
    #: casserait des requêtes correctes.
    #:
    #: Ici, en revanche, la surface analytique est faite de **vues que nous
    #: écrivons**, dont les jointures sont déjà résolues. Le mode strict est
    #: donc tenable — et il l'est d'autant plus qu'une jointure inventée entre
    #: deux vues produit un résultat plausible et faux.
    strict_joins: bool = False

    allow_select_star: bool = False
    require_limit: bool = True

    #: Un `CROSS JOIN` explicite est-il une intention, ou un accident ?
    #:
    #: Le code d'origine le tenait pour délibéré : un humain qui écrit
    #: `CROSS JOIN` sait ce qu'il demande. Ici l'auteur est un **modèle**, et
    #: « le modèle a écrit CROSS JOIN » n'est pas une preuve d'intention — c'est
    #: seulement la preuve qu'il a écrit CROSS JOIN. Toute la prémisse du socle
    #: est que l'auteur du SQL n'est pas de confiance.
    #:
    #: Le produit cartésien de `v_fields` et `v_sites` s'exécute, renvoie des
    #: lignes, et l'explication décrit sereinement un résultat qui ne veut rien
    #: dire. Les jointures latérales et les fonctions de table restent
    #: autorisées : ce sont des constructions différentes, pas des produits.
    reject_explicit_cross_join: bool = True

    extra_allowed_functions: tuple[str, ...] = field(default_factory=tuple)
    #: L'emporte toujours sur l'autorisation, y compris sur les ajouts d'un
    #: exploitant.
    extra_denied_functions: tuple[str, ...] = field(default_factory=tuple)

    max_join_count: int = 12
    max_query_length: int = 20_000

    def __post_init__(self) -> None:
        if not 1 <= self.max_join_count <= 64:
            raise ValueError("max_join_count doit être compris entre 1 et 64.")
        if self.max_query_length < 100:
            raise ValueError("max_query_length doit valoir au moins 100.")
