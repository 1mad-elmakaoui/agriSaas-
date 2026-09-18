# Moteur de risque logistique

Trois modules purs sous `backend/app/domain/logistics/`, plus un service qui les
orchestre. Aucun d'eux n'ouvre de connexion : **la météo leur est donnée**.

```
morocco.py     30 nœuds, 40 axes — faits géographiques
network.py     Dijkstra sur la durée, alternatives par pénalisation itérative
road_risk.py   quatre critères de vigilance routière, à trois bornes
transport.py   coût explicable ligne à ligne
exposure.py    le véhicule déplacé dans le temps, tronçon par tronçon
alternatives.py génération et filtrage — le classement vit dans domain/ranking
```

## Ce que le moteur fait, et que personne d'autre ne fait

**Un itinéraire n'est pas exposé parce qu'il traverse une région où il pleuvra
dans trente heures.** Il est exposé si le véhicule s'y trouve *pendant* la
fenêtre de perturbation. Un camion qui franchit le col dans deux heures ne
rencontre pas l'orage prévu pour la nuit suivante.

Sans cette dimension, le système surestimerait massivement le risque, alerterait
sur des trajets déjà terminés, et cesserait d'être cru en quelques jours — ce qui
est pire qu'un système absent, parce qu'on aurait cessé de regarder ailleurs.

Corollaire pratique : le ralentissement d'un tronçon décale l'heure de passage sur
**tous** les suivants. Cumuler la durée nominale placerait le véhicule au mauvais
endroit au mauvais moment, et l'exposition calculée porterait sur une fenêtre
qu'il ne traverse pas — l'erreur la plus difficile à repérer, parce que le
résultat reste plausible.

## Risque routier ≠ risque agronomique

Deux phénomènes qu'il serait faux de mesurer avec la même règle.

| | Risque agricole | Risque routier |
|---|---|---|
| Porte sur | la culture au champ | la circulation d'un poids lourd |
| S'évalue sur | des cumuls longs (48 h et plus) | l'**intensité** pendant la traversée, et la **saturation antérieure** des sols |
| Pourquoi | c'est la quantité d'eau reçue qui abîme un plant | visibilité et aquaplanage sur l'instant ; ruissellement et coupures sur les sols déjà saturés |

Comparer un cumul d'une heure de traversée à un seuil agronomique de 48 h
sous-estimerait systématiquement le risque routier. Les quatre critères
(`road_risk.py`) corrigent ce défaut de catégorie, et chacun porte son
`rationale_fr` — affiché à côté du chiffre, pour qu'un seuil dépassé se conteste.

## Alternatives : générer, filtrer, puis classer

Trois étapes séparées, et la séparation est le point.

**Générer** — le plan actuel, les corridors de repli, les décalages de départ. Les
décalages **négatifs** comptent autant que les positifs : avancer un départ est
souvent la meilleure réponse à un front qui arrive, et un moteur qui n'explorerait
que les retards ne la trouverait jamais.

**Filtrer** — une contrainte dure élimine. Échéance manquée, capacité de
destination insuffisante, départ avant le délai de préparation : l'option sort du
classement. Ranger un plan infaisable, même en dernier, le met dans un tableau où
l'utilisateur le lira comme un choix possible.

**Classer** — par `app.domain.ranking`, partagé avec les autres domaines
(décision 0002). Aucune implémentation de classement ne vit ici.

Les options écartées sont conservées **avec leur motif chiffré**. « Capacité 120 t
< 180 t demandées » se conteste ; « infaisable » ne se conteste pas. Et une option
écartée ne porte aucun chiffre de comparaison — les renseigner la ferait figurer
au tableau comme un choix possible.

## Les corridors sont distincts, pas des variantes

La génération d'alternatives pénalise les arêtes déjà empruntées puis recalcule.
Cela produit des corridors réellement différents — montagne, littoral, intérieur —
là où une recherche des *k* plus courts chemins rendrait *k* variantes du même
trajet, dont un exploitant ne pourrait rien faire.

Deux garde-fous : une alternative dépassant 1,75 fois la durée du meilleur trajet
n'est pas proposée, et deux itinéraires partageant plus de 70 % de leurs nœuds
intermédiaires ne sont pas présentés côte à côte. Trois alternatives crédibles
valent mieux que cinq dont deux sont absurdes.

Le coût minimisé est la **durée**, pas la distance : un détour autoroutier plus
long en kilomètres est souvent plus rapide, et c'est la durée qui compte pour une
marchandise périssable.

## L'indicateur de perturbation n'est pas une probabilité

Il s'appelle `disruption_indicator`, et le nom porte la limite. C'est un indicateur
**comparatif** dérivé de règles explicites, dont l'usage légitime est de classer
des itinéraires entre eux.

Aucun historique d'incidents marocains ne l'a calibré. L'API rend donc la mise en
garde **dans la même charge utile** que le chiffre : il n'existe aucun chemin par
lequel l'indicateur arriverait seul à l'écran ou au modèle. Affiché seul, il
devient une probabilité dans la tête du lecteur, et c'est irréversible.

## Ce qui n'est pas mesuré

Les seuils de vigilance, les vitesses moyennes, les fiabilités d'axe, les
paramètres de coût et les pondérations d'arbitrage sont des **valeurs de départ**.
Lignes 31 à 34 de `ce-qui-nest-pas-mesure.md`.

Ce qui *est* un fait : les coordonnées des villes et les distances routières entre
elles.

Le moteur classe correctement selon ces valeurs. Il ne dit pas qu'elles sont
justes, et l'interface affiche l'avertissement sur chaque analyse.

## Ce qui n'est pas porté

Les options de sourcing (changer de fournisseur) et d'entrepôt (rupture de charge
intermédiaire) existent dans le code d'origine et ne sont pas reprises : elles
demandent des fournisseurs et des stocks, que ce dépôt n'a pas encore. Les
inventer produirait des alternatives plausibles fondées sur rien.
