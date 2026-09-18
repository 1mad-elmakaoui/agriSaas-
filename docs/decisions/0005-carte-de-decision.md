# 0005 — La carte de décision est reconstruite depuis la trace d'outils

**Date** : 2026-08-24 · **État** : Acceptée · **Corrige** un défaut d'`atlasagri`

## Contexte

`atlasagri` obtient sa sortie structurée en demandant au modèle de terminer par un bloc
```` ```json ```` récupéré par expression régulière. Le prompt est prudent — « ne remplis ce bloc
qu'avec des valeurs issues des outils » — et le bloc ne contient aucun montant.

Il contient cependant `niveau_risque` et `confiance` : deux valeurs que `RankingResult` a déjà
calculées et que le modèle **ré-énonce**. Si le modèle écrit « Modéré » là où le moteur a produit
`ÉLEVÉ`, c'est la valeur du modèle qui s'affiche.

Une consigne de prompt est un indice, pas une garantie.

## Décision

Le modèle ne rend que `option_recommandee_id` et des raisons rédigées. Tous les champs affichés
— niveau de risque, confiance, écarts de coût et de délai, tronçons exposés — sont relus depuis
le résultat du moteur conservé dans la trace d'outils, par jointure sur cet identifiant. Un
identifiant ne correspondant à aucune option renvoyée n'affiche pas de carte.

## Conséquence

La carte devient structurellement incapable d'afficher un chiffre que le moteur n'a pas produit.
L'analyse d'un bloc Markdown sort du chemin critique. Applicable en phase 4, quand le registre
d'outils et la trace arrivent.
