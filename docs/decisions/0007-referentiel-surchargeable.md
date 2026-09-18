# 0007 — Deux politiques d'isolation : métier stricte, référentiel surchargeable

**Date** : 2026-08-25 · **État** : Acceptée · **Étend** la décision 0001

## Contexte

Le référentiel agronomique — Kc, longueurs de stades, enracinement, `p`, propriétés hydriques
des sols, efficience d'application — est **global** : il vient de la FAO et vaut pour toutes les
organisations. Mais la §5 exige aussi qu'une organisation puisse le surcharger : « une ligne
`soil_profiles` avec `is_measured = true` et un `tenant_id` masque la ligne de référence ».

La politique posée en phase 1 est `tenant_id = organisation courante`. Appliquée telle quelle
aux tables de référentiel, elle rend le référentiel **invisible** : une ligne globale porte
`tenant_id IS NULL`, donc elle ne satisfait jamais l'égalité. Une organisation neuve n'aurait
aucune agronomie.

## Options

1. **Dupliquer le référentiel par organisation à la création du tenant.** Dix cultures × N
   clients, et une correction FAO à propager à la main sur toutes les copies. La question
   « laquelle fait foi » se pose à chaque incident.
2. **Retirer la RLS des tables de référentiel.** La lecture marcherait ; l'écriture aussi, pour
   tout le monde. Une organisation pourrait modifier un Kc pour tous les clients.
3. **Une seconde politique, pour cette famille de tables.**

## Décision

Option 3. Deux familles, deux politiques :

```sql
-- tables métier
USING      (tenant_id = organisation courante)
WITH CHECK (tenant_id = organisation courante)

-- tables de référentiel
USING      (tenant_id IS NULL OR tenant_id = organisation courante)
WITH CHECK (tenant_id = organisation courante)
```

Lecture : le référentiel FAO **plus** ses propres mesures. Écriture : les siennes seulement.
L'asymétrie entre `USING` et `WITH CHECK` est le mécanisme entier — et elle est bruyante :
un `UPDATE` visant la ligne globale la *trouve* (elle est lisible) puis **lève**, plutôt que de
ne toucher aucune ligne. Un `rowcount = 0` silencieux laisserait croire à un no-op anodin.

Reste un problème que la politique crée : plus personne ne peut **alimenter** le référentiel,
puisqu'une ligne globale n'a pas d'organisation. Trois issues existaient. Un rôle `BYPASSRLS`
rendrait décoratives toutes les politiques du schéma. Un `SET row_security = off` échoue
précisément parce que `FORCE` s'applique aussi au propriétaire. Retenue : **déclarer**
l'exception dans le catalogue, pour le seul rôle d'administration —

```sql
CREATE POLICY reference_admin ON app.crops TO atlas_owner USING (true) WITH CHECK (true);
```

`atlas_owner` n'a pas de droit de connexion : on ne l'atteint que par `SET ROLE`, depuis une
session d'administration. Le pouvoir existait déjà — il possède les tables, donc il pourrait
altérer les politiques — mais il est désormais visible dans `pg_policy` plutôt que caché dans un
attribut de rôle.

## Conséquence

Une troisième famille apparaît de fait : `user_directory`, sans RLS (décision 0006). Un test de
**partition** vérifie que chaque table portant `tenant_id` appartient à exactement une famille.
Une table qui n'appartiendrait à aucune ne recevrait la politique de personne ; une table dans
deux recevrait la mauvaise. Les deux échouent.

La calibration locale reste une tâche de données. Le moteur lit la base ; remplacer une valeur
de catalogue par une analyse de laboratoire ne demande aucune modification de code — ce qui est
l'objectif, puisque cette calibration est le premier travail qu'un agronome marocain voudra
faire.
