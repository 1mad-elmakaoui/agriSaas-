# API HTTP

Ce document décrit ce qui existe ; ce qui n'existe pas n'y figure pas, même quand la
spécification le prévoit.

Préfixe : `/api/v1`. Documentation interactive : `/api/docs`. Schéma : `/api/openapi.json`.

| Méthode | Chemin | Rôle |
|---|---|---|
| `POST` | `/api/v1/auth/login` | Ouvrir une session |
| `POST` | `/api/v1/auth/inscription` | Créer une organisation |
| `GET` | `/api/v1/auth/moi` | Utilisateur connecté |
| `GET` | `/api/v1/sites` | Lister les sites |
| `POST` | `/api/v1/sites` | Créer un site |
| `GET` | `/api/v1/sites/{site_id}` | Fiche d'un site |
| `POST` | `/api/v1/copilote` | Poser une question au copilote |
| `POST` | `/api/v1/analyse` | Interroger les données en langage naturel |
| `GET` | `/api/v1/recommandations` | Lister les recommandations archivées |
| `POST` | `/api/v1/recommandations` | Enregistrer une proposition |
| `GET` | `/api/v1/recommandations/decompte` | Décompte des verdicts |
| `POST` | `/api/v1/recommandations/{id}/verdict` | Rendre un verdict |
| `GET` | `/api/v1/parcelles` | Lister les parcelles |
| `POST` | `/api/v1/parcelles` | Créer une parcelle |
| `POST` | `/api/v1/parcelles/{code}/humidite` | Saisir un relevé d'humidité |
| `GET` | `/api/v1/parcelles/{code}/irrigation` | Avis d'irrigation |
| `GET` | `/api/v1/demarrage` | Où en est le premier parcours |
| `GET` | `/api/v1/demarrage/choix` | Valeurs proposées par le catalogue |
| `GET` | `/api/v1/abonnement` | Plan, plafonds et consommation |
| `POST` | `/api/v1/abonnement/plan` | Changer de plan (administrateur) |
| `GET` | `/api/v1/organisation` | Organisation et ses membres |
| `POST` | `/api/v1/organisation/membres` | Ajouter un membre (administrateur) |
| `POST` | `/api/v1/organisation/membres/{id}/role` | Changer un rôle (administrateur) |
| `DELETE` | `/api/v1/organisation/membres/{id}` | Supprimer un membre (administrateur) |
| `GET` | `/api/v1/organisation/journal` | Journal d'audit (administrateur) |
| `GET` | `/api/v1/organisation/export` | Exporter toutes les données (administrateur) |
| `DELETE` | `/api/v1/organisation` | Supprimer l'organisation (administrateur) |
| `GET` | `/api/v1/organisation/conformite` | Résidence, conservation et limites |
| `GET` | `/api/sante` | État du service et de ses sondes |

## L'organisation n'est jamais un paramètre

Elle est lue dans le jeton signé, et nulle part ailleurs. Il n'existe aucun en-tête, aucun
paramètre de requête, aucun champ de corps et aucun argument d'outil par lequel en désigner une
autre. C'est ce qui rend la garantie structurelle plutôt que réglementaire : le modèle de
langage ne peut pas franchir une frontière d'organisation parce qu'il n'a aucun moyen d'exprimer
la demande.

## Toute valeur arrive avec sa provenance

Aucune réponse ne porte un nombre nu. Chaque valeur affichable est accompagnée de son état
(`OBSERVED`, `FORECAST`, `DERIVED`, `INFERRED`, `SIMULATED`), de son origine (`SENSOR`,
`MANUAL_ENTRY`, `EXTERNAL_API`, `REFERENCE_TABLE`, `MODEL`, `SEED_DEMO`) et de leurs libellés
français. Une fiche saisie par un exploitant et une fiche de démonstration sont indiscernables
sans cette paire ; c'est précisément pourquoi elle est obligatoire dans le schéma.

## Les erreurs sont en français et disent quoi faire

