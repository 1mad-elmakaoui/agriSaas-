# 0004 — Trois vocabulaires de provenance, et la suppression de « valeur par défaut »

**Date** : 2026-08-24 · **État** : Acceptée · **Étend** `00-prompt-unification.md` §4.1

## Contexte

La §4.1 décrit deux axes et donne une table de correspondance couvrant trois des huit valeurs de
`agriflow.DataSource`. Les cinq absentes ne sont pas des détails, et un troisième vocabulaire
existe déjà dans `atlasagri` sans être mentionné.

## Décision

**Deux énumérations fermées, plus une référence ouverte.**

* `DataState` — statut épistémique : ce que la valeur *est*.
* `DataOrigin` — catégorie de provenance : d'où elle vient. Porte le poids de fiabilité.
* `DataSourceRef` — source **nommée** (« Open-Meteo, archive ERA5-Land »), ouverte, destinée au
  panneau de preuves. Deux services externes partagent `EXTERNAL_API` et ne partagent pas leur
  référence : aplatir l'un dans l'autre perdrait l'une des deux questions.

**`WEATHER_API` ne se traduit pas par une paire fixe.** L'état dépend de la ligne — une
observation est `OBSERVED`, une prévision est `FORECAST` — donc c'est le service qui décide, pas
une table de correspondance.

**`CALCULATED` et `ESTIMATED` ne se confondent pas.** Une ET0 calculée est `(DERIVED, MODEL)` ;
un Kc estimé depuis la date de plantation est `(INFERRED, MODEL)`. Les écraser sur la même paire
effacerait la distinction que l'interface doit montrer.

**`DataSource.DEFAULT` est supprimé, pas traduit.** Il pesait 0,4 dans le score de qualité
d'`agriflow` et servait de repli. C'est littéralement une valeur plausible substituée à une
valeur manquante — la règle « ce qui manque manque » contournée, dans le dépôt que la
spécification cite en exemple sur ce point. Une entrée absente rend la sortie dépendante
indisponible.

## Conséquence

Les poids de fiabilité sont strictement décroissants, et un test le vérifie : deux origines de
même poids rendraient le score aveugle à une dégradation. `reliability_score([])` renvoie `None`
et non `0.0` — aucune entrée est une fiabilité *inconnue*, pas une fiabilité nulle, et la
distinction décide de ce que l'interface affiche.

Les colonnes de provenance sont `NOT NULL` **sans valeur par défaut** : une insertion oublieuse
échoue au lieu de produire une ligne « observée » plausible.
