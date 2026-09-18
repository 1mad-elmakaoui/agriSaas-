# 0014 — Porter les moteurs logistiques en les rendant purs

**Date** : 2026-09-09  ·  **État** : Acceptée

## Contexte

La phase 3 devait porter « risque, exposition d'itinéraire, alternatives,
optimisation » depuis `atlasagri`. Elle ne l'a pas fait : seuls le moteur
d'irrigation et le moteur de classement générique ont été portés, et le critère
d'achèvement de la phase — « la sortie classée inclut les options écartées avec
leur motif » — était satisfait par le contrat de classement seul.

L'écart s'est vu en phase 5, quand l'interface a eu besoin d'afficher une
analyse d'expédition qui n'existait pas. C'est la ligne 25 du registre
d'honnêteté.

Le code d'origine a un défaut structurel qu'il ne fallait pas reprendre.
`RouteRiskService.assess()` appelait `get_weather_provider()` — un registre
global — depuis la couche service. Conséquence : l'exposition n'était testable
ni sans réseau ni sans monkeypatch, et **la partie qui fait la valeur du moteur
n'avait aucun test**. Cette partie est le déplacement du véhicule dans le temps :
un itinéraire n'est pas exposé parce qu'il traverse une région où il pleuvra dans
trente heures, il est exposé si le camion s'y trouve *pendant* la fenêtre.

## Options

**A — Porter tel quel.** Rapide, et on hérite d'un moteur non testable dont la
propriété centrale n'est vérifiée nulle part. Dans un dépôt dont la règle est
« les garanties viennent de la structure », c'est le pire choix disponible.

**B — Réécrire depuis la publication.** Le raisonnement — intensité pendant la
traversée, saturation antérieure des sols, vulnérabilité par classe de route,
pénalisation itérative pour des corridors distincts — est bon et coûteux à
retrouver. Le réécrire coûterait le prix de la redécouverte pour le bénéfice
d'un style.

**C — Porter la logique, inverser la dépendance.** Les moteurs deviennent purs :
la météo leur est **donnée**. La couche service échantillonne, traduit et
appelle.

## Décision

**C.** `app/domain/logistics/` contient `morocco` (le graphe de référence),
`network` (Dijkstra et alternatives), `road_risk` (les seuils), `transport` (le
coût) et `exposure` (l'exposition), tous purs. `LogisticsService` orchestre.

Trois écarts assumés par rapport à la source :

1. **Le classement n'est pas reporté.** `OptimizationService` est remplacé par
   `app.domain.ranking`, déjà partagé avec les autres domaines (décision 0002).
   Une seconde implémentation aurait divergé.
2. **`disruption_probability` devient `disruption_indicator`.** L'ancien nom
   invitait à afficher « 34 % de risque de perturbation », ce qu'aucune donnée
   ne soutient : rien n'a été calibré sur un historique d'incidents marocains.
   Le nom porte désormais la limite, et l'API rend la mise en garde *dans la
   même charge utile* que le chiffre — il n'existe aucun chemin par lequel
   l'indicateur arriverait seul à l'écran ou au modèle.
3. **Les options de sourcing et d'entrepôt ne sont pas portées.** Elles
   demandent des fournisseurs et des stocks, que ce dépôt n'a pas. Les inventer
   produirait des alternatives plausibles fondées sur rien.

## Conséquence

Vingt-trois tests couvrent désormais ce que la source ne testait pas : qu'un
orage prévu après l'arrivée n'expose rien, qu'un tronçon ralenti décale l'heure
de passage sur tous les suivants, qu'un tronçon sans météo est *non évalué* et
non *sans risque*, et qu'un tronçon critique court n'est pas dilué par la
moyenne.

Un défaut a été trouvé en portant : `fixed_cost = fixed_cost_mad * trucks /
max(1, trucks)` vaut toujours `fixed_cost_mad` et contredit le commentaire
« forfait par expédition » qui l'accompagnait. L'une des deux était fausse. Le
portage tranche pour le par-camion — un convoi de huit frigorifiques ne coûte pas
les frais fixes d'un seul — et un test fixe la décision.

Ce que cela ferme : l'idée qu'un moteur puisse consulter le réseau lui-même. Ce
que cela ouvre : un adaptateur OSRM, une archive météo datée pour rejouer une
décision passée, ou un graphe routier d'un autre pays, tous substituables sans
toucher au raisonnement.