```json
{
  "code": "not_found",
  "message_fr": "Expédition « EXP-0000 » introuvable.",
  "remedy_fr": "Vérifiez la référence dans la page Expéditions.",
  "run_id": "db8bb21dacc64e0e97441c85e47dae8b"
}
```

Jamais de trace technique. L'exploitant reçoit ce qu'il faut faire ; l'opérateur retrouve le
détail par le `run_id`, également renvoyé dans l'en-tête `X-Run-Id` de chaque réponse.

Un échec d'authentification ne distingue pas ses causes : courriel inconnu et mot de passe faux
produisent exactement la même réponse, sinon l'API énumère les comptes existants.

## `POST /api/v1/copilote`

```json
{ "question": "Est-ce que je dois irriguer P03 ?" }
```

La réponse n'est **jamais** une prose seule :

```json
{
  "text_fr": "…",
  "tool_calls": [
    {
      "name": "calculate_irrigation_requirement",
      "arguments": { "field_code": "P03" },
      "succeeded": true,
      "result": { "recommendation": "IRRIGATE", "water_volume_m3": 84.2, "inputs": [ … ] },
      "error_fr": null
    }
  ],
  "iterations": 2,
  "truncated": false,
  "stop_reason": "end_turn",
  "model": "claude-opus-5",
  "prompt_version": "2026-08-25.1",
  "run_id": "…",
  "cost_usd": 0.0421,
  "cost_label_fr": "0.0421 USD"
}
```

`tool_calls` porte le **résultat structuré**, pas seulement le fait qu'un appel a eu lieu.
L'interface reconstruit la carte de décision — « Voir les calculs », « Sources et preuves » —
depuis cette trace, jamais depuis un bloc rédigé par le modèle
(`decisions/0005-carte-de-decision.md`). Un chiffre que le modèle aurait retapé dans sa prose
sans qu'il figure ici n'a aucune source : c'est ce que la trace rend visible.

Trois comportements à connaître :

- **Sans clé d'API**, la route répond `503` avec un message français. Elle ne fabrique pas de
  réponse plausible : une démonstration locale donnerait une fausse idée de ce que le produit
  sait faire.
- **`truncated: true`** signale que la borne d'itérations a été atteinte. Les résultats
  intermédiaires restent dans `tool_calls` ; la réponse ne doit pas être présentée comme
  complète.
- **`cost_usd` vaut `null`** dès qu'un seul appel n'est pas tarifé — jamais un total partiel
  présenté comme complet.

## `GET /api/v1/expeditions/{reference}/risque`

L'exposition tronçon par tronçon, les options classées, et celles qui ont été écartées.

Trois choses que cette réponse fait et qu'il faut connaître :

- **`disruption_indicator` ne voyage jamais seul.** `disruption_caveat_fr` l'accompagne dans la
  même charge utile, parce qu'il n'existe aucun chemin par lequel le chiffre devrait arriver sans
  sa réserve. Ce n'est pas une probabilité : rien ne l'a calibré sur un historique d'incidents.
- **Une option écartée porte `null` sur chaque chiffre** — coût, durée, marge, niveau de risque —
  et ses motifs dans `rejection_reasons_fr`. Les renseigner la ferait figurer dans un tableau
  comparatif comme un choix possible.
- **Les écarts sont relatifs au plan actuel** (`cost_delta_mad`, `duration_delta_hours`,
  `risk_delta`) et calculés par le serveur. L'interface ne les recalcule pas.

Chaque tronçon exposé porte `hours_from_departure` et `entry_at` : c'est ce qui distingue
« la route sera coupée » de « le camion y sera au mauvais moment ».

Voir `risk-engine.md`.

## `POST /api/v1/analyse`

```json
{ "question": "Quelle est la surface totale par culture ?" }
```

**Aucune route n'accepte de SQL.** Celle-ci accepte une question ; le SQL est produit, validé,
planifié et exécuté à l'intérieur, par le seul chemin d'exécution du produit.

