# 0015 — Ajouter une liste blanche de relations, et la nommer autrement qu'« inconnue »

**Date** : 2026-09-10  ·  **État** : Acceptée

## Contexte

Le socle SQL de `text_to_sql` a quatre couches, et son système listait la
multi-location comme un **non-objectif**. Les quatre couches ne l'adressent donc
pas : `SELECT * FROM shipments` est un `SELECT` valide, peu coûteux, correct, qui
passe la validation d'arbre, le rôle en lecture seule et la transaction bornée —
et qui renvoie les expéditions de toutes les organisations.

Dans ce dépôt, la RLS attrape ce cas : la surface analytique est faite de vues, et
le rôle analytique n'a aucun privilège sur `app`. La question n'est donc pas
« sommes-nous protégés » mais « que se passe-t-il quand un modèle essaie ».

Sans travail supplémentaire, il se passe ceci : le catalogue ne contient que les
vues, donc `app.shipments` ne se résout pas, donc la requête est refusée avec
« table inconnue ». Protégé, oui. Mais le message classe une tentative de
franchissement d'organisation comme une **faute de frappe**, au même rang qu'un
nom de colonne mal orthographié — et l'événement se perd dans le bruit des
erreurs de génération, qui sont nombreuses et sans intérêt.

## Options

**A — S'en remettre à la résolution de noms.** Gratuit. La garantie devient une
propriété émergente du fait que le catalogue a été peuplé d'une certaine façon.
Un auditeur qui cherche « où est appliquée la liste blanche » ne trouve rien, et
le prochain changement de catalogue la casse sans qu'aucun test ne parle.

**B — Un filtre par expression régulière sur le texte SQL.** Défait par
`app /* x */ . shipments`, par la casse, par un échappement Unicode. Un filtre de
texte sur du SQL est un vœu.

**C — Une étape dédiée dans le pipeline de validation**, avant la résolution de
noms, avec son propre code d'erreur classé comme sécurité.

## Décision

**C.** `app/analytics/validation/relations.py` refuse toute relation dont le
schéma n'est pas `analytics`, produit `RELATION_NOT_ALLOWED`, et ce code figure
dans `SECURITY_CODES` — donc **jamais réparé, toujours journalisé**.

L'étape passe avant la résolution de noms précisément pour que le message soit le
bon. Les alias de CTE sont collectés d'abord : `WITH recent AS (…) SELECT … FROM
recent` nomme une relation qui n'existe dans aucun schéma, et la rejeter serait
un faux positif sur une requête parfaitement légitime.

Neuf cas du corpus adverse portent sur ce point, et chacun nomme la couche censée
l'attraper : table de base directe, table des organisations, annuaire global,
jointure vue→table de base, table de base derrière un alias de CTE, sous-requête,
sondage de `pg_catalog`, sondage d'`information_schema`, et même nom de vue dans
un autre schéma. Le harnais échoue si l'un d'eux est attrapé par une **autre**
couche que celle annoncée : passer par accident n'est pas passer.

## Conséquence

Cette liste blanche n'est pas la couche de sécurité principale et ne doit pas être
lue comme telle. La RLS l'est, et elle tient même si ce fichier est faux — c'est
la seule couche qui ne dépende pas de la correction de notre propre code. Celle-ci
existe pour deux choses : que l'erreur soit **comprise**, et que la tentative soit
**visible**.

Le taux d'attrape du corpus ne bouge pas en ajoutant ces neuf cas — il reste à
47/47 — parce que la couche existe. Il aurait été de 38/47 sans elle, avec neuf
franchissements refusés en tant que fautes de frappe. La spécification prévoyait
que le chiffre baisse ; il ne baisse pas, et la raison est écrite ici plutôt que
laissée à l'interprétation d'un tableau de bord.
