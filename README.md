# AtlasAgri

**Prévoir. Anticiper. Réacheminer. Décider.**

Plateforme de décision pour les exploitations et les chaînes d'approvisionnement agricoles
marocaines : irrigation, risque agro-climatique, logistique, alternatives, analyse.

> **État : phases 1 à 6 livrées — socle, modèle de données, moteurs d'irrigation et de
> logistique, copilote, interface, analyse en langage naturel.** Les deux parcours fonctionnent de bout en bout, du
> navigateur jusqu'à la politique RLS. Irrigation : ouvrir P03, lire la recommandation en m³
> avec durée et coût, déplier « Pourquoi cette décision ? » et y trouver quatorze étapes
> numérotées avec leur équation FAO. Logistique : ouvrir EXP-1842, voir les trois tronçons de
> l'A7 exposés *au moment où le camion y passe*, et le corridor littoral recommandé avec son
> écart de risque, de coût et de durée face au plan actuel.
>
> **Ce que ce dépôt ne masque pas.** Ni le copilote ni l'agent d'analyse n'ont jamais été
> exécutés contre une clé d'API réelle : leur mécanique est vérifiée contre un fournisseur
> scripté, le jugement du modèle ne l'est pas. Les seuils routiers, les vitesses d'axe, les
> coûts de fret et les pondérations d'arbitrage sont des valeurs de départ que rien n'a encore
> confrontées au terrain — le moteur classe correctement selon elles, il ne dit pas qu'elles
> sont justes.
>
> `docs/ce-qui-nest-pas-mesure.md` dit exactement ce qui a tourné et ce qui est seulement
> écrit.

## Ce dépôt fusionne trois systèmes existants

| Origine | Apport |
|---|---|
| `agriflow-` | Moteur d'irrigation FAO-56 (ET0, ETc, bilan hydrique, dose, scénarios), tables de référence agronomiques, traçabilité par valeur |
| `atlasagri` | Cœur multi-tenant, registre d'outils unique (MCP + agent), nowcasting, moteurs de risque, itinéraires et alternatives, carte opérationnelle |
| `text_to_sql` | Agent d'analyse SQL : pile de validation en couches, récupération de schéma vectorielle, boucle de réparation, harnais d'évaluation |

## Règle qui traverse toute l'architecture

**Les calculs critiques sont déterministes et faits en Python. Le modèle interprète, compare,
explique et communique. Il n'invente jamais un itinéraire, un coût, un délai, un stock, une
mesure ou un score.**

## Structure

```
CLAUDE.md                      règles chargées à chaque session
.claude/skills/                sept compétences d'ingénierie
backend/app/domain/irrigation/ FAO-56 : ET0, ETc, bilan, dose, scénarios
backend/app/domain/logistics/  réseau, exposition dans le temps, coût, alternatives
backend/app/domain/            Python pur — ni SQLAlchemy, ni FastAPI, ni httpx
backend/app/repositories/      accès aux données, borné par organisation
backend/app/services/          orchestration
backend/app/analytics/         socle SQL : validation, catalogue, exécution bornée
backend/app/tools/registry.py   le seul endroit où une capacité est définie
backend/app/agent/             boucle d'agent — manuelle, bornée, tracée
backend/app/mcp_server.py      le second consommateur du même registre
backend/app/api/               HTTP
backend/migrations/            Alembic — schémas, rôles, politiques, vues
frontend/src/components/       les deux panneaux, la carte, les primitives
frontend/src/pages/            un écran par capacité réellement livrée
docs/00-prompt-unification.md  spécification de fusion, phases 0 à 8
docs/00-plan-unification.md    audit des trois dépôts et désaccords argumentés
docs/decisions/                une décision par fichier
docs/ce-qui-nest-pas-mesure.md registre d'honnêteté
```

## Le référentiel agronomique

Dix cultures, quarante stades, sept profils de sol, cinq systèmes d'irrigation. Chaque valeur
porte **la table FAO dont elle vient** — Kc en table 12, longueurs de stades en table 11,
enracinement et fraction d'épuisement en table 22, propriétés hydriques en table 19, réponse du
rendement en FAO-33. Une valeur sans citation n'entre pas.

Quatre des dix cultures n'ont **aucun** Ky publié, et n'en reçoivent donc aucun : pas de valeur
voisine, pas de moyenne de famille, pas de fourchette. La base impose qu'un Ky présent porte sa
source et qu'un Ky absent porte sa raison — le silence est ce que la contrainte interdit.

