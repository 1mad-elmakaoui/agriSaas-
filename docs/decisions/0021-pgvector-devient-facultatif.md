# 0021 — Rendre `pgvector` facultatif, et supprimer l'image dérivée

**Date** : 2026-09-18  ·  **État** : Acceptée  ·  **Amende la décision 0016**

## Contexte

La phase 1 a construit une image PostgreSQL dérivée parce que le produit fusionné
avait besoin de **PostGIS et pgvector dans le même serveur** : `postgis/postgis`
n'a pas le second, `pgvector/pgvector` n'a pas le premier. C'était la bonne
décision à ce moment-là, et pour la bonne raison — développer les phases 2 à 7
sur une base qui ne serait jamais celle de production, c'est découvrir
l'extension manquante à la fin.

La décision 0016 a ensuite retiré la récupération vectorielle : six vues et
soixante-quinze colonnes tiennent dans une invite, et un index vectoriel y
ajoutait un mode d'échec silencieux pour choisir six vues parmi six. Elle
concluait : *« pgvector est déjà installé et une sonde de démarrage le vérifie ;
rien n'est à défaire. »*

La phase 8 a démarré la pile pour de bon, et cette phrase ne tenait plus. Trois
constats, dans l'ordre où ils sont apparus :

1. **plus rien n'utilise l'extension.** Aucun code, aucune migration, aucune
   table. Seule une sonde la cherche ;
2. **la sonde mentait.** Absente, elle annonçait « l'agent d'analyse est
   indisponible jusqu'à son installation ». L'agent d'analyse fonctionne sans
   elle, et quatre cents tests le montrent ;
3. **la couche dérivée coûte un `apt-get`**, c'est-à-dire un dépôt Debian
   joignable au moment de la construction. Dans l'environnement de développement
   de ce dépôt, `deb.debian.org` et `apt.postgresql.org` sont refusés par la
   politique réseau : l'image ne se construisait pas, et la ligne 8 du registre
   d'honnêteté attendait depuis la phase 1.

## Options

**A — Garder l'image dérivée.** Le besoin qu'elle sert n'existe plus. Elle reste
invérifiable ici, donc la pile reste non démarrée, donc la phase 8 ne peut pas
tenir sa promesse.

**B — Prendre une image qui porte les deux.** `timescale/timescaledb-ha:pg16` a
PostGIS et pgvector. Elle pèse **4,33 Go** contre 853 Mo, et embarque un moteur
de séries temporelles, `vectorscale` et `h3` dont rien ici ne se sert. Payer 3,5
Go pour une extension qu'aucune ligne de code n'appelle est un mauvais échange.

**C — Rendre l'extension facultative.** `postgis/postgis:16-3.4` telle quelle,
la migration crée `vector` **si le serveur la propose**, et la sonde constate au
lieu de prédire.

## Décision

**C.**

L'extension n'est pas retirée pour autant : une installation qui l'a déjà n'a
aucune raison de la perdre, et la décision 0016 nomme le jour où elle
redeviendra nécessaire — celui où une organisation pourra définir son propre
catalogue, où l'index cessera d'être commun et devra être borné par organisation.
Ce jour-là, la migration qui crée l'index créera l'extension.

La sonde, elle, dit désormais ce qui est : présente ou absente, et dans les deux
cas « aucune capacité livrée n'en dépend ». Une sonde `DEGRADED` qui ne dégrade
rien coûte plus qu'elle ne rapporte — le jour où une sonde crie pour rien, on
cesse de lire les sondes.

## Conséquence

`docker/postgres.Dockerfile` disparaît. La pile n'a plus aucune image à
construire pour sa base, ce qui la rend démarrable partout, y compris derrière un
réseau qui filtre les dépôts de paquets.

Un défaut est apparu en chemin et méritait d'être corrigé de toute façon : les
deux extensions étaient créées dans un `contextlib.suppress(DBAPIError)`.
L'exception était bien attrapée, mais un `CREATE EXTENSION` qui échoue **avorte
la transaction**, et tout le reste de la migration échouait ensuite sur
« current transaction is aborted ». Le même piège guettait PostGIS sur une
installation qui ne l'a pas. Les deux sont désormais demandées à
`pg_available_extensions` avant d'être créées.

C'est exactement ce que la phase 8 devait produire : non pas un fichier
plausible, mais les défauts qu'un fichier jamais exécuté conserve.
