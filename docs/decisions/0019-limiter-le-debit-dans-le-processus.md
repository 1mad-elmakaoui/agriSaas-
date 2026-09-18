# 0019 — Limiter le débit dans le processus, et le dire

**Date** : 2026-09-18  ·  **État** : Acceptée

## Contexte

La §9 demande des limites de débit, dont une plus stricte sur le copilote et
l'agent d'analyse. Les deux surfaces n'ont pas le même coût : une requête de
liste coûte une requête SQL indexée, une question au copilote coûte plusieurs
appels de modèle, plusieurs secondes et de l'argent réel.

## Options

**A — Un magasin partagé (Redis).** Seuil exact quel que soit le nombre de
travailleurs. Ajoute une dépendance d'infrastructure à un produit qui n'en a
aucune autre, et un mode de panne nouveau : que fait-on quand le magasin est
injoignable — on laisse tout passer, ou on ferme le produit ?

**B — En mémoire du processus.** Aucune dépendance. Avec plusieurs travailleurs,
le seuil effectif est multiplié par leur nombre, et un redémarrage remet les
compteurs à zéro.

**C — Rien, en attendant un déploiement réel.**

## Décision

**B**, avec la limite inscrite au grand livre (ligne 40) et dans la docstring du
module.

Ce que cette implémentation protège est réel : une boucle client partie en vrille,
un écran qui se rafraîchit en rafale, un usage accidentellement abusif du
copilote. Ce qu'elle ne protège pas l'est tout autant : un attaquant déterminé,
qui n'a qu'à attendre un redémarrage ou à répartir ses requêtes.

Annoncer « limitation de débit » sans cette réserve serait exactement la
conformité de façade que ce dépôt refuse ailleurs. La réserve est donc écrite là
où quelqu'un la lira — dans le module, pas seulement dans un document.

## Conséquence

Deux seuils, posés à deux endroits différents, et la différence est structurelle :

**Le seuil général est un intergiciel, clé sur l'adresse cliente.** En
intergiciel parce qu'une dépendance se pose routeur par routeur, et que le
routeur ajouté dans six mois sera celui qu'on aura oublié. Sur l'adresse parce
que la limite doit s'appliquer **avant** l'authentification : posée sur
l'utilisateur, elle ne s'appliquerait jamais à celui qui essaie d'entrer.

**Le seuil de l'agent est une dépendance de routeur, clé sur l'utilisateur.** Sur
l'utilisateur parce qu'une limite de modèle par adresse punirait toute une
exploitation derrière une seule sortie internet pour l'usage d'une personne.
Posée sur le routeur et non route par route, elle couvre aussi les routes qui
n'existent pas encore.

La fenêtre est **glissante** et non fixe : avec une fenêtre fixe, un client
consomme le double du seuil à cheval sur la frontière, et l'exploitant constate
le pic que la limite était censée empêcher.

Enfin, `RateLimitedError` est distincte de `QuotaExceededError` bien que toutes
deux rendent 429. Le quota dit « combien ce mois-ci », la limite dit « à quelle
vitesse ». Les confondre ferait croire à un exploitant que son plan est épuisé
alors qu'il lui suffisait d'attendre dix secondes.