Le référentiel est global et **surchargeable** : une organisation qui fait analyser un sol pose
sa propre ligne, la voit à côté de la référence, et ne peut pas modifier la référence pour les
autres. Voir `docs/decisions/0007-referentiel-surchargeable.md`.

## Les moteurs déterministes

`backend/app/domain/` est du Python pur : ni SQLAlchemy, ni FastAPI, ni httpx, ni `anthropic`.
Un test l'impose. Le modèle n'y entre jamais — il interprète et explique ce que ces fonctions
produisent, il ne le produit pas.

**Irrigation** — ET0 par Penman-Monteith (FAO-56 éq. 6) avec repli Hargreaves-Samani déclaré,
ETc par Kc de stade, bilan racinaire, dose, durée, coût, scénarios. Sept exemples publiés de la
FAO-56 sont reproduits à l'arrondi près. Sans débit il n'y a pas de durée ; sans Ky documenté il
n'y a aucun chiffre de rendement. Voir `docs/irrigation.md`.

**Risque logistique** — l'exposition est calculée **tronçon par tronçon et dans le temps**. Un
itinéraire n'est pas exposé parce qu'il traverse une région où il pleuvra dans trente heures ; il
l'est si le camion s'y trouve *pendant* la fenêtre. Sans cela le système alerterait sur des
trajets déjà terminés et cesserait d'être cru. Voir `docs/risk-engine.md`.

**Classement multicritère** — pour les domaines qui ont réellement des candidats hétérogènes :
logistique, approvisionnement, stocks. Les contraintes dures filtrent **avant** toute notation,
et les options écartées sortent avec leur motif. L'irrigation n'en fait pas partie : sa décision
est une cascade de seuils sur une grandeur physique, et lui donner des poids inventerait un
arbitrage agronomique que la FAO-56 ne fournit pas
(`docs/decisions/0002-deux-moteurs-un-contrat.md`).

## Le copilote

Une capacité est définie **une fois**, dans `backend/app/tools/registry.py`, et deux
consommateurs la lisent : le pont MCP et la boucle d'agent. Deux définitions divergeraient en
silence.

Quatre garanties portées par la structure, pas par le prompt :

1. **L'organisation n'est jamais un paramètre d'outil.** Elle vient du jeton. Le modèle ne peut
   pas franchir une frontière parce qu'il n'a aucun champ pour l'exprimer — et un test balaie
   tous les outils enregistrés, donc aussi celui qu'on ajoutera demain.
2. **Les sorties sont typées.** Un outil qui renvoie une phrase a déjà perdu le chiffre.
3. **Tout résultat est encapsulé par le registre** avant d'atteindre le modèle : un nom de site
   est du texte qu'un utilisateur a saisi.
4. **Un outil qui échoue ne casse pas la conversation** et rien n'est substitué au résultat
   manquant.

La réponse HTTP porte la **trace d'outils**, avec les résultats structurés. L'interface
reconstruit « Voir les calculs » depuis cette trace, jamais depuis un bloc rédigé par le modèle.
Sans clé d'API, la route répond 503 en français plutôt que de fabriquer une réponse plausible.

Voir `docs/api.md`, `docs/mcp.md` et
`docs/decisions/0010-frontiere-de-verification-du-copilote.md`.

## L'interface

React, TypeScript strict, Tailwind, MapLibre. Français intégral. Voir
`docs/frontend.md`.

Trois règles la tiennent :

**Elle ne calcule rien.** Chaque nombre affiché est lu dans la réponse de l'API. Un chiffre
recalculé pour l'affichage est une seconde version du même chiffre.

**La trace d'outils est la réponse du copilote ; la prose du modèle est un commentaire**, dans
un panneau qui le dit. Un chiffre retapé par le modèle dans une phrase n'a plus de source, et
c'est la disposition de l'écran qui empêche que ce soit celui-là qu'on lise
(`docs/decisions/0012-la-trace-avant-la-prose.md`).

**Ce qui n'existe pas est nommé.** Les capacités non livrées arrivent du serveur et s'affichent
sur la vue générale, sur la carte et sur chaque fiche d'expédition. Il n'y a pas d'écran vide
qui promet, et pas de lien de navigation vers une page à venir.

Les valeurs atteignent le DOM comme du texte : `react/no-danger` est une erreur, et un test
balaie l'arbre source pour `innerHTML`, `insertAdjacentHTML` et `document.write`. Ces tests sont
écrits pour ce dépôt, pas repris de `text_to_sql` — les siens portaient sur une page vanilla que
le portage supprime.

## La couche SaaS

