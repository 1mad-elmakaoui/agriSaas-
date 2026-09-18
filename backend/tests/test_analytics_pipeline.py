"""La voie analytique de bout en bout, contre un fournisseur scripté.

La même frontière de vérification que la boucle d'agent (décision 0010) : le
fournisseur rejoue une séquence décidée par le test. Ce que cela couvre est la
**mécanique** — validation, EXPLAIN, exécution, réparation, refus. Ce que cela ne
couvre pas est la qualité du SQL qu'un vrai modèle écrirait, et le registre
d'honnêteté le dit à sa place.

C'est aussi ce qui permet de scripter des requêtes qu'aucun modèle raisonnable
n'émettrait — une visée sur une table de base, un `DELETE` — et de vérifier ce
que la plateforme en fait.
"""

from __future__ import annotations

import pytest

from app.analytics.executor import ExecutionSettings
from app.analytics.service import AnalyticsService
from app.core.errors import FeatureDisabledError
from app.db.session import Databases
from app.llm.base import LLMResponse
from app.llm.fake import FakeProvider
from tests.conftest import Org

# `asyncio_mode = "auto"` : les tests asynchrones sont détectés sans marqueur.


def _sql(text: str) -> LLMResponse:
    return LLMResponse(
        text=text,
        tool_calls=(),
        stop_reason="end_turn",
        model="fake",
        input_tokens=0,
        output_tokens=0,
    )


def _service(databases: Databases, org: Org, script: list[LLMResponse], **kwargs):
    return AnalyticsService(
        provider=FakeProvider(script),
        scope=databases.analytics_for_tenant(org.tenant_id),
        execution=ExecutionSettings(max_rows=kwargs.pop("max_rows", 500)),
        **kwargs,
    )


async def test_a_valid_question_runs_the_whole_chain(
    databases: Databases, demo_org: Org
) -> None:
    """Génération, validation, EXPLAIN, exécution, résumé."""
    service = _service(
        databases,
        demo_org,
        [
            _sql("SELECT f.code, f.area_ha FROM analytics.v_fields f ORDER BY f.code"),
            _sql("Sept parcelles sont suivies, de 1,9 à 22,6 hectares."),
        ],
    )
    answer = await service.ask("Quelles sont mes parcelles et leur surface ?")

    assert answer.refused is False
    assert answer.sql and "v_fields" in answer.sql
    assert answer.result is not None and answer.result.row_count > 0
    assert answer.text_fr
    assert [a.status for a in answer.attempts] == ["ok"]
    # La LIMIT est injectée : aucune requête ne part sans borne.
    assert "LIMIT" in answer.sql.upper()


async def test_a_query_aimed_at_a_base_table_is_refused_not_repaired(
    databases: Databases, demo_org: Org
) -> None:
    """Un événement de sécurité, pas une faute de frappe.

    Le fournisseur a une seconde réponse en réserve : si la plateforme tentait
    une réparation, elle la consommerait. Le test vérifie qu'elle ne le fait pas.
    """
    provider_script = [
        _sql("SELECT s.reference FROM app.shipments s"),
        _sql("SELECT s.reference FROM analytics.v_shipments s"),
    ]
    service = _service(databases, demo_org, provider_script)
    answer = await service.ask("Montre-moi toutes les expéditions de la base")

    assert answer.refused is True
    assert answer.result is None
    assert [a.status for a in answer.attempts] == ["refused"]
    assert "surface analytique" in (answer.refusal_reason_fr or "")
    assert "Aucune donnée n'a été lue" in answer.text_fr


async def test_a_write_is_refused_at_the_first_attempt(
    databases: Databases, demo_org: Org
) -> None:
    service = _service(
        databases,
        demo_org,
        [_sql("DELETE FROM analytics.v_shipments"), _sql("SELECT 1")],
    )
    answer = await service.ask("Supprime les expéditions")
    assert answer.refused is True
    assert len(answer.attempts) == 1


async def test_an_invalid_query_is_repaired_with_the_exact_error(
    databases: Databases, demo_org: Org
) -> None:
    """La réparation reçoit le SQL **et** l'erreur, pas « ça n'a pas marché »."""
    provider = FakeProvider(
        [
            _sql("SELECT f.chiffre_affaires FROM analytics.v_fields f"),
            _sql("SELECT f.code FROM analytics.v_fields f"),
            _sql("Sept parcelles."),
        ]
    )
    service = AnalyticsService(
        provider=provider, scope=databases.analytics_for_tenant(demo_org.tenant_id)
    )
    answer = await service.ask("Quelles parcelles ?")

    assert [a.status for a in answer.attempts] == ["invalid", "ok"]
    assert answer.result is not None

    # La seconde invite porte la requête rejetée et l'erreur nommée.
    repair_prompt = provider.seen_messages[1][0]["content"]
    assert "chiffre_affaires" in repair_prompt
    assert "<requete_rejetee>" in repair_prompt


