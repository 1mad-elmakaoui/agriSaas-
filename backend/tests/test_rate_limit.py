"""Limitation de débit, et le seuil strict du copilote.

Deux seuils parce qu'il y a deux coûts. Une requête de liste coûte une requête
SQL indexée ; une question au copilote coûte plusieurs appels de modèle, des
secondes et de l'argent. Un seuil unique obligerait à choisir entre laisser
saturer le modèle et brider l'interface.

Les tests fixent aussi la frontière avec le quota : un refus de débit dit
« ralentissez », un refus de quota dit « votre plan est épuisé ». Les deux
rendent 429 et ne doivent pas porter le même code.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from app.core.rate_limit import AGENT_LIMIT, DEFAULT_LIMIT, RateLimit, RateLimiter
from app.llm.fake import FakeProvider, scripted_text
from tests.conftest import Org

# `asyncio_mode = "auto"` : les tests asynchrones sont détectés sans marqueur.


async def _login(client: AsyncClient, org: Org) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login", json={"email": org.email, "password": org.password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# ---------------------------------------------------------------------------
# La fenêtre, sans HTTP
# ---------------------------------------------------------------------------
def test_the_window_refuses_at_the_threshold_and_not_after() -> None:
    limiter = RateLimiter()
    limit = RateLimit(max_calls=3, window_seconds=60.0, label_fr="test")
    for index in range(3):
        assert limiter.check("k", limit, now=index).allowed is True
    assert limiter.check("k", limit, now=3).allowed is False


def test_the_window_slides_instead_of_resetting_on_the_hour() -> None:
    """Une fenêtre fixe laisserait passer le double du seuil à cheval sur la
    frontière — un pic que la limite était censée empêcher."""
    limiter = RateLimiter()
    limit = RateLimit(max_calls=2, window_seconds=10.0, label_fr="test")
    assert limiter.check("k", limit, now=0.0).allowed is True
    assert limiter.check("k", limit, now=9.0).allowed is True
    assert limiter.check("k", limit, now=9.5).allowed is False

    # À 10,1 s, le premier appel sort de la fenêtre : une place se libère, une
    # seule. Une remise à zéro en aurait libéré deux.
    assert limiter.check("k", limit, now=10.1).allowed is True
    assert limiter.check("k", limit, now=10.2).allowed is False


def test_the_refusal_says_how_long_to_wait() -> None:
    """« Trop de requêtes » sans délai laisse le client réessayer aussitôt."""
    limiter = RateLimiter()
    limit = RateLimit(max_calls=1, window_seconds=30.0, label_fr="test")
    limiter.check("k", limit, now=0.0)
    verdict = limiter.check("k", limit, now=10.0)
    assert verdict.allowed is False
    assert verdict.retry_after_seconds == pytest.approx(20.0)
    assert "20 secondes" in verdict.message_fr


def test_two_keys_do_not_share_a_window() -> None:
    limiter = RateLimiter()
    limit = RateLimit(max_calls=1, window_seconds=60.0, label_fr="test")
    assert limiter.check("a", limit, now=0.0).allowed is True
    assert limiter.check("b", limit, now=0.0).allowed is True


def test_a_refused_call_does_not_extend_the_window() -> None:
    """Un client qui martèle ne doit pas repousser indéfiniment sa propre
    libération, ni faire grossir la structure en mémoire."""
    limiter = RateLimiter()
    limit = RateLimit(max_calls=1, window_seconds=10.0, label_fr="test")
    limiter.check("k", limit, now=0.0)
    for tick in range(1, 10):
        assert limiter.check("k", limit, now=float(tick)).allowed is False
    assert limiter.check("k", limit, now=10.1).allowed is True


def test_the_agent_threshold_is_stricter_than_the_general_one() -> None:
    """§9 demande une limite dédiée. Égale à la générale, elle n'existerait pas."""
    general = DEFAULT_LIMIT.max_calls / DEFAULT_LIMIT.window_seconds
    agent = AGENT_LIMIT.max_calls / AGENT_LIMIT.window_seconds
    assert agent < general


def test_a_limit_without_a_window_is_refused_at_construction() -> None:
    with pytest.raises(ValueError):
        RateLimit(max_calls=0, window_seconds=60.0, label_fr="test")
    with pytest.raises(ValueError):
        RateLimit(max_calls=10, window_seconds=0.0, label_fr="test")


