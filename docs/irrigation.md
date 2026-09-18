# Moteur d'irrigation

Le moteur répond à quatre questions, et refuse d'en répondre une cinquième qu'on ne lui a pas
donné les moyens de traiter.

> Faut-il irriguer cette parcelle aujourd'hui ? · Quelle quantité ? · Pourquoi celle-là ? ·
> Que se passe-t-il si je réduis de 20 % ?

Il est **pur** : ni base de données, ni réseau, ni horloge, ni modèle de langage. La météo et la
date arrivent en arguments. C'est ce qui le rend testable en microsecondes et auditable par un
agronome qui ne lit pas de code web — et un test d'architecture interdit à `domain/` d'importer
SQLAlchemy, FastAPI, httpx ou `anthropic`.

## La chaîne de calcul

```
météo + culture + sol + parcelle + système + stade + humidité
                          │
              ET0  ── FAO-56 éq. 6 (Penman-Monteith)
                   └─ repli éq. 52 (Hargreaves-Samani), déclaré
                          │
              ETc = ET0 × Kc          éq. 31, Kc par stade (fig. 21)
                          │
              bilan racinaire         éq. 82-84 : TAW, RAW, Dr, Ks
                          │
              besoin d'irrigation     dose, plafonds, volume
                          │
        volume · durée · coût · stress · qualité des données
```

Chaque étape porte son numéro d'équation FAO jusque dans le panneau « Pourquoi cette
décision ? ». Un chiffre dont on ne peut pas remonter la chaîne n'a pas sa place à l'écran.

## Ce que le moteur refuse de faire

| Entrée manquante | Ce que le moteur rend |
|---|---|
| débit | volume oui, **durée non** — « débit non renseigné » |
| tarif de l'eau | volume oui, **coût non** |
| coefficient Ky (FAO-33) | **aucun chiffre de rendement**, avec la raison |
| date de plantation, culture annuelle | **aucun stade estimé** — il lève plutôt que de deviner |
| humidité **ou** vent | ET0 par Hargreaves-Samani, et le résultat dit lequel a manqué |

Jamais un zéro, jamais une valeur voisine, jamais une fourchette. Un tableau de bord qui affiche
« 0 h » parce qu'il ignore le débit est plus dangereux qu'un tableau de bord qui dit ne pas
savoir.

## Les plafonds, et leur ordre

Le déficit **projeté** décide s'il faut irriguer ; le déficit **du jour** fixe la dose. Ajouter
la demande de l'horizon à la dose enverrait l'eau sous les racines — invisible, parce que la
parcelle a l'air bien arrosée.

Trois plafonds s'appliquent ensuite, dans cet ordre :

1. **Infiltration** — ce que le sol absorbe en une application. Au-delà, la dose est plafonnée
   et le conseil devient « fractionnez l'irrigation ».
2. **Quota saisonnier** — ce que le périmètre autorise. Une contrainte, pas un critère
   d'arbitrage : voir `docs/decisions/0002-deux-moteurs-un-contrat.md`.
3. **Dose minimale utile** — en dessous, l'apport s'évapore avant d'atteindre les racines. Le
   moteur préfère « à surveiller » à un geste qui gaspille l'eau *et* le tour d'eau.

Les deux derniers se composent : un quota si serré que la dose autorisée tombe sous le seuil
d'utilité produit « à surveiller », pas un filet d'eau inutile.

## Ce qui est mesuré, et ce qui ne l'est pas

Sept exemples publiés de la FAO-56 sont reproduits à l'arrondi près — c'est ce qui valide
l'**arithmétique** contre la publication.

Cela ne valide pas la dose sur une parcelle marocaine. Trois limites connues, toutes dans
`docs/ce-qui-nest-pas-mesure.md` :

- les paramètres FAO sont des références **pour conditions standard**, jamais mesurés dans le
  Souss ni le Gharb ;
- l'ajustement climatique du Kc (éq. 62) n'est pas appliqué faute de hauteurs de culture citées,
  ce qui **sous-estime** l'ETc en conditions sèches et ventées — la limite voyage avec le chiffre
  plutôt que dans un document à part ;
- la dose est calculée sur la surface entière, ce qui **surestime** le besoin sous
  goutte-à-goutte.

Les deux derniers biais jouent en sens contraire. Ils ne s'annulent pas : ils s'additionnent
différemment selon la parcelle, et aucun des deux n'est quantifié.
