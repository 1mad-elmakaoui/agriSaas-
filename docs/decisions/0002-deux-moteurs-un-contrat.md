# 0002 — Ne pas fusionner le simulateur d'irrigation et le moteur d'alternatives

**Date** : 2026-08-24 · **État** : Acceptée · **Contredit** `00-prompt-unification.md` §4.6

## Contexte

La §4.6 affirme que le simulateur de scénarios d'`agriflow` et l'`AlternativeEngine`
d'`atlasagri` « sont la même forme » — générer des candidats, filtrer les infaisables, noter par
critères pondérés, classer, expliquer, conserver les écartés — et demande un `DecisionEngine`
unique à générateurs par domaine.

La lecture du code contredit l'affirmation sur trois des six étapes.

## Options

1. **Fusionner comme spécifié.** `Alternative` devient générique : soit une union de champs
   optionnels (chaque variante d'irrigation porte `distance_km=0.0`, un mensonge de schéma dans
   un produit où chaque valeur porte son état épistémique), soit un vecteur `dict[str, float]`
   qui jette les champs typés dont dépendent le classement, le panneau de preuves et la carte.
   Coût : ~1 200 lignes réécrites.
2. **Unifier le contrat de sortie, garder deux moteurs.**

## Décision

Option 2.

Côté logistique, les six étapes existent littéralement : les candidats sont hétérogènes et
incomparables par construction — un itinéraire côtier, un fournisseur de Berkane, un entrepôt
intermédiaire — et ne deviennent comparables que par normalisation min-max sur un vecteur de
critères pondéré par profil produit.

Côté irrigation, la décision est une cascade de seuils sur **une seule grandeur physique** :

```python
if forecast_rainfall_postpones:  POSTPONE_RAIN
elif projected >= trigger:       IRRIGATE      # trigger = RAW × fraction
elif projected >= monitor:       MONITOR
else:                            NO_IRRIGATION
```

Il n'y a pas de candidats à générer : il y a une position sur un axe à lire. Rien n'est écarté —
la dose est **plafonnée** (capacité d'infiltration) ou annulée (sous la dose minimale utile),
avec un avertissement français ; transformer un plafonnement en rejet perdrait le conseil réel,
« fractionnez l'irrigation ». Et aucun vecteur de poids n'existe.

Il ne doit pas en exister. Pondérer « eau / rendement / coût / stress » exigerait un modèle
d'arbitrage agronomique que la FAO-56 ne fournit pas : une valeur inventée, présentée avec la
même apparence d'objectivité que les autres. À l'écran, cela remplacerait une chaîne auditable
— « déficit projeté 41,2 mm ≥ seuil 38,0 mm, soit la RFU » — par « report noté 0,62 ». La
première est contestable par un agronome ; la seconde ne l'est par personne.

Le quota d'un périmètre en pénurie, que la §4.6 cite à l'appui, est une **contrainte** et non un
poids : un plafond de volume, à placer à côté du plafond d'infiltration existant. Absent des
trois dépôts, il sera ajouté là.

## Conséquence

Ce qui est réellement partagé est unifié : le contrat de sortie
`Decision { recommendation, alternatives_considered (avec motif), tradeoffs, explanation,
evidence, confidence, human_decision }`, la table `recommendations`, et les deux panneaux du §7.
`OptimizationService` est généralisé sur trois domaines — logistique, sourcing, stocks — via un
protocole `Rankable`. L'irrigation ne l'implémente pas, parce qu'elle n'a rien à classer.

À reconsidérer si un jour un arbitrage agronomique **mesuré** existe (essais locaux liant dose,
rendement et prix de l'eau). Alors, et seulement alors, l'irrigation aura des poids défendables.
