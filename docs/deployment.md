# Déploiement

Ce guide décrit une installation réelle. Ce qui a été **exécuté** ici et ce qui ne l'a pas été
est dit à chaque étape : un runbook qui n'a jamais tourné est une hypothèse, et c'est
exactement le défaut que `text_to_sql` documentait à propos du sien.

## Ce qui a tourné, et sur quoi

La pile `docker compose` a été construite et démarrée, volume vide, jusqu'à une recommandation
d'irrigation servie par l'API du conteneur. La sauvegarde et la restauration ont été exercées
sur cette même base. Ce qui n'a pas tourné — un vrai serveur, un certificat, un
aller-retour Copernicus — est au registre `docs/ce-qui-nest-pas-mesure.md`.

## La pile locale, en une commande

```bash
docker compose up -d --build
```

Quatre services, dans cet ordre : `db`, `migrate`, `seed`, `api`.

* `db` — `postgis/postgis:16-3.4`, sans couche dérivée. Sa sonde interroge le catalogue de la
  base `atlas` plutôt que `pg_isready` : `pg_isready` répond « prêt » pendant `initdb`, sur une
  base temporaire, et les migrations démarraient alors sur une base qui disparaissait ;
* `migrate` — `alembic upgrade head`, puis `set-role-passwords`. La migration crée les rôles
  **sans** mot de passe ; sans la seconde commande, ils n'ouvrent aucune session par TCP ;
* `seed` — référentiel agronomique, organisation de démonstration, puis un compte pour s'y
  connecter. Les trois commandes sont idempotentes : la pile se redémarre sans échouer ;
* `api` — `uvicorn --factory app.main:create_app`. Sa sonde de santé interroge `/api/sante`,
  c'est-à-dire les neuf contrôles d'isolation, et non un port ouvert.

Connexion de démonstration : `agronome@souss-primeurs.ma` / `demo-souss-primeurs-2026`
(modifiable par `ATLAS_DEMO_PASSWORD`). L'organisation porte `is_demo=true` et l'interface
l'annonce sur chaque écran.

### Derrière un mandataire qui inspecte le trafic TLS

`pip` échoue alors sur `CERTIFICATE_VERIFY_FAILED`, sans dire que la cause est le réseau.
Déposez les certificats de l'autorité interne dans `backend/ca/` (voir le LISEZMOI qui s'y
trouve) : ils sont ajoutés au magasin du conteneur avant l'installation des dépendances. Le
dossier est vide par défaut et `.gitignore` refuse les `*.crt` — un certificat versionné est un
certificat que personne ne révoque.

Si le registre d'images est lui aussi filtré, configurez un miroir côté démon
(`/etc/docker/daemon.json`, clé `registry-mirrors`) plutôt que de réécrire les noms d'images
dans le dépôt.

## Une installation réelle

### 1. Rôles et mots de passe

```bash
export ATLAS_MIGRATION_DATABASE_URL=postgresql+asyncpg://admin@db.interne/atlas
alembic upgrade head

export ATLAS_APP_ROLE_PASSWORD="$(openssl rand -base64 32)"
export ATLAS_ANALYTICS_ROLE_PASSWORD="$(openssl rand -base64 32)"
python -m app.cli set-role-passwords
```

Le rôle d'exploitation (`ATLAS_MIGRATION_DATABASE_URL`) crée des schémas, des rôles, des
extensions et lit `app.tenants` sans passer par la politique d'isolation. C'est un rôle
privilégié, et il ne doit **jamais** être celui de l'application : `atlas_app` est
`NOBYPASSRLS`, et une sonde de démarrage le vérifie à chaque lancement.

### 2. Référentiel

```bash
python -m app.cli seed-reference
```

Les valeurs FAO sont globales et ne sont copiées pour personne. `seed-demo` ne s'exécute pas en
production : une organisation de démonstration y serait une base de données de fausses mesures.

### 3. Configuration refusée au démarrage

En production, le processus **ne démarre pas** si l'un de ces points manque :

| Réglage | Pourquoi le démarrage est refusé |
|---|---|
| `ATLAS_JWT_SECRET` | le défaut intégré est public |
| `ATLAS_ANTHROPIC_API_KEY` | annoncé comme fournisseur, absent en fait |
| `ATLAS_DATA_RESIDENCY_COUNTRY` | la page de conformité annonce où vivent les données |
| `ATLAS_REQUIRE_POSTGIS=true` | polygones, itinéraires et statistiques zonales en dépendent |
| `ATLAS_DEMO_MODE=false` | un jeu fabriqué servi comme une mesure |
| DSN applicatif ≠ DSN analytique | deux rôles distincts sont la couche 2 du socle SQL |

C'est un refus, pas un avertissement. Une clé absente découverte à la première requête d'un
client est un incident ; la même découverte au démarrage est un déploiement qui n'a pas eu lieu.

