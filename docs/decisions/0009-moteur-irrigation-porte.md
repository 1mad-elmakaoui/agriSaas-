# 0009 — Porter le moteur d'irrigation, et unifier son vocabulaire au passage

**Date** : 2026-08-25 · **État** : Acceptée · **Applique** la décision 0008 au code

## Contexte

Le moteur FAO-56 d'`agriflow` est le code le mieux validé des trois dépôts : sept exemples
publiés de la FAO-56 reproduits à l'arrondi près (exemples 2, 3, 5, 8, 14, 18 et 20). La phase 3
devait le faire entrer dans le produit fusionné.

Le réécrire depuis les équations aurait été la même faute que retranscrire un tableau de Kc :
un signe inversé dans le rayonnement net produit une ET0 parfaitement plausible.

## Décision

Le moteur est **porté fichier par fichier**, avec ses tests, puis adapté. Trois adaptations
seulement, chacune motivée.

**1. Le vocabulaire de provenance.** `agriflow` portait un `DataSource` à un seul axe mêlant
« ce que la valeur est » et « d'où elle vient », plus une seconde table de poids de fiabilité et
ses propres bornes de classement. Le produit en avait alors trois, toutes affichées sous le mot
« fiabilité » (D9 du plan). Le moteur lit désormais `DataOrigin.reliability_weight` et les bornes
de `app.domain.provenance`. `DataSource.DEFAULT` disparaît plutôt que d'être traduit : il valait
0,4 et servait de repli, c'est-à-dire une valeur plausible substituée à une valeur manquante.
Une entrée dont l'origine est inconnue reçoit désormais **zéro** crédit, au lieu de 0,4.

**2. Deux `GrowthStage` homonymes.** Le module portait sa propre énumération, mêmes membres,
**valeurs différentes** : `"initial"` contre `"INITIAL"`. La colonne
`fields.declared_growth_stage` stocke la seconde. Deux énumérations de même nom auraient produit
un stade que la base ne reconnaît pas, sans la moindre erreur à l'écriture du code. Une seule
subsiste, et `STAGE_ORDER` est dérivé de `GrowthStage.sequence` plutôt que réécrit à côté.

**3. Le quota saisonnier**, absent des trois dépôts. La spécification proposait de pondérer le
volume au-dessus du rendement pour une parcelle sous quota. Un quota n'est pas un poids : c'est
un plafond, et il vit à côté du plafond d'infiltration. Pondérer permettrait à un score de
rendement suffisant d'autoriser un apport que le périmètre interdit ; plafonner ne le permet pas.

## Ce que le portage a révélé

Un défaut latent dans le calcul de la pression de vapeur :
`relative_humidity_max_pct or relative_humidity_min_pct`. En Python `0.0` est faux, donc une
humidité maximale nulle basculait silencieusement sur la minimale — et deux valeurs nulles
faisaient tomber le calcul sur Hargreaves-Samani sans raison. Une humidité relative nulle est
rare mais physiquement possible en conditions désertiques, c'est-à-dire précisément dans le
régime où le Souss et les plateaux de l'Est comptent. Corrigé en comparant à `None`, et figé par
un test.

## Conséquence

L'ajustement climatique du Kc (FAO-56 éq. 62) **n'est toujours pas appliqué** : il demande la
hauteur moyenne de la culture par stade, que le référentiel ne porte pas, et la renseigner de
mémoire violerait la règle qui interdit toute constante agronomique non citée.

La limite est donc **déclarée avec le chiffre** — `CropWaterRequirement.caveats_fr` — et elle
nomme le sens du biais : sans correction, l'ETc est sous-estimée en conditions sèches et
ventées, donc la dose aussi. Une sous-irrigation ne se voit pas : la parcelle a l'air normale et
le rendement baisse. « Précision réduite » n'aurait aidé personne à décider.
