# 0016 — Envoyer le schéma entier, et ne pas porter la récupération vectorielle

**Date** : 2026-09-10  ·  **État** : Acceptée

## Contexte

`text_to_sql` récupère un sous-ensemble du schéma par similarité vectorielle,
puis l'étend par les clés étrangères. C'est une bonne conception, et elle répond
à une contrainte précise, énoncée par la compétence `sql-safety-stack` : *« les
schémas complets ne tiennent pas dans une invite, et le sous-ensemble qui y tient
est pire que le bon sous-ensemble »*.

La surface analytique de ce produit compte **six vues et soixante-quinze
colonnes**. Elle tient dans une invite avec une marge considérable.

La récupération a un mode d'échec que le reste du socle n'a pas : il est
**silencieux**. Une requête produite sur les mauvaises tables est valide, passe
la validation d'arbre, passe `EXPLAIN`, s'exécute, renvoie des lignes, et
l'explication les décrit avec assurance. Rien ne signale quoi que ce soit. C'est
exactement le défaut que la compétence décrit pour un modèle d'embarquement
anglophone face à une question française — et le remède qu'elle prescrit (un
modèle multilingue, avec ses préfixes) réduit le taux d'échec sans jamais le
rendre visible.

## Options

**A — Porter la récupération telle quelle.** On hérite d'un index vectoriel,
d'une dépendance à un modèle d'embarquement, d'un mode d'échec silencieux, et
d'une exigence de mesure multilingue — pour choisir six vues parmi six.

**B — Porter la récupération et la neutraliser** (renvoyer toujours tout).
Le pire des deux : le code existe, semble faire quelque chose, et ne le fait pas.

**C — Envoyer le schéma entier, et écrire pourquoi.**

## Décision

**C.** `prompt.schema_block()` rend les six vues, leurs colonnes typées, leurs
commentaires, les valeurs d'énumération déclarées et le graphe de jointure.

La récupération redeviendra nécessaire à une condition précise, qu'il faut nommer
pour qu'on la reconnaisse en arrivant : **le jour où une organisation pourra
définir ses propres champs, cultures ou produits.** Le catalogue cesse alors
d'être commun, sa taille cesse d'être bornée, et — c'est le point que la
compétence souligne — l'index doit être borné à l'organisation, faute de quoi les
identifiants d'une organisation apparaissent dans le panneau de schéma et dans le
SQL généré d'une autre.

Ce jour-là, l'index vectoriel arrive avec sa contrainte de multi-location dès le
premier jour, pas après. `pgvector` est déjà installé et une sonde de démarrage
le vérifie ; rien n'est à défaire.

## Conséquence

Ce qui disparaît : une dépendance à un modèle d'embarquement, un index à
maintenir et réindexer, un mode d'échec invisible, et la nécessité de mesurer une
recall multilingue pour un choix parmi six.

Ce qui reste à faire, et qui figure au registre d'honnêteté : la mesure de
**génération** multilingue. Elle ne dépendait pas de la récupération — elle
demande une clé d'API — et son absence n'est pas comblée par cette décision.

Ce que cela rend mesurable dès maintenant, et qui l'est : que chaque requête de
référence du corpus multilingue est valide contre le catalogue réel. C'est une
mesure du **corpus**, pas du modèle, et le harnais le dit sur la ligne suivante
pour que personne ne rapporte la première sous le nom de la seconde.