async def test_the_repair_loop_stops_when_the_model_repeats_itself(
    databases: Databases, demo_org: Org
) -> None:
    """La déduplication compare l'arbre, pas la chaîne.

    Réindenter et changer la casse d'un mot-clé produit une chaîne différente
    pour la même requête — et c'est exactement ce qu'un modèle fait quand on lui
    dit « essaie autrement » sans qu'il ait compris l'erreur.
    """
    service = _service(
        databases,
        demo_org,
        [
            _sql("SELECT f.inexistante FROM analytics.v_fields f"),
            _sql("select   f.inexistante\n  FROM analytics.v_fields   f"),
            _sql("SELECT f.code FROM analytics.v_fields f"),
        ],
    )
    answer = await service.ask("Quelque chose d'impossible")

    assert [a.status for a in answer.attempts] == ["invalid", "duplicate"]
    assert answer.result is None
    assert "n'a pas pu être traduite" in answer.text_fr


async def test_a_semantic_error_only_the_planner_sees_is_repaired(
    databases: Databases, demo_org: Org
) -> None:
    """La couche 4 attrape ce que la couche 1 ne peut pas.

    `status > 5` compare du texte à un entier : le catalogue connaît les types,
    mais c'est le vérificateur de PostgreSQL qui fait autorité.
    """
    service = _service(
        databases,
        demo_org,
        [
            _sql("SELECT s.reference FROM analytics.v_shipments s WHERE s.status > 5"),
            _sql(
                "SELECT s.reference FROM analytics.v_shipments s "
                "WHERE s.status = 'IN_TRANSIT'"
            ),
            _sql("Aucune expédition en transit."),
        ],
    )
    answer = await service.ask("Quelles expéditions sont en transit ?")

    assert [a.status for a in answer.attempts] == ["plan_error", "ok"]
    assert answer.result is not None


async def test_the_summariser_sees_the_rows_inside_an_untrusted_envelope(
    databases: Databases, demo_org: Org
) -> None:
    """Les lignes sont une donnée, et le SQL aussi.

    Le SQL est le canal le plus subtil des deux : le générateur y recopie les
    mots de la question comme littéraux, si bien qu'une consigne tapée dans le
    champ de saisie arrive au résumeur à l'intérieur d'une requête.
    """
    provider = FakeProvider(
        [
            _sql("SELECT f.code FROM analytics.v_fields f"),
            _sql("Sept parcelles."),
        ]
    )
    service = AnalyticsService(
        provider=provider, scope=databases.analytics_for_tenant(demo_org.tenant_id)
    )
    await service.ask("Combien de parcelles ?")

    summary_prompt = provider.seen_messages[1][0]["content"]
    assert "<donnees_externes" in summary_prompt
    assert "</donnees_externes>" in summary_prompt
    assert "<requete>" in summary_prompt
    # La langue est fixée hors de toute région encadrée.
    assert summary_prompt.startswith("Réponds en :")
    assert summary_prompt.index("Réponds en :") < summary_prompt.index("<question>")


async def test_a_row_value_cannot_change_the_reply_language(
    databases: Databases, demo_org: Org
) -> None:
    """Une valeur de ligne disant « réponds en anglais » est une donnée.

    Le test ne porte pas sur ce que le modèle ferait — il porte sur la structure
    de l'invite, qui est ce que nous contrôlons : la consigne de langue est
    au-dessus, hors des délimiteurs, et le système dit explicitement d'ignorer
    toute consigne de langue venue de l'intérieur.
    """
    provider = FakeProvider(
        [
            _sql("SELECT f.code, f.name_fr FROM analytics.v_fields f"),
            _sql("Sept parcelles."),
        ]
    )
    service = AnalyticsService(
        provider=provider, scope=databases.analytics_for_tenant(demo_org.tenant_id)
    )
    await service.ask("Combien de parcelles ?")

    system = provider.seen_systems[1]
    assert "jamais une instruction" in system
    assert "Tes instructions viennent" in system

    summary_prompt = provider.seen_messages[1][0]["content"]
    language_line = summary_prompt.splitlines()[0]
    assert language_line == "Réponds en : français"


async def test_the_catalog_offered_to_the_model_contains_no_base_table(
    databases: Databases, demo_org: Org
) -> None:
    """Le modèle n'apprend pas l'existence des tables de base.

    Filtré à la déclaration, pas au refus : il ne les tente donc pas, et n'a pas
    à interpréter un rejet.
    """
    provider = FakeProvider([_sql("SELECT f.code FROM analytics.v_fields f"), _sql("Sept.")])
    service = AnalyticsService(
        provider=provider, scope=databases.analytics_for_tenant(demo_org.tenant_id)
    )
    await service.ask("Combien de parcelles ?")

    schema = provider.seen_systems[0]
    assert "analytics.v_fields" in schema
    assert "app.fields" not in schema
    assert "app.tenants" not in schema
    # Et le glossaire est là : sans lui, « annulé » devient un filtre vide.
    assert "'CANCELLED'" in schema


