# 0013 — Écrire les nombres en français jusque dans les moteurs

**Date** : 2026-09-09  ·  **État** : Acceptée

## Contexte

Les moteurs déterministes produisent des phrases : étapes de calcul,
avertissements, explications de bilan hydrique. Ces phrases ne sont pas des
journaux — elles sont affichées telles quelles dans le panneau « Pourquoi cette
décision ? ».

Écrites avec les formats de Python, elles donnaient :

```
4. Réserve utile totale (RU) = 1000 × (0.250 − 0.120) × 1.50 m = 195.0 mm.
```

sous un titre formaté par l'interface :

```
ET0   4,78 mm/j
```

Le même écran mêlait donc deux conventions décimales. Le lecteur doit alors
décider, chiffre par chiffre, si un point sépare les décimales ou les milliers.

Le cas qui tranche est le coût. En `fr-MA`, `Intl.NumberFormat` groupe les
milliers avec un point : **3.936 MAD** pour trois mille neuf cent trente-six
dirhams, à côté de **4,78 mm/j**. C'est la seule combinaison réellement
ambiguë, et elle porte sur un facteur mille.

## Options

**A — Ne rien changer.** Le mélange est visible sur toutes les captures du
panneau, et l'ambiguïté sur le coût est réelle.

**B — Convertir à la frontière de présentation.** Une expression régulière
transformant `chiffre.chiffre` en `chiffre,chiffre` dans les chaînes venues du
serveur. Rapide, et faux : elle toucherait aussi les identifiants, les versions
et tout ce qui ressemble à un nombre sans en être un.

**C — Formater en français à la source, dans le domaine.** Un module pur,
`app/domain/formatting.py`, et une réécriture mécanique des ~90 interpolations
numériques des moteurs d'irrigation.

## Décision

**C**, avec deux précisions qui font que les deux moitiés du produit écrivent le
même nombre de la même façon :

* le séparateur de milliers est **U+202F**, l'espace fine insécable — exactement
  le caractère que produit `Intl.NumberFormat('fr-FR')` ;
* l'interface utilise `fr-FR` et non `fr-MA` pour les **nombres**, précisément
  pour éviter le groupement par point. Le Maroc emploie les deux conventions ;
  à l'intérieur d'un seul écran, une seule est tenable.

Un test parcourt les phrases produites par le moteur et échoue sur tout point
encadré de chiffres — puis vérifie qu'une virgule décimale est bien présente,
pour qu'il ne puisse pas passer simplement parce qu'aucun nombre n'est affiché.

## Conséquence

`domain/` gagne une dépendance interne, `app.domain.formatting`, qui reste pure —
aucune bibliothèque, aucune locale système, donc aucun comportement dépendant de
l'environnement d'exécution.

Ce que cela ferme : l'idée que les chaînes des moteurs sont « techniques ». Elles
sont de l'interface, et la règle de langue s'y applique entièrement.

À reconsidérer avec la localisation `ar-MA` : les chiffres arabes orientaux et le
sens de lecture des nombres dans un texte de droite à gauche sont un problème
distinct, et il se traitera dans ce module plutôt que dans quatre-vingt-dix
f-strings.
