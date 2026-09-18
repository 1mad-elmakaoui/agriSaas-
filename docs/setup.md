# Installation

## Ce qui est nécessaire

- Python 3.11
- PostgreSQL 16, avec **PostGIS** et **pgvector**

Les deux extensions sont requises ensemble et aucune image publique ne les fournit toutes les
deux : `postgis/postgis` n'a pas pgvector, `pgvector/pgvector` n'a pas PostGIS.
`docker/postgres.Dockerfile` construit l'image dérivée.

## Base de données

```bash
docker compose up -d db          # ou un PostgreSQL 16 local
```

Sur une installation Debian/Ubuntu locale :

```bash
apt-get install postgresql-16 postgresql-16-postgis-3 postgresql-16-pgvector
createdb atlas
```

## Migrations

Elles tournent avec un rôle d'administration : elles créent des schémas, des rôles, des
politiques et des vues, ce que le rôle applicatif n'a pas le droit de faire — et ne doit pas
l'avoir.

```bash
cd backend
export ATLAS_MIGRATION_DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:5432/atlas
alembic upgrade head
```

La migration `0001_core` crée quatre rôles :

| Rôle | Ce qu'il peut faire |
|---|---|
| `atlas_owner` | propriétaire des tables ; ni superutilisateur, ni `BYPASSRLS` |
| `atlas_app` | CRUD sur `app`, **sous** politique d'isolation |
| `atlas_analytics_owner` | propriétaire des vues de `analytics` ; lit les tables de base |
| `atlas_analytics_ro` | `SELECT` sur les vues, et rien d'autre — pas même `USAGE` sur `app` |

En local, l'authentification `trust` suffit. Ailleurs, donnez-leur un mot de passe :

```bash
psql -d atlas -c "ALTER ROLE atlas_app LOGIN PASSWORD '…'"
psql -d atlas -c "ALTER ROLE atlas_analytics_ro LOGIN PASSWORD '…'"
```

## Application

```bash
cd backend
pip install -e ".[dev]"
cp .env.example .env          # puis renseigner les DSN
uvicorn app.main:app --reload
```

Documentation interactive : <http://127.0.0.1:8000/api/docs>.
État du service et résultat des sondes : <http://127.0.0.1:8000/api/sante>.

## Sondes de démarrage

Neuf sondes tournent avant que la première requête ne soit acceptée. En production, un échec
**arrête le processus** ; en développement, il est journalisé en erreur et le démarrage continue.

| Sonde | Ce qu'elle empêche |
|---|---|
| `no_bypassrls` | un rôle qui contourne la RLS rendrait toutes les politiques décoratives |
| `rls_coverage` | une table portant `tenant_id` sans `ENABLE` **et** `FORCE` et une politique |
| `cross_tenant` | une isolation configurée mais fonctionnellement fausse |
| `analytics_view_chain` | une vue `security_invoker`, ou détenue par un rôle qui contourne la RLS |
| `analytics_read_only` | un DSN analytique pointant sur un rôle qui peut écrire |
| `analytics_confinement` | un `GRANT` de trop rendant les tables de base atteignables |
| `no_materialized_views` | une matview dans `analytics` : PostgreSQL n'y attache aucune politique |
| `postgis` | requis en production ; dégrade ailleurs |
| `pgvector` | requis par l'index de schéma de l'agent d'analyse |

`cross_tenant` prouve les deux sens : la ligne de l'organisation B **est** visible en tant que B,
puis **ne l'est pas** en tant que A. Sans le premier temps, elle passerait sur une base vide en
démontrant l'absence de données plutôt que la présence d'une frontière.

## Tests

```bash
cd backend
export ATLAS_TEST_DATABASE_URL=postgresql+asyncpg://atlas_app@127.0.0.1:5432/atlas
export ATLAS_TEST_ANALYTICS_DATABASE_URL=postgresql+asyncpg://atlas_analytics_ro@127.0.0.1:5432/atlas
export ATLAS_TEST_OWNER_DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:5432/atlas
pytest -q
```

La suite tourne contre un **vrai PostgreSQL**, jamais SQLite : politiques, `FORCE`, propriété des
vues et privilèges de rôle n'existent nulle part ailleurs. Sans DSN, elle **échoue** au lieu
d'être sautée — un test d'isolation silencieusement sauté est indiscernable d'un test qui passe.

```bash
ruff check .
mypy app
```

## Référentiel et démonstration

Deux commandes, deux niveaux de privilège — et la séparation n'est pas cosmétique.

