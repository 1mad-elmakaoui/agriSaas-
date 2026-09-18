"""Exécute les corpus et imprime le rapport.

    python -m app.analytics.evals.run

Aucun modèle n'est appelé : les deux mesures produites ici sont celles qui
tiennent hors ligne. La mesure de génération multilingue demande une clé, et le
harnais la refuse plutôt que d'imprimer un chiffre approchant.
"""

from __future__ import annotations

import asyncio
import uuid

from app.analytics.evals.harness import run_corpus
from app.analytics.evals.multilingual import check_references
from app.analytics.introspection import read_catalog
from app.core.config import get_settings
from app.db.session import Databases


async def main() -> int:
    databases = Databases(get_settings())
    try:
        # Une organisation quelconque : le catalogue décrit la **forme** des
        # vues, identique pour toutes. Aucune ligne n'est lue.
        scope = databases.analytics_for_tenant(uuid.uuid4())
        async with scope.connection() as conn:
            catalog = await read_catalog(conn)
    finally:
        await databases.dispose()

    print("— Corpus adverse —")  # noqa: T201 - sortie de commande
    corpus = run_corpus(catalog)
    for line in corpus.summary_lines_fr():
        print(line)  # noqa: T201

    print()  # noqa: T201
    print("— Suite multilingue (corpus seul) —")  # noqa: T201
    references = check_references(catalog)
    for line in references.summary_lines_fr():
        print(line)  # noqa: T201

    return 0 if not corpus.failures and not references.failures else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
