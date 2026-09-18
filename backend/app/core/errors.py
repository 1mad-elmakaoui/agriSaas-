"""Erreurs applicatives et leur forme de sortie.

Une erreur qui atteint l'utilisateur porte trois choses : un **code stable**
(anglais, pour le client et les journaux), un **message français** utilisable
tel quel à l'écran, et le cas échéant **ce qu'il faut faire**. Une trace
technique n'atteint jamais l'interface.

Le message français est écrit ici, pas traduit plus tard par un appel de
modèle : traduire une erreur avec le service qui vient d'échouer est le moyen
le plus sûr de n'avoir aucun message du tout.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "AtlasError",
    "AuthenticationError",
    "AuthorizationError",
    "ConfigurationError",
    "FeatureDisabledError",
    "NotFoundError",
    "ProviderUnavailableError",
    "QuotaExceededError",
    "RateLimitedError",
    "TenantIsolationError",
    "ValidationError",
]


class AtlasError(Exception):
    """Erreur métier. Toujours convertible en réponse HTTP propre."""

    code = "internal_error"
    http_status = 500

    def __init__(self, message_fr: str, *, remedy_fr: str | None = None) -> None:
        super().__init__(message_fr)
        self.message_fr = message_fr
        self.remedy_fr = remedy_fr

    def payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message_fr": self.message_fr}
        if self.remedy_fr:
            body["remedy_fr"] = self.remedy_fr
        return body


class ValidationError(AtlasError):
    code = "invalid_input"
    http_status = 422


class NotFoundError(AtlasError):
    code = "not_found"
    http_status = 404


class AuthenticationError(AtlasError):
    code = "unauthenticated"
    http_status = 401


class AuthorizationError(AtlasError):
    code = "forbidden"
    http_status = 403


class TenantIsolationError(AtlasError):
    """Tentative d'accès à une ressource d'une autre organisation.

    Rendue en 404, jamais en 403 : confirmer l'existence d'une ressource
    appartenant à un autre client est déjà une divulgation. Le code interne
    reste distinct pour que le journal d'audit puisse compter les tentatives.
    """

    code = "not_found"
    http_status = 404

    def __init__(self) -> None:
        super().__init__("Ressource introuvable.")


class FeatureDisabledError(AtlasError):
    code = "feature_disabled"
    http_status = 503


class ProviderUnavailableError(AtlasError):
    code = "provider_unavailable"
    http_status = 503


class QuotaExceededError(AtlasError):
    code = "quota_exceeded"
    http_status = 429


class RateLimitedError(AtlasError):
    """Trop de requêtes, trop vite.

    Distincte de :class:`QuotaExceededError` bien que toutes deux rendent 429 :
    le quota dit « combien ce mois-ci », la limite de débit dit « à quelle
    vitesse ». Les confondre ferait croire à un exploitant qu'il a épuisé son
    plan alors qu'il lui suffisait d'attendre dix secondes — et le code distinct
    permet au journal de compter les deux séparément.
    """

    code = "rate_limited"
    http_status = 429

    def __init__(
        self, message_fr: str, *, remedy_fr: str | None = None, retry_after: int = 1
    ) -> None:
        super().__init__(message_fr, remedy_fr=remedy_fr)
        #: Secondes à attendre, rendues aussi en en-tête `Retry-After` : un
        #: client correct saura ralentir sans qu'un humain lise le message.
        self.retry_after = retry_after


class ConfigurationError(RuntimeError):
    """Configuration refusée au démarrage.

    Volontairement **pas** une :class:`AtlasError` : elle ne devient jamais une
    réponse HTTP, parce que le processus ne doit pas avoir démarré. En anglais,
    comme tout ce que seul un opérateur lit.
    """
