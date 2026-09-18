# Conformité — loi 09-08 et CNDP

Ce document dit ce que la plateforme **fait**, ce qu'elle **déclare** et ce qu'elle **ne
couvre pas**. La troisième colonne est la plus importante : une page de conformité qui ne
liste que des cases cochées ne sert à personne, et surtout pas au responsable de traitement
qui doit répondre devant la CNDP.

La loi 09-08 relative à la protection des personnes physiques à l'égard du traitement des
données à caractère personnel s'applique ici à un ensemble restreint : les **comptes
utilisateurs** (nom, courriel, rôle) et le **journal d'audit** qui nomme leurs actes. Les
données agronomiques — parcelles, mesures d'humidité, expéditions — sont des données
d'entreprise, pas des données personnelles ; elles sont néanmoins couvertes par l'export et
par la suppression, parce qu'un client qui part doit pouvoir tout emporter et tout faire
effacer.

## Ce qui est implémenté

| Droit | Où | Ce que cela fait exactement |
|---|---|---|
| Accès | `GET /api/v1/organisation` | Liste les membres, leurs rôles et leur date de création |
| Traçabilité | `GET /api/v1/organisation/journal` | Qui a vu quoi, qui a approuvé quoi, qui a changé quel plan. Réservé à l'administrateur, et **la consultation elle-même y est inscrite** |
| Portabilité | `GET /api/v1/organisation/export` | Copie JSON de toutes les tables portant l'identifiant de l'organisation, secrets d'authentification retirés |
| Suppression d'une personne | `DELETE /api/v1/organisation/membres/{id}` | Supprime le compte et l'entrée d'annuaire, pseudonymise le courriel au journal |
| Suppression de l'organisation | `DELETE /api/v1/organisation` | Efface toutes les lignes de l'organisation, confirmation par recopie de l'identifiant |
| Déclarations | `GET /api/v1/organisation/conformite` | Résidence, hébergeur, numéro CNDP, durée de conservation — tels que l'exploitant les a déclarés |

Deux garanties structurelles soutiennent tout cela :

* **l'isolation entre organisations est tenue par la base**, par une politique de sécurité au
  niveau des lignes, et non par le code applicatif. Une erreur de notre part dans une requête
  ne fait pas franchir la frontière ;
* **l'export est découvert depuis le modèle de données**, table par table. La table ajoutée le
  mois prochain sera exportée le jour de sa création. Une liste écrite à la main serait vraie
  le jour où on l'écrit et fausse ensuite — et un export incomplet présenté comme complet est
  exactement le manquement que la loi vise.

## Ce qui est déclaré, et par qui

La plateforme ne peut pas savoir où tourne sa base de données. Les valeurs suivantes viennent
de la configuration du déploiement, sous la responsabilité de l'exploitant :

| Réglage | Signification |
|---|---|
| `ATLAS_DATA_RESIDENCY_COUNTRY` | Pays d'hébergement effectif |
| `ATLAS_DATA_RESIDENCY_PROVIDER` | Nom de l'hébergeur |
| `ATLAS_CNDP_DECLARATION_NUMBER` | Numéro de la déclaration déposée |
| `ATLAS_AUDIT_RETENTION_DAYS` | Durée de conservation annoncée du journal (défaut : 1 095 jours) |

**Le démarrage en production est refusé** tant que le pays de résidence n'est pas déclaré. Non
renseignés, les autres champs rendent `null` et l'interface affiche « non déclarée ». Aucune
valeur par défaut n'est inventée : afficher « Maroc » parce que le produit est marocain serait
une affirmation de conformité que personne n'a vérifiée.

## Ce qui n'est pas couvert

1. **La conservation est annoncée, pas appliquée.** Aucune tâche ne purge les lignes échues du
   journal d'audit. Le point d'entrée de conformité rend `retention_enforced: false` plutôt que
   de laisser croire le contraire (grand livre, ligne 42).
