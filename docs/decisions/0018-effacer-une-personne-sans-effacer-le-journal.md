# 0018 — Effacer une personne sans effacer le journal d'audit

**Date** : 2026-09-18  ·  **État** : Acceptée

## Contexte

La loi 09-08 donne à une personne le droit d'obtenir l'effacement de ses données.
La §9 exige un journal de qui a vu quoi, qui a approuvé quoi, qui a changé quel
seuil. Les deux exigences se rencontrent sur la même ligne : le journal nomme des
personnes.

Le conflit est réel et n'a pas de résolution parfaite. Supprimer les lignes d'un
membre efface aussi la preuve des accès **subis par les autres** — si quelqu'un a
consulté les données d'un collègue puis fait supprimer son compte, la trace de
cette consultation disparaît avec lui. Conserver les lignes telles quelles refuse
un droit opposable.

## Options

**A — Supprimer les lignes du journal.** Le droit de l'un est satisfait en
retirant aux autres le leur. Le journal cesse d'être une preuve : il devient une
liste que la personne concernée peut faire raccourcir.

**B — Ne rien supprimer, et invoquer l'obligation légale de traçabilité.**
Défendable dans certains cadres, mais le journal contient un courriel nominatif,
donc une donnée personnelle, et la conservation devrait alors être justifiée,
bornée et purgée — ce qu'aucune tâche ne fait ici (grand livre, ligne 42).

**C — Pseudonymiser.** Le compte, l'entrée d'annuaire et le condensat du mot de
passe disparaissent. Au journal, le courriel est remplacé par `supprimé-<8 hex>`
et l'identifiant technique subsiste, sans plus rien à quoi le rattacher : la
table `users` ne contient plus la ligne correspondante.

## Décision

**C**, avec une réserve écrite.

Ce qui reste est un identifiant opaque et une suite d'actes. Rattacher cet
identifiant à une personne demande une source extérieure au système — un export
antérieur, une sauvegarde. Ce n'est donc pas un effacement complet, et
`docs/conformite.md` le dit en ces termes plutôt qu'en annonçant « droit à
l'effacement : implémenté ».

Une personne qui exige davantage obtient la suppression de l'**organisation**
entière, qui emporte le journal avec le reste. C'est cohérent : conservé, ce
journal désignerait par courriel des personnes dont le compte vient d'être
effacé, dans une organisation qui n'existe plus pour en répondre.

## Conséquence

Deux refus accompagnent la suppression d'un membre, et ils ne sont pas du confort :
on ne supprime pas son propre compte, et on ne supprime pas le dernier
administrateur. Une organisation sans administrateur ne peut plus ni inviter, ni
changer de plan, ni se supprimer — elle ne peut qu'appeler au secours.

La suppression d'une organisation exige de recopier son identifiant lisible. Une
case à cocher se coche par réflexe ; un nom se recopie en ayant lu ce qu'on tape.
L'audit est écrit **avant** l'effacement : écrit après, il porterait sur une
organisation dont la ligne n'existe plus.

Enfin, l'export et l'effacement parcourent les tables **découvertes depuis le
modèle**, et filtrent explicitement sur l'organisation plutôt que de s'en remettre
à la politique RLS. Les tables de référentiel laissent passer la ligne globale :
sans ce filtre, un export contiendrait le catalogue FAO présenté comme les données
du client, et un effacement le supprimerait **pour tout le monde**.
