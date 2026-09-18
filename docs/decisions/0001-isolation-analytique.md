# 0001 — Placer les politiques sur les tables de base, pas sur le schéma analytique

**Date** : 2026-08-24 · **État** : Acceptée

## Contexte

`docs/00-prompt-unification.md` §4.3 demande « `FORCE ROW LEVEL SECURITY` sur chaque table de
`analytics` », tandis que la §3 impose que `analytics` ne contienne « que des vues, aucune table
de base ». Les deux ne peuvent pas être vraies : PostgreSQL refuse toute politique RLS sur une
vue.

```
ALTER VIEW analytics.v_sites ENABLE ROW LEVEL SECURITY;
ERROR:  ALTER action ENABLE ROW SECURITY cannot be performed on relation "v_sites"
DETAIL:  This operation is not supported for views.
```

Le point est structurant : c'est la couche censée tenir quand notre propre code est le bug.

## Options

1. **Tables réelles dans `analytics`, dupliquées depuis `app`.** Deux copies de chaque donnée,
   une synchronisation à écrire, et la question « laquelle fait foi » posée à chaque incident.
2. **Vues `security_invoker`.** Élégant sur le papier. Mesuré : exige que le rôle analytique
   détienne `SELECT` sur les tables de base, donc qu'il puisse les interroger directement. Le
   confinement retombe alors entièrement sur le validateur AST — c'est-à-dire sur notre code,
   exactement ce que la conception veut éviter.
3. **Politiques sur `app`, vues `analytics` détenues par un rôle distinct.** Une seule copie des
   données, une seule définition de politique, et un rôle analytique sans aucun privilège sur
   `app`.

## Décision

Option 3.

* Les tables de base vivent dans `app`, appartiennent à `atlas_owner` (non superutilisateur,
  `NOBYPASSRLS`), et portent `ENABLE` **et** `FORCE ROW LEVEL SECURITY` avec une politique
  `USING` et `WITH CHECK` sur l'organisation courante.
* `analytics` ne contient que des vues, créées par `atlas_analytics_owner`, **sans**
  `security_invoker`.
* `atlas_analytics_ro` détient `SELECT` sur ces vues et rien d'autre — pas même `USAGE` sur le
  schéma `app`.
* Le paramètre `app.current_tenant` est lu **sans** `missing_ok`.

`FORCE` n'est pas une précaution. Mesuré sur PostgreSQL 16 : une vue détenue par le propriétaire
de la table, au-dessus d'une table seulement `ENABLE`, renvoie **toutes** les organisations —
politique présente, `\d` rassurant, aucune erreur.

Le refus de `missing_ok` est également délibéré, et contre-intuitif : sans GUC, la lecture doit
**échouer**. Avec `missing_ok`, la politique serait fausse partout et l'appelant recevrait un jeu
vide, que l'agent d'analyse raconterait comme « vous n'avez aucune expédition ce mois-ci ». Une
panne muette est ici pire qu'une panne bruyante.

Enfin, le tenant est lié à l'**obtention d'une connexion** (`AnalyticsScope`, `TenantSession`) et
non passé en argument : l'exécuteur analytique ouvre deux transactions distinctes — une pour
`EXPLAIN`, une pour la requête — et un `SET LOCAL` posé au seul endroit évident casse la moitié
du chemin.

## Conséquence

Ce que cela rend possible : un contournement du validateur AST échoue au privilège, pas au
validateur. Neuf sondes de démarrage vérifient la disposition, et six tests la cassent
délibérément pour prouver que les sondes savent dire non.

Ce que cela ferme : aucune vue matérialisée dans `analytics` — PostgreSQL n'y attache aucune
politique, une matview y serait une copie non filtrée de toutes les organisations. Une sonde
l'interdit.

À reconsidérer : si une charge analytique impose un jour la matérialisation, la seule voie sûre
est une table par organisation ou une matview dans un schéma inaccessible au rôle analytique,
exposée par une vue. Ne pas improviser ce jour-là.
