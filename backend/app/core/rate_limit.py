"""Limitation de débit : fenêtre glissante, en mémoire du processus.

Deux limites, pas une. Une requête de liste coûte une requête SQL indexée ; une
question au copilote coûte plusieurs appels de modèle, plusieurs secondes et de
l'argent réel. Les soumettre au même seuil revient à choisir entre laisser
saturer le modèle et brider l'interface.

**Ce que cette implémentation garantit, et ce qu'elle ne garantit pas.** Le
compteur vit dans la mémoire du processus. Avec plusieurs travailleurs, chacun
applique le seuil pour sa part du trafic : le seuil effectif est donc multiplié
par le nombre de travailleurs, et un redémarrage remet les compteurs à zéro. Ce
n'est pas une protection contre un attaquant déterminé — c'est une protection
contre une boucle client partie en vrille et contre un usage accidentellement
abusif. Une limite réellement partagée demande un magasin commun ; c'est écrit
au grand livre plutôt que sous-entendu ici.

Le quota et la limite de débit répondent à deux questions différentes. Le quota
dit « combien ce mois-ci », la limite dit « à quelle vitesse ». Un plan peut
avoir du quota et cogner la limite : c'est normal, et les deux messages le
disent différemment.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass

__all__ = ["RateLimit", "RateLimitVerdict", "RateLimiter"]


@dataclass(frozen=True, slots=True)
class RateLimit:
    """Un seuil : `max_calls` actes par `window_seconds`."""

    max_calls: int
    window_seconds: float
    label_fr: str

    def __post_init__(self) -> None:
        if self.max_calls < 1 or self.window_seconds <= 0:
            raise ValueError("A rate limit needs at least one call and a real window.")


@dataclass(frozen=True, slots=True)
class RateLimitVerdict:
    allowed: bool
    limit: RateLimit
    #: Secondes à attendre avant que la fenêtre libère une place. `0` si passant.
    retry_after_seconds: float

    @property
    def message_fr(self) -> str:
        seconds = max(1, round(self.retry_after_seconds))
        return (
            f"Trop de requêtes : {self.limit.label_fr} est limité à "
            f"{self.limit.max_calls} appels par "
            f"{round(self.limit.window_seconds)} secondes."
            f" Réessayez dans {seconds} seconde{'s' if seconds > 1 else ''}."
        )

    @property
    def remedy_fr(self) -> str:
        return (
            "Attendez quelques instants avant de relancer. Si cela se reproduit "
            "sans raison, signalez-le : c'est peut-être un écran qui boucle."
        )


class RateLimiter:
    """Fenêtre glissante par clé.

    Une fenêtre glissante plutôt qu'une fenêtre fixe : avec une fenêtre fixe, un
    client peut consommer le double du seuil à cheval sur la frontière, et
    l'exploitant constate un pic que la limite était censée empêcher.

    Les horodatages sont conservés dans une file bornée par le seuil lui-même :
    la mémoire consommée est celle des appels *autorisés*, pas celle des appels
    tentés — un client qui martèle ne fait donc pas grossir la structure.
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(
        self, key: str, limit: RateLimit, *, now: float | None = None
    ) -> RateLimitVerdict:
        moment = now if now is not None else time.monotonic()
        window = self._hits[key]
        horizon = moment - limit.window_seconds
        while window and window[0] <= horizon:
            window.popleft()

        if len(window) >= limit.max_calls:
            return RateLimitVerdict(
                allowed=False,
                limit=limit,
                retry_after_seconds=max(
                    0.0, window[0] + limit.window_seconds - moment
                ),
            )

        window.append(moment)
        return RateLimitVerdict(allowed=True, limit=limit, retry_after_seconds=0.0)

    def reset(self) -> None:
        """Vide les compteurs. Existe pour les tests, et uniquement pour eux."""
        self._hits.clear()


#: Seuil général : large, parce qu'un écran qui s'ouvre déclenche facilement une
#: dizaine d'appels. Il attrape la boucle folle, pas l'usage normal.
DEFAULT_LIMIT = RateLimit(
    max_calls=120, window_seconds=60.0, label_fr="l'accès général à l'API"
)

#: Seuil du copilote et de l'analyse. Beaucoup plus strict : chaque appel
#: déclenche plusieurs appels de modèle, dure des secondes et coûte de l'argent.
#: §9 le demande explicitement, et c'est le seul endroit où une requête peut
#: coûter davantage que la base de données.
AGENT_LIMIT = RateLimit(
    max_calls=10, window_seconds=60.0, label_fr="le copilote et l'analyse"
)

#: Seuil de l'inscription autonome. Très strict : chaque appel réussi crée une
#: organisation, c'est-à-dire une ligne que personne n'a demandée si l'appelant
#: est un script. Cinq par heure laisse largement passer une personne qui se
#: trompe et recommence.
SIGNUP_LIMIT = RateLimit(
    max_calls=5, window_seconds=3_600.0, label_fr="la création d'organisation"
)
