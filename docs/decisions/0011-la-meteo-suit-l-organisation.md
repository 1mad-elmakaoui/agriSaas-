# 0011 — Choisir le fournisseur de météo d'après l'organisation, pas d'après un drapeau global

**Date** : 2026-08-25  ·  **État** : Acceptée

## Contexte

L'outil d'irrigation a besoin d'une série météo. Deux fournisseurs existent : Open-Meteo, qui
rend des prévisions réelles étiquetées `(FORECAST, EXTERNAL_API)`, et un fournisseur hors ligne
déterministe qui rend une série entièrement étiquetée `(SIMULATED, SEED_DEMO)`.

La première version choisissait d'après `settings.demo_mode`, une bascule de processus. C'était
faux dans les deux sens. Une installation de démonstration servant une organisation réelle lui
aurait donné une météo inventée ; et — le cas grave — une installation ordinaire hébergeant
l'organisation de démonstration lui aurait injecté une prévision authentique au milieu d'un jeu
fabriqué.

Ce second cas est celui qui compte. Un jeu de démonstration où *une* valeur est vraie n'est plus
un jeu de démonstration : il devient partiellement vrai, et plus personne ne peut dire quelle
valeur relève de laquelle. Toute la discipline de provenance de ce dépôt existe pour rendre cette
confusion impossible.

## Options

**A — Garder le drapeau global.** Simple, et faux dès qu'une installation héberge à la fois une
organisation de démonstration et une organisation réelle — c'est-à-dire toute installation de
vente.

**B — Passer le fournisseur dans le `ToolContext`.** Correct, mais déplace la décision chez
l'appelant : chaque consommateur du registre — la route du copilote, le pont MCP, celui qu'on
ajoutera — devrait la reprendre, et un seul oubli suffirait.

**C — Lire `tenants.is_demo` depuis l'outil, sous politique.** La décision vit au seul endroit
qui en a besoin, et la lecture est bornée par la politique d'isolation : une organisation ne peut
lire que sa propre ligne, donc aucun appelant ne peut détourner le choix.

## Décision

**C.** Une organisation marquée `is_demo` reçoit toujours le fournisseur hors ligne. `demo_mode`
survit comme bascule d'installation — elle force le mode hors ligne pour *toutes* les
organisations d'un poste de développement — et `_production_guards` continue de la refuser en
production.

## Conséquence

Le bandeau « démonstration » de l'interface, le drapeau `is_demo` du schéma, l'étiquette
`(SIMULATED, SEED_DEMO)` de chaque ligne semée et le choix du fournisseur météo disent désormais
tous la même chose, et tous à partir de la même colonne.

Ce que cela ferme : la possibilité de faire une démonstration avec « la vraie météo d'
aujourd'hui, ça fait plus concret ». C'est délibéré.

À reconsidérer si une organisation réelle demande une série météo reproductible pour rejouer une
décision passée : ce serait un troisième fournisseur — une archive datée — et non un retour au
drapeau global.