La réponse porte les lignes, la requête retenue, et **la trace de chaque tentative avec son
SQL** — y compris celles qui ont échoué. Un exploitant qui ne voit qu'« impossible de répondre »
n'a rien à contester ; celui qui voit la requête peut dire « la colonne que tu cherches
s'appelle autrement ».

Trois comportements à connaître :

- **`refused: true`** signale une requête que la plateforme a refusé d'exécuter — une visée sur
  une table de base, une écriture. Aucune donnée n'a été lue, et il n'y a eu **aucune tentative
  de réparation** : un refus de sécurité est un événement à signaler, pas une syntaxe à
  corriger.
- **`truncated: true`** couvre aussi le cas où la `LIMIT` injectée a été atteinte exactement.
  « Exactement N lignes, où N est le plafond » est indiscernable de « il y en avait davantage ».
- **`row_count: 0` sans refus** est un résultat, pas une erreur : la requête a tourné et rien ne
  correspond.

Voir `analytics-sql.md`.

## Les recommandations

Le moteur propose, une personne dispose. Rien ici n'exécute une irrigation ni ne déplace une
expédition : le verdict **consigne** ce qu'un responsable a décidé, il ne le fait pas à sa place.

Trois règles portées par le serveur :

- **Le client envoie un sujet, jamais une décision.** `POST /recommandations` prend un domaine et
  un code de parcelle ou une référence d'expédition ; la décision est recalculée par le moteur au
  moment de l'enregistrement. Accepter une décision venue du navigateur laisserait archiver
  n'importe quel chiffre sous le nom du moteur.
- **Un verdict se rend une fois.** Un second appel reçoit une erreur, pas une réécriture : une
  décision qui change se consigne comme une nouvelle recommandation, pour que la trace de la
  première subsiste. `PENDING` n'est pas accepté en entrée — c'est l'état initial, pas une
  décision.
- **Un verdict rendu porte qui et quand**, et une contrainte de table le garantit. « Acceptée »
  sans décideur ni horodatage est exactement la ligne qu'un audit cherche.

`acceptance_rate` vaut `null` tant que rien n'a été décidé : 0 % sur zéro décision se lirait
comme « tout est refusé ».

## Quotas et limitation de débit

Deux refus rendent `429`, et ils ne veulent pas dire la même chose.

| Code | Sens | Ce qu'il faut faire |
|---|---|---|
| `quota_exceeded` | Le plan est épuisé pour la période ou pour le stock | Attendre le mois prochain, libérer une entrée, ou demander un plan supérieur |
| `rate_limited` | Trop de requêtes, trop vite | Attendre : l'en-tête `Retry-After` dit combien de secondes |

Les quotas sont vérifiés **avant** l'acte et enregistrés **après** : un appel qui a échoué n'a
pas été consommé. Le message nomme toujours le plafond, le chiffre, l'unité et le plan —
« quota atteint » sans le nom du plan n'apprend rien à quelqu'un qui ignore lequel il a.

Le seuil de débit du copilote et de l'analyse est **plus strict** que le seuil général : ces
appels coûtent plusieurs appels de modèle, des secondes et de l'argent. Le seuil général vaut
par adresse cliente et s'applique avant l'authentification ; celui de l'agent vaut par
utilisateur. Il vit en mémoire du processus : voir la ligne 40 de
`docs/ce-qui-nest-pas-mesure.md`.

## `GET /api/v1/abonnement`

Rend le plan, les plans disponibles et **cinq jauges**, chacune avec son dénominateur :
parcelles, expéditions, questions au copilote, questions d'analyse, dépense du modèle.

`limit: null` signifie **illimité**, jamais zéro. `fraction` vaut `null` quand il n'y a pas de
plafond : une jauge sans dénominateur ne se dessine pas.

`spend_is_partial` passe à `true` dès qu'un appel du mois n'est pas tarifé ; la dépense
affichée est alors un **minorant**, et `spend_notice_fr` le dit. Elle reste de toute façon
dérivée d'une grille tarifaire recopiée, jamais rapprochée d'une facture.

