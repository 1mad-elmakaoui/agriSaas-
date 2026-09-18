# 0003 — Modèle tenant d'`atlasagri`, socle de persistance d'`agriflow`

**Date** : 2026-08-24 · **État** : Acceptée · **Précise** `00-prompt-unification.md` §11 phase 1

## Contexte

La §11 demande de « relever et durcir le cœur d'`atlasagri` ». Son modèle multi-tenant,
`RequestContext`, `TenantRepository` et le registre d'outils sont effectivement les meilleurs des
trois dépôts. Sa **couche de persistance** est la plus faible :

| | `agriflow` | `atlasagri` | `text_to_sql` |
|---|---|---|---|
| SQLAlchemy | async | **sync** | async |
| Migrations | **Alembic** | `Base.metadata.create_all()` | SQL de seed |
| RLS | aucune | aucune | aucune |

Un produit multi-tenant sans cadre de migration ne peut pas évoluer : la §5 exige un test de
migration énumérant les tables, et il n'y avait rien pour l'accrocher.

## Options

1. Lever `atlasagri` tel quel et ajouter Alembic ensuite. Reporte le portage async après
   l'écriture des phases 2 et 3, donc double le travail.
2. Modèle tenant d'`atlasagri`, persistance d'`agriflow`, dès la phase 1.

## Décision

Option 2. Session async + Alembic d'`agriflow` ; modèles, `RequestContext` et dépôt
d'`atlasagri` portés en async ; migration `0001_core` décrivant schémas, rôles, tables,
politiques et vues.

Le risque évité est silencieux : un `Session` synchrone conservé dans le registre d'outils, appelé
depuis une boucle d'agent async, ne casse rien de visible — il sérialise les requêtes.

## Conséquence

La frontière de la phase 1 ne bouge pas ; son contenu si. Une règle d'architecture testée
interdit `sqlalchemy.orm.Session` synchrone. `EnumValue`, décorateur de type, stocke la valeur
anglaise et rend le membre d'énumération : sans lui, `Mapped[SiteType]` sur une colonne `String`
est décoratif et le premier `.label_fr` échoue à l'affichage plutôt qu'à l'écriture.