```bash
cd backend
python -m app.cli seed-reference   # lignes globales : rôle d'administration
python -m app.cli seed-demo        # données d'organisation : sous politique
```

`seed-reference` charge le référentiel FAO. Ses lignes portent `tenant_id IS NULL` : aucune
organisation ne peut les écrire, donc la commande prend le rôle `atlas_owner`, pour lequel une
politique d'administration est déclarée. C'est une opération d'exploitation, au même titre
qu'une migration.

`seed-demo` écrit de la donnée d'organisation ordinaire, sous politique, exactement comme une
saisie. Chaque valeur porte `(SIMULATED, SEED_DEMO)` et pèse 0,30 dans le score de fiabilité :
un tenant de démonstration **ne peut pas** afficher « Fiabilité : Élevée ».

### Le référentiel se surcharge, il ne se modifie pas

Une organisation qui fait analyser un sol pose sa propre ligne portant le même `code` et
`is_measured = true`. Elle voit alors les deux — la référence FAO et sa mesure — ce qui permet
d'afficher l'écart. Les autres organisations ne voient que la référence, et personne ne peut
réécrire la ligne globale par la voie applicative.

### Régénérer le référentiel

```bash
python scripts/build_reference.py
```

Le script transforme les fichiers d'`agriflow` sans toucher à aucune valeur numérique. Il est
là parce que recopier un tableau de Kc à la main est précisément l'endroit où une valeur fausse
mais plausible entre — et qu'aucun test en aval ne la rattraperait.

## Le copilote

```bash
# Facultatif. Sans clé, la route /api/v1/copilote répond 503 en français et le
# reste de l'application fonctionne normalement.
export ATLAS_ANTHROPIC_API_KEY=sk-ant-…
export ATLAS_LLM_MODEL=claude-opus-5
```

Aucune réponse n'est fabriquée localement en l'absence de clé : une réponse plausible produite
sans modèle donnerait une fausse idée de ce que le produit sait faire.

**Rien de tout cela n'a été exécuté dans cet environnement.** Aucun appel n'a quitté la machine,
ni vers Anthropic, ni vers Open-Meteo. La boucle est vérifiée hors ligne contre un fournisseur
scripté ; ce que cela couvre et ce que cela ne couvre pas est écrit dans
`decisions/0010-frontiere-de-verification-du-copilote.md` et compté aux lignes 20 à 24 de
`ce-qui-nest-pas-mesure.md`.

Une organisation marquée `is_demo` reçoit toujours la météo hors ligne, entièrement simulée,
quelle que soit la configuration : `decisions/0011-la-meteo-suit-l-organisation.md`.

Voir `api.md` pour la forme des réponses et `mcp.md` pour le pont MCP.

## L'interface

```bash
cd frontend
npm install
npm run dev      # http://127.0.0.1:5173
```

Le serveur de développement mandate `/api` vers `127.0.0.1:8000`, donc l'API doit tourner :

```bash
cd backend
uvicorn "app.main:create_app" --factory --port 8000
```

`--factory` n'est pas optionnel : `app.main` expose `create_app(settings)` et non un objet
`app`, pour qu'une seconde instance puisse être construite avec une autre configuration sans
que la première la voie changer.

Voir `frontend.md` pour ce que l'interface fait et ne fait pas.

### Un compte pour ouvrir la démonstration

`seed-demo` crée l'organisation et ses données, pas d'utilisateur — un mot de passe par défaut
dans un dépôt finit toujours par se retrouver sur une installation réelle. Provisionnez-en un :

```bash
cd backend
python - <<'PY'
import asyncio
from app.core.config import get_settings
from app.db.session import Databases
from app.db.seed import DEMO_TENANT_ID
from app.domain.enums import UserRole
from app.services.auth_service import AuthService

async def main():
    settings = get_settings()
    dbs = Databases(settings)
    service = AuthService(dbs, jwt_secret=settings.jwt_secret, ttl_minutes=480)
    await service.provision_user(
        tenant_id=DEMO_TENANT_ID, email="agronome@example.ma",
        full_name="Agronome", password="choisissez-un-mot-de-passe",
        role=UserRole.AGRONOME,
    )
    await dbs.dispose()

asyncio.run(main())
PY
```

### Le jeu de démonstration vieillit

`seed-demo` fige ses dates au moment du semis. Quelques semaines plus tard, l'expédition affiche
« échéance dépassée » et aucune n'est en transit. Re-exécutez `seed-demo` sur une organisation
neuve avant une démonstration. L'irrigation, elle, recalcule à la météo du jour et ne vieillit
pas.