Aucun paiement n'est traité : un plan est **provisionné** par un administrateur.

## `POST /api/v1/auth/inscription`

Inscription autonome. L'organisation créée est **vide** — ni site, ni parcelle, ni donnée
fabriquée — et démarre sur le plan `COOPERATIVE`, le plus petit : provisionner mieux
reviendrait à vendre sans qu'un administrateur l'ait décidé. Le compte créé est `ADMIN`, seul de
son organisation. La session est ouverte immédiatement.

Le référentiel agronomique est global : une organisation neuve lit les cultures et les sols FAO
sans que rien soit copié pour elle.

Cette route est limitée à cinq appels par heure et par adresse, bien en dessous du seuil
général : un appel réussi crée une organisation.

Un courriel déjà utilisé est refusé **en le disant**. C'est une énumération de comptes, assumée :
un formulaire d'inscription qui échoue en silence est inutilisable. `/auth/login`, lui, continue
de ne jamais distinguer ses causes d'échec.

`POST /api/v1/organisation/membres` crée un compte dans l'organisation. **Aucun courriel n'est
envoyé** : l'administrateur choisit un mot de passe provisoire et le transmet par un autre canal.

## Le premier parcours

`GET /api/v1/demarrage` rend les étapes — site, parcelle, relevé, avis — avec pour chacune si
elle est franchie et, sinon, ce qu'il faut faire. Les étapes viennent du serveur pour la même
raison que les capacités non livrées : le jour où une étape disparaît, l'interface cesse de la
demander sans qu'on la modifie.

`GET /api/v1/demarrage/choix` rend les cultures, sols, systèmes d'irrigation et sites du
catalogue de l'organisation. Une ligne posée par l'organisation porte `is_local: true` et
masque la ligne globale de même code.

À la création d'une parcelle ou d'un relevé, **le serveur décide de la provenance** :
`OBSERVED` / saisie manuelle. Les schémas d'entrée refusent tout champ inconnu, donc un client
qui tenterait de déclarer « sonde » reçoit une `422` plutôt qu'un silence.

## Conformité et journal d'audit

`GET /api/v1/organisation/export` rend une copie JSON de toutes les tables portant
l'identifiant de l'organisation. Les tables sont **découvertes depuis le modèle** : celle qu'on
ajoutera se retrouve dans l'export le jour de sa création. Les condensats de mots de passe en
sont retirés et le fichier le dit lui-même, dans `redacted_columns`.

`GET /api/v1/organisation/journal` rend le journal d'audit. **Cette consultation est
elle-même inscrite au journal** : un registre qu'on peut lire sans laisser de trace ne prouve
plus rien.

`DELETE /api/v1/organisation/membres/{id}` supprime un compte et pseudonymise son courriel au
journal — décision 0018. `DELETE /api/v1/organisation` efface tout, après recopie de
l'identifiant lisible de l'organisation.

`GET /api/v1/organisation/conformite` rend ce que l'exploitant a **déclaré** — résidence,
hébergeur, numéro CNDP, durée de conservation — et la liste de ce que la plateforme ne couvre
pas. Non renseignés, ces champs rendent `null` : aucune valeur plausible n'est inventée.

## `GET /api/sante`

Énumère les sondes plutôt que de les résumer. Un « ok » sans détail obligerait à lire les
journaux pour savoir *ce qui* a été vérifié ; l'exploitant doit pouvoir constater que
l'isolation a été contrôlée au démarrage, sonde par sonde.

## Ce qui n'existe pas encore

Stocks, fournisseurs, alertes et analyse en langage naturel de l'entrepôt de données ne sont pas
exposés : les capacités correspondantes ne sont pas livrées. La vue générale les énumère, depuis
le serveur, pour que l'interface n'ait pas à les connaître.

`docs/ce-qui-nest-pas-mesure.md` tient le compte de ce qui est écrit et de ce qui a réellement
tourné.