### 4. Premier administrateur

Deux voies, et elles ne se valent pas :

```bash
# Depuis le serveur, sans exposer l'inscription :
ATLAS_NEW_USER_PASSWORD="$(openssl rand -base64 24)" python -m app.cli create-user \
    --email admin@exploitation.ma --name "Nom Prénom" --role ADMIN --tenant-slug mon-organisation
```

ou l'inscription autonome (`POST /api/v1/auth/inscription`), qui crée l'organisation **et** son
administrateur. Elle est limitée à cinq appels par heure et par adresse.

## Journalisation

Hors `local`, chaque ligne est un objet JSON sur une seule ligne — y compris celles d'`uvicorn`
et d'`alembic`, routées par le même formateur. Une installation qui émettait du JSON pour ses
propres évènements et du texte brut pour ses accès HTTP faisait rejeter la moitié de son flux
par un collecteur, et c'était la moitié qui dit quelle requête a été servie.

Chaque requête écrit une ligne `http_request` avec méthode, chemin, statut, durée et
**`run_id`**. Ce même identifiant figure dans les lignes du journal d'audit et dans la trace
d'outils : c'est ce qui permet de rattacher « qui a vu quoi » à la requête qui l'a produit, des
mois plus tard. La chaîne de requête n'est jamais journalisée.

```bash
docker compose logs api | jq -c 'select(.event == "http_request")'
```

## Santé

`GET /api/sante` énumère les neuf sondes plutôt que de les résumer. Un « ok » sans détail
obligerait à lire les journaux pour savoir *ce qui* a été vérifié.

| Statut | Signification | Ce que fait l'orchestrateur |
|---|---|---|
| `ok` | aucune sonde en échec | rien |
| `degrade` | au moins une sonde en échec | en production, le processus n'aurait pas démarré |

La sonde du conteneur `api` interroge cette route. Un port ouvert prouve qu'un processus vit,
pas que le produit est en état de servir.

## Sauvegarde et restauration

```bash
ATLAS_BACKUP_DSN=postgresql://admin@db.interne/atlas ./scripts/backup.sh
```

Deux fichiers sont produits, et les deux sont nécessaires :

* `<horodatage>-roles.sql` — les rôles de la **grappe**, sans mot de passe. Un `pg_dump` seul ne
  les contient pas, et la restauration échouerait sur le premier `GRANT` adressé à un rôle
  inexistant ;
* `<horodatage>-atlas.dump` — la base, format personnalisé.

```bash
./scripts/restore.sh 20260918T142647Z-atlas.dump postgresql://admin@db.interne/atlas_restaure
ATLAS_APP_ROLE_PASSWORD=… python -m app.cli set-role-passwords
```

La restauration **exige une base vide** et refuse de s'exécuter par-dessus un schéma `app`
existant : `pg_restore` ne supprime rien, et restaurer sur des tables peuplées produit des
doublons là où aucune contrainte ne les interdit.

La propriété des objets est restaurée telle quelle, jamais neutralisée par `--no-owner`. Ce
n'est pas une préférence de forme : les tables appartiennent à `atlas_owner`, et c'est cette
propriété qui rend `FORCE ROW LEVEL SECURITY` opérant. Une restauration qui rend les tables au
rôle qui restaure conserve l'isolation sous une forme **décorative**.

Ce cycle a été exercé sur la base de la pile Docker. Après restauration dans une base neuve :
19 tables détenues par `atlas_owner`, 16 tables en `FORCE`, 20 politiques, 8 vues analytiques,
`atlas_app` toujours `NOBYPASSRLS` — et, connecté comme `atlas_app`, une organisation étrangère
voit **0** parcelle là où l'organisation de démonstration en voit 7.

### Ce que la sauvegarde ne couvre pas

Elle ne transporte aucun mot de passe de rôle, par choix. Elle ne couvre pas non plus les
sauvegardes de l'hébergeur : une demande de suppression au titre de la loi 09-08 efface des
lignes en base, et ce que l'hébergeur conserve ailleurs relève d'une procédure à écrire avec
lui (`docs/conformite.md`, et ligne 44 du registre d'honnêteté).

## Ce qui n'est pas outillé

- **Aucune rotation de secrets automatique.** `set-role-passwords` se relance à la main.
- **Aucune purge des journaux d'audit.** La durée de conservation est annoncée, pas appliquée.
- **Aucun envoi de courriel** : ni vérification d'adresse, ni réinitialisation de mot de passe.
- **La limitation de débit vit dans le processus.** Avec plusieurs travailleurs, le seuil
  effectif est multiplié par leur nombre (registre, ligne 40).
- **Aucun terminaison TLS, aucun reverse proxy, aucune configuration systemd** n'est fournie :
  elles dépendent de l'hébergeur, et en inventer une non testée serait pire que son absence.