2. **La suppression d'une personne ne supprime pas le journal.** Le journal est la preuve des
   accès subis par les *autres* membres ; l'effacer au nom du droit de l'un retirerait aux
   autres le leur. Le courriel y est remplacé par un pseudonyme et l'identifiant technique
   subsiste sans rien à quoi le rattacher. C'est une **atténuation**, pas un effacement
   complet, et une personne qui exige davantage doit obtenir la suppression de l'organisation
   entière.
3. **Les sauvegardes échappent à la plateforme.** Le reçu de suppression compte les lignes
   effacées en base, et rien d'autre. Ce que l'hébergeur conserve relève d'une procédure à
   écrire avec lui (ligne 44).
4. **Le chiffrement au repos n'est ni assuré ni vérifié par l'application.** Il relève de
   l'hébergeur.
5. **Aucun registre des sous-traitants n'est tenu** par la plateforme.
6. **L'inscription est ouverte, et aucune mention d'information n'est présentée.**
   `POST /api/v1/auth/inscription` crée une organisation sans intermédiaire (§6). La personne
   qui s'inscrit saisit son nom et son courriel sans qu'aucun texte ne lui dise qui traite ces
   données, pour quelle finalité, ni comment exercer ses droits. C'est le manquement le plus
   visible de cette liste, et il se comble par un texte à rédiger par le responsable de
   traitement — pas par du code. Aucun registre des consentements n'existe non plus.
7. **Aucun courriel n'est envoyé.** La plateforme n'a pas d'acheminement de courrier : un
   administrateur qui ajoute un membre choisit un mot de passe provisoire et le transmet par un
   autre canal. Il n'y a donc ni vérification d'adresse, ni réinitialisation de mot de passe en
   autonomie.
8. **Aucune notification de violation n'est outillée.** La procédure relève de l'organisation
   exploitante.

## Ce que le journal d'audit contient

Une ligne par acte : horodatage, auteur (identifiant et courriel), action, type et identifiant
de la ressource, issue, identifiant de corrélation, et un détail structuré. Sont inscrits, au
minimum : les connexions refusées, la création d'un site ou d'une parcelle, la saisie d'un
relevé, le verdict humain rendu sur une recommandation, le changement de plan, le changement de
rôle, l'export, la suppression d'un membre, la suppression de l'organisation et **toute
consultation du journal**.

Le journal est écrit sur une transaction **séparée** de l'action auditée. Une tentative refusée
laisse donc une trace, et un échec d'écriture du journal ne fait jamais échouer l'action.

## Sécurité

| Mesure | État |
|---|---|
| Isolation entre organisations | Politique de sécurité au niveau des lignes, `FORCE`, vérifiée par neuf sondes au démarrage |
| Rôle analytique | `SELECT` seulement, confiné à des vues, sans accès aux tables de base |
| SQL généré par le modèle | Validé en arbre syntaxique, exécuté en transaction lecture seule avec délais et plafonds |
| Mots de passe | `bcrypt`, jamais exportés |
| Limitation de débit | Seuil général par adresse, seuil strict par utilisateur sur le copilote et l'analyse. **En mémoire du processus** — voir ligne 40 du grand livre |
| Journalisation des accès | Journal d'audit, consultation incluse |
| Chiffrement au repos | Non assuré par l'application |

## Pour le responsable de traitement

Avant toute mise en service réelle :

1. rédiger la mention d'information présentée à l'inscription — la plateforme ne la fournit
   pas, et le formulaire est ouvert ;
2. déclarer le traitement auprès de la CNDP et renseigner le numéro obtenu ;
3. renseigner le pays et l'hébergeur — sans quoi la plateforme refuse de démarrer en
   production ;
4. écrire avec l'hébergeur la procédure d'effacement couvrant les sauvegardes ;
5. décider si la durée de conservation annoncée doit être appliquée par une purge, et la
   planifier.