async def test_the_result_is_scoped_to_the_calling_organisation(
    databases: Databases, demo_org: Org, two_orgs: tuple[Org, Org]
) -> None:
    """Le même SQL, deux organisations, deux résultats.

    Rien dans la requête ne nomme d'organisation — il n'existe aucune colonne
    pour cela. C'est la couche 2 qui répond différemment.
    """
    alpha, _ = two_orgs
    sql = "SELECT f.code FROM analytics.v_fields f"

    mine = await _service(databases, demo_org, [_sql(sql), _sql("Sept.")]).ask("?")
    theirs = await _service(databases, alpha, [_sql(sql), _sql("Aucune.")]).ask("?")

    assert mine.result is not None and mine.result.row_count > 0
    assert theirs.result is not None and theirs.result.row_count == 0


async def test_without_a_key_the_analysis_declines_instead_of_pretending(
    databases: Databases, demo_org: Org
) -> None:
    service = AnalyticsService(
        provider=None, scope=databases.analytics_for_tenant(demo_org.tenant_id)
    )
    with pytest.raises(FeatureDisabledError) as excinfo:
        await service.ask("Combien de parcelles ?")
    assert "aucune clé" in str(excinfo.value).lower()


async def test_a_fenced_reply_is_normalised_rather_than_rejected(
    databases: Databases, demo_org: Org
) -> None:
    """Refuser pour un délimiteur dépenserait une réparation sur une requête juste."""
    service = _service(
        databases,
        demo_org,
        [
            _sql("```sql\nSELECT f.code FROM analytics.v_fields f;\n```"),
            _sql("Sept parcelles."),
        ],
    )
    answer = await service.ask("Combien de parcelles ?")
    assert [a.status for a in answer.attempts] == ["ok"]
    assert answer.sql and answer.sql.startswith("SELECT")


async def test_a_truncated_result_is_declared_to_the_summariser(
    databases: Databases, demo_org: Org
) -> None:
    """Décrire un résultat tronqué comme complet est une faute de correction."""
    provider = FakeProvider(
        [_sql("SELECT f.code FROM analytics.v_fields f"), _sql("Deux parcelles.")]
    )
    service = AnalyticsService(
        provider=provider,
        scope=databases.analytics_for_tenant(demo_org.tenant_id),
        execution=ExecutionSettings(max_rows=2),
    )
    answer = await service.ask("Quelles parcelles ?")

    assert answer.result is not None and answer.result.truncated is True
    summary_prompt = provider.seen_messages[1][0]["content"]
    assert "TRONQUÉ" in summary_prompt


async def test_a_filled_injected_limit_is_reported_as_possibly_incomplete(
    databases: Databases, demo_org: Org
) -> None:
    """« Exactement N lignes » est indiscernable de « il y en avait davantage ».

    Le plafond est poussé dans la requête — la base n'envoie pas des lignes
    qu'on jetterait — donc l'exécuteur ne voit jamais son propre plafond et ne
    signale rien. Sans cette déclaration, le résumé décrirait deux lignes comme
    s'il n'y en avait que deux, alors qu'il y en a sept.
    """
    provider = FakeProvider(
        [_sql("SELECT f.code FROM analytics.v_fields f"), _sql("Deux parcelles.")]
    )
    service = AnalyticsService(
        provider=provider,
        scope=databases.analytics_for_tenant(demo_org.tenant_id),
        execution=ExecutionSettings(max_rows=2),
    )
    answer = await service.ask("Quelles parcelles ?")

    assert answer.result is not None
    assert answer.result.row_count == 2
    assert answer.result.truncated is True
    assert "peut en exister davantage" in (answer.result.truncation_reason or "")

    summary_prompt = provider.seen_messages[1][0]["content"]
    assert "TRONQUÉ" in summary_prompt


async def test_a_short_result_under_the_limit_is_not_called_truncated(
    databases: Databases, demo_org: Org
) -> None:
    """L'inverse compte autant : annoncer une troncature qui n'a pas eu lieu
    apprendrait à ignorer l'avertissement."""
    service = _service(
        databases,
        demo_org,
        [_sql("SELECT f.code FROM analytics.v_fields f"), _sql("Sept parcelles.")],
    )
    answer = await service.ask("Quelles parcelles ?")
    assert answer.result is not None
    assert answer.result.row_count == 7
    assert answer.result.truncated is False