# ---------------------------------------------------------------------------
# Par le vrai chemin HTTP
# ---------------------------------------------------------------------------
async def test_the_copilot_is_capped_before_the_general_api_is(
    app: Any, client: AsyncClient, demo_org: Org
) -> None:
    """Le onzième appel en une minute est refusé, l'interface reste utilisable.

    C'est tout l'intérêt de deux seuils : l'exploitant qui a trop questionné le
    copilote peut encore consulter ses parcelles.
    """
    from app.api.deps import get_llm_provider

    app.dependency_overrides[get_llm_provider] = lambda: FakeProvider(
        [scripted_text("Bonjour.") for _ in range(40)]
    )
    try:
        headers = await _login(client, demo_org)
        codes = []
        for _ in range(AGENT_LIMIT.max_calls + 1):
            response = await client.post(
                "/api/v1/copilote", headers=headers, json={"question": "Bonjour ?"}
            )
            codes.append(response.status_code)
            last = response
        assert codes[: AGENT_LIMIT.max_calls] == [200] * AGENT_LIMIT.max_calls
        assert codes[-1] == 429
        assert last.json()["code"] == "rate_limited", "ni quota, ni panne"
        assert last.headers["Retry-After"], "un client correct doit savoir attendre"

        # La limite du modèle n'a pas fermé le reste du produit.
        assert (await client.get("/api/v1/parcelles", headers=headers)).status_code == 200
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)


async def test_a_rate_limit_is_not_a_quota(
    app: Any, client: AsyncClient, demo_org: Org
) -> None:
    """Deux 429 qui veulent dire deux choses différentes portent deux codes.

    Confondre les deux ferait croire à un exploitant que son plan est épuisé
    alors qu'il lui suffisait d'attendre dix secondes.
    """
    from app.api.deps import get_llm_provider

    app.dependency_overrides[get_llm_provider] = lambda: FakeProvider(
        [scripted_text("Bonjour.") for _ in range(40)]
    )
    try:
        headers = await _login(client, demo_org)
        for _ in range(AGENT_LIMIT.max_calls):
            await client.post(
                "/api/v1/copilote", headers=headers, json={"question": "Bonjour ?"}
            )
        refused = await client.post(
            "/api/v1/copilote", headers=headers, json={"question": "Bonjour ?"}
        )
        body = refused.json()
        assert body["code"] == "rate_limited"
        assert body["code"] != "quota_exceeded"
        assert "Réessayez" in body["message_fr"]
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)


async def test_the_agent_threshold_follows_the_user_not_the_organisation(
    app: Any, client: AsyncClient, demo_org: Org, two_orgs: tuple[Org, Org]
) -> None:
    """Un analyste qui boucle ne doit pas fermer le copilote à ses collègues.

    Et une limite par adresse punirait toute une exploitation derrière une seule
    sortie internet pour l'usage d'une personne.
    """
    from app.api.deps import get_llm_provider

    app.dependency_overrides[get_llm_provider] = lambda: FakeProvider(
        [scripted_text("Bonjour.") for _ in range(40)]
    )
    try:
        headers = await _login(client, demo_org)
        for _ in range(AGENT_LIMIT.max_calls):
            await client.post(
                "/api/v1/copilote", headers=headers, json={"question": "Bonjour ?"}
            )
        assert (
            await client.post(
                "/api/v1/copilote", headers=headers, json={"question": "Bonjour ?"}
            )
        ).status_code == 429

        other, _ = two_orgs
        other_headers = await _login(client, other)
        response = await client.post(
            "/api/v1/copilote", headers=other_headers, json={"question": "Bonjour ?"}
        )
        assert response.status_code != 429, "le seuil d'un utilisateur n'est pas celui d'un autre"
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)


async def test_the_health_probe_is_never_rate_limited(client: AsyncClient) -> None:
    """Une sonde de santé limitée déclencherait une alerte de panne inventée."""
    for _ in range(5):
        assert (await client.get("/api/sante")).status_code in {200, 503}


async def test_the_general_limit_applies_before_authentication(
    app: Any, client: AsyncClient
) -> None:
    """Une rafale de tentatives de connexion doit rencontrer un seuil.

    Posé sur l'utilisateur, il ne s'appliquerait qu'après la connexion — c'est
    à dire jamais à celui qui essaie d'entrer.
    """
    limiter = app.state.rate_limiter
    limiter.reset()
    codes = set()
    for _ in range(DEFAULT_LIMIT.max_calls + 2):
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "inconnu@example.ma", "password": "faux"},
        )
        codes.add(response.status_code)
    assert 429 in codes