Inscription autonome, plans, quotas, mesure d'usage, premier parcours, console
d'administration et conformité. **Aucun paiement n'est traité** : une organisation qui s'inscrit
démarre sur le plus petit plan, et passer au-dessus est provisionné par un administrateur.

Une organisation créée à l'inscription est **vide** : ni site, ni parcelle, ni donnée fabriquée.
Le référentiel agronomique est global, donc elle le lit immédiatement sans que rien soit copié
pour elle.

Les quotas distinguent un **stock** — combien de parcelles existent maintenant — d'un **flux** —
combien de questions ont été consommées cette période. Les confondre produit deux défauts
opposés et tous deux silencieux (`docs/decisions/0017-stocks-flux-et-evenements.md`). L'usage
s'écrit en **événements**, jamais en compteurs : un compteur entretenu à côté de ses actes
dérive, et la dérive se découvre le jour où un client conteste sa facture.

Les jetons sont **réels**, le coût est **dérivé** d'une grille recopiée, et les deux vivent dans
des colonnes distinctes. Aucun total n'a été rapproché d'une facture, et la page d'abonnement le
dit à côté du chiffre.

Deux seuils de débit : un général par adresse, appliqué avant l'authentification, et un strict
par utilisateur sur le copilote et l'analyse. Il vit en mémoire du processus, ce qui est écrit
là où le module est écrit (`docs/decisions/0019-limiter-le-debit-dans-le-processus.md`).

`docs/conformite.md` traite la loi 09-08 et la CNDP : accès, portabilité, suppression et
traçabilité, chacune avec ce qu'elle ne couvre pas. L'export est **découvert depuis le modèle**,
donc la table ajoutée le mois prochain y figurera le jour de sa création. La suppression d'un
membre pseudonymise sa trace au journal au lieu de l'effacer
(`docs/decisions/0018-effacer-une-personne-sans-effacer-le-journal.md`). Le démarrage en
production est **refusé** tant que la résidence des données n'est pas déclarée.

## L'analyse en langage naturel

Une question devient du SQL, vérifié avant d'atteindre la base. Cinq couches, et **la deuxième
compte le plus : c'est la seule qui ne dépende pas de la correction de ce dépôt.**

Le corpus adverse compte **47 cas, tous conformes**, dont neuf tentatives de franchissement
d'organisation — que le corpus d'origine ne pouvait pas contenir, la multi-location y étant un
non-objectif déclaré. « Conforme » y veut dire plus qu'« attrapé » : chaque cas nomme la couche
censée le refuser, et un cas attrapé par une autre est compté en échec. Passer par accident
n'est pas passer.

Une visée sur une table de base est refusée comme **événement de sécurité**, jamais comme faute
de frappe : `RELATION_NOT_ALLOWED`, jamais réparé, toujours journalisé
(`docs/decisions/0015-la-liste-blanche-de-relations.md`).

```bash
cd backend && python -m app.analytics.evals.run
```

Voir `docs/analytics-sql.md`.

## L'isolation, en une phrase

Les politiques d'isolation vivent sur les tables de base de `app`, avec `FORCE ROW LEVEL
SECURITY` ; `analytics` ne contient que des vues, détenues par un rôle distinct du propriétaire
des tables ; et le rôle analytique n'a aucun privilège sur `app`. Un contournement du validateur
SQL échoue donc au privilège, pas au validateur.

`FORCE` n'est pas une précaution : une vue détenue par le propriétaire de la table, au-dessus
d'une table dont la RLS est seulement `ENABLE`, renvoie **toutes** les organisations — politique
présente, aucune erreur. C'est mesuré, pas supposé : `docs/decisions/0001-isolation-analytique.md`.

## Démarrage

Voir `docs/setup.md`. En bref :

```bash
docker compose up -d db
cd backend && pip install -e ".[dev]"
ATLAS_MIGRATION_DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:5432/atlas alembic upgrade head
python -m app.cli seed-reference && python -m app.cli seed-demo
uvicorn "app.main:create_app" --factory --port 8000

cd ../frontend && npm install && npm run dev
```

## Documentation à produire

Écrites : `setup.md` · `irrigation.md` · `risk-engine.md` · `analytics-sql.md` · `api.md` ·
`mcp.md` · `frontend.md` · `conformite.md`

Restent à produire : `architecture.md` · `ml.md` · `alternatives.md` · `security.md` ·
`deployment.md` · `methodologie-recherche.md` · `calibration-et-fiabilite.md`

En français, et honnête : une capacité non opérationnelle n'est jamais présentée comme
opérationnelle.
