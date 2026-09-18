"""Règles d'architecture, vérifiées par le code plutôt que par la relecture.

Ce qui est testé ici n'échoue jamais en production : cela se dégrade lentement,
un import à la fois, jusqu'à ce que la couche métier ne soit plus lisible sans
lire du code web. C'est précisément le genre de règle qu'une revue laisse
passer.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parents[1] / "app"

#: Modules d'infrastructure interdits dans `domain/`.
#:
#: L'objectif n'est pas la pureté : c'est qu'un agronome puisse auditer un
#: calcul FAO-56 sans lire de code web, et qu'un test de domaine tourne en
#: microsecondes sans base de données.
FORBIDDEN_IN_DOMAIN = ("sqlalchemy", "fastapi", "httpx", "anthropic", "pydantic_settings")


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def test_domain_imports_no_infrastructure() -> None:
    offences: list[str] = []
    for path in sorted((APP / "domain").rglob("*.py")):
        for module in sorted(_imports(path) & set(FORBIDDEN_IN_DOMAIN)):
            offences.append(f"{path.relative_to(APP)} imports {module}")
    assert not offences, "domain/ must stay free of infrastructure:\n  " + "\n  ".join(offences)


def test_domain_imports_nothing_from_the_upper_layers() -> None:
    """`domain/` ne connaît ni l'API, ni les services, ni la persistance.

    La dépendance ne peut aller que dans un sens : `api → services → domain`.
    Un import remontant crée un cycle que Python tolère et qu'un humain ne suit
    plus.
    """
    offences: list[str] = []
    for path in sorted((APP / "domain").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for layer in ("app.api", "app.services", "app.repositories", "app.db", "app.adapters"):
            if f"from {layer}" in source or f"import {layer}" in source:
                offences.append(f"{path.relative_to(APP)} imports {layer}")
    assert not offences, "\n  ".join(offences)


#: Noms de construction de requête. Importer `AsyncSession` pour une annotation
#: de type est légitime ; construire un `select()` dans un routeur ne l'est pas.
#: C'est la distinction qui compte : la règle porte sur qui écrit du SQL, pas
#: sur qui nomme un type.
QUERY_BUILDERS = frozenset(
    {"select", "insert", "update", "delete", "text", "func", "join", "union"}
)


def _imported_names(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.split(".")[0] == "sqlalchemy"
        ):
            names.update(alias.name for alias in node.names)
    return names


def test_api_builds_no_queries_of_its_own() -> None:
    """La couche API passe par un service ou un dépôt ; elle n'écrit pas de SQL.

    Une requête construite dans un routeur échappe au dépôt, donc au filtre
    d'organisation. La RLS la rattraperait — mais l'objectif est qu'il n'y ait
    pas de chemin à rattraper, et une couche qui n'importe pas `select` ne peut
    pas en construire un par distraction.
    """
    offences: list[str] = []
    for path in sorted((APP / "api").rglob("*.py")):
        for name in sorted(_imported_names(path) & QUERY_BUILDERS):
            offences.append(f"{path.relative_to(APP)} imports sqlalchemy.{name}")
    assert not offences, "API modules must not build queries:\n  " + "\n  ".join(offences)


@pytest.mark.parametrize("layer", ["domain", "core"])
def test_lower_layers_do_not_import_fastapi(layer: str) -> None:
    offences = [
        str(path.relative_to(APP))
        for path in sorted((APP / layer).rglob("*.py"))
        if "fastapi" in _imports(path)
    ]
    assert not offences, f"{layer}/ must not import fastapi: {offences}"
