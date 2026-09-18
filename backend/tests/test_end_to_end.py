"""Parcours complet, à travers HTTP.

Les autres suites vérifient qu'une frontière tient. Celle-ci vérifie que le
produit fonctionne : se connecter, créer un site, le relire — et constater que
chaque valeur arrive à l'appelant avec sa provenance.
"""

from __future__ import annotations

from tests.conftest import Org

# `asyncio_mode = "auto"` : les tests asynchrones sont détectés, et un marqueur
# explicite ferait avertir pytest sur les tests synchrones du même module.


async def test_login_then_create_then_read(client, two_orgs: tuple[Org, Org]) -> None:
    alpha, _ = two_orgs

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": alpha.email, "password": alpha.password},
    )
    assert login.status_code == 200, login.text
    body = login.json()
    assert body["role"] == "ADMIN"
    assert body["role_label_fr"] == "Administrateur"
    headers = {"Authorization": f"Bearer {body['access_token']}"}

    created = await client.post(
        "/api/v1/sites",
        headers=headers,
        json={
            "code": "P03",
            "name_fr": "Parcelle P03",
            "site_type": "FARM",
            "region_code": "SOUSS_MASSA",
            "latitude": 30.42,
            "longitude": -9.60,
            "capacity_tonnes": 120.0,
        },
    )
    assert created.status_code == 201, created.text
    site = created.json()

    # Le libellé français accompagne la valeur stockée anglaise : c'est la règle
    # de langue, et c'est ce qui permet à l'interface de ne pas porter sa propre
    # table de traduction.
    assert site["site_type"] == "FARM"
    assert site["site_type_label_fr"] == "Exploitation"

    # La provenance voyage jusqu'à l'appelant. Sans elle, une fiche saisie par
    # un exploitant et une fiche de démonstration seraient indiscernables.
    provenance = site["provenance"]
    assert provenance["origin"] == "MANUAL_ENTRY"
    assert provenance["origin_label_fr"] == "Saisie manuelle"
    assert provenance["state_label_fr"] == "Observé"
    assert site["reliability_label_fr"] == "Élevée"

    listed = await client.get("/api/v1/sites", headers=headers)
    assert listed.status_code == 200
    assert [s["code"] for s in listed.json()] == ["P03"]


async def test_a_wrong_password_says_nothing_useful(
    client, two_orgs: tuple[Org, Org]
) -> None:
    """Le message d'échec ne distingue pas les causes.

    Courriel inconnu et mot de passe faux doivent produire exactement le même
    texte : sinon, l'API énumère les comptes existants.
    """
    alpha, _ = two_orgs
    wrong_password = await client.post(
        "/api/v1/auth/login",
        json={"email": alpha.email, "password": "not-the-password"},
    )
    unknown_email = await client.post(
        "/api/v1/auth/login",
        json={"email": "personne@example.ma", "password": "not-the-password"},
    )
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()
    assert wrong_password.json()["message_fr"] == "Courriel ou mot de passe incorrect."


async def test_health_reports_every_probe(client) -> None:
    """L'état du service énumère les sondes, il ne résume pas.

    « ok » sans détail obligerait à lire les journaux pour savoir *ce qui* est
    vérifié ; l'exploitant doit pouvoir constater que l'isolation a été
    contrôlée à ce démarrage-là.
    """
    response = await client.get("/api/sante")
    assert response.status_code == 200
    body = response.json()
    assert body["statut"] == "ok"
    names = {p["nom"] for p in body["sondes"]}
    assert {"rls_coverage", "cross_tenant", "analytics_confinement"} <= names
