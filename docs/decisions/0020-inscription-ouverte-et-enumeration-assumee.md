# 0020 — Ouvrir l'inscription, et assumer l'énumération qu'elle crée

**Date** : 2026-09-18  ·  **État** : Acceptée

## Contexte

La §6 demande une inscription autonome : *self-serve signup → organisation →
seeded reference data → guided onboarding*, avec une première valeur en moins de
dix minutes.

Une inscription ouverte crée deux problèmes que le reste du produit n'avait pas.

**Elle énumère les comptes.** Un formulaire d'inscription doit dire « ce courriel
est déjà utilisé », sinon il est inutilisable — la personne resaisit indéfiniment
une adresse qui ne passera jamais. Mais le dire renseigne un attaquant sur les
adresses qui ont un compte. Or `AuthService` fait exactement l'inverse sur la
connexion : un message unique, et un temps de réponse constant, précisément pour
ne pas énumérer.

**Elle crée des lignes sans autorisation préalable.** Chaque appel réussi écrit
une organisation. Un script en écrirait des milliers avant que quiconque le
remarque.

## Options

**A — Ne pas l'ouvrir.** Les comptes restent créés par un administrateur, comme
avant la phase 7. Contredit la spécification sur un point qu'elle énonce en
premier, et supprime la seule façon qu'a quelqu'un d'essayer le produit.

**B — L'ouvrir avec un message d'échec générique.** « L'inscription n'a pas
abouti. » Préserve la non-énumération et rend le formulaire impraticable : la
personne qui a déjà un compte n'apprend jamais qu'elle doit se connecter.

**C — L'ouvrir, dire que le courriel est pris, et borner le débit.**

## Décision

**C.** L'inscription dit qu'un courriel est déjà rattaché à une organisation, et
la route est limitée à **cinq appels par heure et par adresse** — bien en dessous
du seuil général de 120 par minute.

L'asymétrie avec la connexion est délibérée et vaut d'être comprise. Sur la
connexion, distinguer les causes d'échec n'apporte **rien** à l'utilisateur
légitime : il sait s'il a un compte. Sur l'inscription, la distinction est
l'information utile — c'est même la seule chose que la personne a besoin
d'apprendre. Le gain d'un attaquant est le même dans les deux cas ; le gain de
l'utilisateur ne l'est pas. Là où les deux se valaient, nous avons choisi la
discrétion ; là où l'un des deux est inutilisable, nous choisissons l'utilisable
et nous l'écrivons.

Le seuil de débit borne ce que l'énumération rapporte réellement : cinq adresses
sondées par heure et par adresse IP n'est pas un oracle exploitable à l'échelle
d'un annuaire.

## Conséquence

L'organisation créée est **vide** : aucune donnée fabriquée, aucun site de
démonstration. Le référentiel agronomique étant global, elle le lit
immédiatement sans qu'une seule ligne soit copiée pour elle — et elle peut poser
ses propres valeurs mesurées par-dessus le jour où elle en a.

Le plan attribué est le plus petit. Provisionner mieux à l'inscription
reviendrait à vendre sans qu'un administrateur l'ait décidé, ce que la §6
interdit.

L'identifiant lisible est **translittéré** : « Coopérative Aït Melloul » donne
`cooperative-ait-melloul`. Sans translittération, les accents disparaîtraient et
le nom deviendrait méconnaissable dans une URL — au moment précis où il faut le
reconnaître, c'est-à-dire pour confirmer une suppression. Son unicité est tranchée
par la contrainte de base et non par une lecture préalable : l'organisation qui
s'inscrit ne peut de toute façon pas lire la ligne d'une autre, et c'est
exactement ce qu'on veut.

Ce que cela ouvre et que nous ne fermons pas : **aucune mention d'information
n'est présentée** au formulaire. La personne saisit son nom et son courriel sans
qu'aucun texte ne lui dise qui traite ces données ni comment exercer ses droits.
C'est un manquement à la loi 09-08, il est inscrit au grand livre (ligne 46) et
dans `docs/conformite.md`, et il se comble par un texte que doit rédiger le
responsable de traitement — pas par du code. Il aurait été facile d'ouvrir
l'inscription sans le dire ; c'est précisément ce que ce dépôt refuse.
