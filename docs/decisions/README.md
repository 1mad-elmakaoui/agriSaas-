# Décisions d'architecture

Un fichier par décision structurante, numéroté, jamais réécrit — une décision remplacée est
marquée *Remplacée par NNNN*, pas supprimée. L'écart entre ce qui était spécifié et ce qui a
été construit est l'endroit où se trouve l'ingénierie intéressante.

Modèle :

```markdown
# NNNN — Titre à l'impératif

**Date** : AAAA-MM-JJ  ·  **État** : Acceptée | Remplacée par NNNN

## Contexte
Ce qui rendait la décision nécessaire. Les contraintes réelles, pas les préférences.

## Options
Ce qui a été envisagé, et ce que chaque option coûtait.

## Décision
Ce qui a été retenu.

## Conséquence
Ce que cela rend possible, ce que cela ferme, ce qu'il faudra reconsidérer et quand.
```

Écrire une décision aussi lorsque la spécification `docs/00-prompt-unification.md` a été
délibérément contredite — surtout dans ce cas.
