"""Authentification, autorisation, et le contexte qui porte le tenant.

`RequestContext` est le point important : il porte l'organisation et le rôle de
l'appelant, et il est **injecté** — jamais fourni par le client, jamais
paramètre d'un outil. Le modèle de langage ne peut pas franchir une frontière
d'organisation, même si le message le lui demande explicitement, parce qu'il
n'a aucun moyen d'exprimer la demande.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import bcrypt
from jose import JWTError, jwt

from app.core.errors import AuthenticationError, AuthorizationError
from app.domain.enums import UserRole

__all__ = ["RequestContext", "create_access_token", "decode_access_token", "hash_password",
           "verify_password"]

ALGORITHM = "HS256"
BCRYPT_ROUNDS = 12


def _prepare(plain: str) -> bytes:
    """Pré-condensé SHA-256 avant bcrypt.

    bcrypt ignore silencieusement tout ce qui dépasse 72 octets : deux mots de
    passe longs partageant leurs 72 premiers octets seraient équivalents. Le
    pré-condensé supprime cette limite ; l'encodage base64 évite les octets
    nuls, que bcrypt rejette.
    """
    return base64.b64encode(hashlib.sha256(plain.encode("utf-8")).digest())


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(_prepare(plain), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prepare(plain), hashed.encode("ascii"))
    except (ValueError, TypeError):
        # Empreinte corrompue ou format inattendu : on refuse, sans détailler.
        return False


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Identité effective d'un appel. Source unique de vérité pour l'autorisation."""

    user_id: UUID
    tenant_id: UUID
    role: UserRole
    email: str

    def require_role(self, *allowed: UserRole) -> None:
        if allowed and self.role not in allowed:
            names = ", ".join(sorted(r.label_fr for r in allowed))
            raise AuthorizationError(
                f"Action réservée aux rôles suivants : {names}.",
                remedy_fr="Demandez à un administrateur de votre organisation de "
                "modifier votre rôle.",
            )


def create_access_token(context: RequestContext, *, secret: str, ttl_minutes: int) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(context.user_id),
        "tid": str(context.tenant_id),
        "role": context.role.value,
        "email": context.email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl_minutes)).timestamp()),
    }
    token: str = jwt.encode(payload, secret, algorithm=ALGORITHM)
    return token


def decode_access_token(token: str, *, secret: str) -> RequestContext:
    """Reconstruit le contexte depuis un jeton, ou refuse.

    Toutes les causes d'échec — signature invalide, jeton expiré, revendication
    manquante, rôle inconnu — produisent le **même** message. Distinguer
    « signature invalide » de « organisation inconnue » renseignerait un
    attaquant sur ce qui existe.
    """
    try:
        claims = jwt.decode(token, secret, algorithms=[ALGORITHM])
        return RequestContext(
            user_id=UUID(claims["sub"]),
            tenant_id=UUID(claims["tid"]),
            role=UserRole(claims["role"]),
            email=str(claims["email"]),
        )
    except (JWTError, KeyError, ValueError) as exc:
        raise AuthenticationError(
            "Session invalide ou expirée.",
            remedy_fr="Reconnectez-vous.",
        ) from exc
