# 0012 — Faire de la trace d'outils la réponse, et de la prose du modèle un commentaire

**Date** : 2026-09-09  ·  **État** : Acceptée

## Contexte

La phase 5 rend le copilote visible. La question est de savoir ce que l'écran
présente comme *la réponse*.

Un copilote se dessine naturellement comme une conversation : la phrase du
modèle en haut, grande, et les détails techniques repliés en dessous. C'est ce
que font la plupart des produits, et c'est ce que les utilisateurs attendent.

C'est aussi la seule disposition qui rend ce dépôt inutile. Un chiffre retapé par
le modèle dans une phrase n'a plus de source : rien, à l'écran, ne le distingue
d'un chiffre calculé. Toute la chaîne — moteurs déterministes, provenance par
valeur, registre d'outils, enveloppe de contenu non fiable — existe pour que le
nombre affiché soit traçable jusqu'au calcul qui l'a produit. La placer derrière
un dépliant revient à la démonter au dernier mètre.

## Options

**A — La prose du modèle en premier, la trace derrière « Voir les calculs ».**
Familier, agréable, et faux au sens ci-dessus.

**B — Interdire la prose.** Cohérent mais appauvrissant : une phrase qui relie
deux résultats d'outils (« la parcelle est en stress parce que le tour d'eau
précédent a été plafonné par le quota ») a une valeur réelle, et c'est ce que le
modèle sait faire.

**C — La trace rendue en interface structurée comme réponse ; la prose en
dessous, dans un panneau nommé « Commentaire du copilote », avec la mention que
les chiffres au-dessus font foi.**

## Décision

**C.** Concrètement, dans `CopilotPage` :

* chaque appel d'outil réussi est rendu comme une **décision** — recommandation,
  chiffres, entrées avec leur provenance, étapes numérotées — par les mêmes
  composants que la page Parcelles ;
* un appel en échec est rendu comme un échec français, avec la mention qu'aucune
  valeur n'a été substituée ;
* **zéro appel d'outil produit un état explicite** : « Le copilote n'a consulté
  aucun moteur de calcul pour cette question. Sa réponse ne repose donc sur aucun
  chiffre de votre organisation. » C'est le cas le plus dangereux et le plus
  facile à masquer ;
* la prose arrive en dernier, dans un panneau qui dit ce qu'elle est.

Un test rend la règle exécutable : le modèle y annonce « environ 300 m³ » en
prose alors que le moteur a rendu 84 m³, et le test vérifie que l'écran affiche
84 m³ et que la phrase du modèle est bien dans le panneau de commentaire.

## Conséquence

Le copilote devient un mode d'accès aux moteurs plutôt qu'un interlocuteur. Il
répond moins joliment, et ce qu'il répond est vérifiable.

Ce que cela ferme : une démonstration où le copilote « discute ». Ce que cela
ouvre : une réponse que l'utilisateur peut contester ligne par ligne, ce qui est
la seule façon d'être cru par quelqu'un dont la récolte dépend du chiffre.

À reconsidérer lorsqu'un domaine produira des alternatives comparées — le
panneau de recommandation logistique aura sa propre forme structurée, mais la
règle ne change pas : c'est la sortie du moteur qui est rendue, pas la phrase qui
la décrit.
