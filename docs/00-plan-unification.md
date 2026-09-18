# Plan d'unification — Phase 0

**Date** : 2026-08-24 · **État** : soumis à revue · **Auteur** : session Claude Code
**Portée** : audit des trois dépôts sources, avant toute écriture de code produit.

Ce document répond aux sept points exigés par `docs/00-prompt-unification.md` §2. Conformément
à la demande, le poids est mis sur la **carte des duplications** (§2) et sur les **désaccords**
(§7). L'inventaire (§1) est volontairement dense.

---

## 0. Ce qui a été lu, et ce qui a été exécuté

Les trois dépôts ont été clonés et lus intégralement sur les branches que la spécification
désigne :

| Dépôt | Branche lue | Commit | Fichiers | Python |
|---|---|---|---|---|
| `agriflow-` | `claude/agriflow-backend-startup-fobs8u` | `980bff7` | 129 | 13 204 l. |
| `atlasagri` | `claude/start-175noj` | `2747fbf` | 140 | 14 577 l. |
| `text_to_sql` | `claude/text-to-sql-agent-qv7v1u` (défaut) | `17301f7` | 129 | 19 253 l. |

La branche `main` d'`agriflow-` ne contient qu'un `CLAUDE.md` ; celle d'`atlasagri` un
`CLAUDE.md` et douze compétences. Tout le code vit sur les branches ci-dessus. Les clones sont
accessibles sous `.sources/` (liens symboliques, non versionnés).

`atlasagri` porte douze compétences qui lui sont propres (`agricultural-risk`,
`alternative-optimization`, `security-multitenancy`, `mcp-engineering`…). Elles recouvrent
partiellement les sept compétences de ce dépôt sans leur être identiques : leur réconciliation
est un travail de phase 1, à faire explicitement plutôt que par écrasement — une compétence
périmée est pire qu'aucune.

**Ce qui a été réellement exécuté.** Un serveur PostgreSQL 16.13 a été démarré localement pour
vérifier la conception d'isolation de la §4.3 plutôt que de la raisonner. Les sondes A à N
citées en §7.1 ont été exécutées et leurs sorties sont reproduites telles quelles.

**Ce qui n'a pas été exécuté** : aucune suite de tests des trois dépôts (dépendances non
installées dans cet environnement), aucun appel réseau, aucune construction d'image. Aucun
chiffre de performance, de couverture ou de précision n'est repris d'un README source comme
s'il avait été revérifié ici.

---

## 1. Inventaire

Classification : **G** garder tel quel · **G+** garder avec modifications · **→X** absorbé
par X · **✗** supprimé.

### 1.1 `agriflow-` — moteur d'irrigation

| Module | l. | Sort | Motif |
|---|---|---|---|
| `irrigation/et0.py` | 398 | **G** | Penman-Monteith FAO-56 + repli Hargreaves-Samani. Le code le mieux validé des trois dépôts (5 exemples publiés reproduits). Ne pas toucher. |
| `irrigation/water_balance.py` | 506 | **G** | Bilan racinaire, TAW/RAW/Dr/Ks, pluie efficace, `project_depletion`. Pur. |
| `irrigation/crop_water.py` | 288 | **G** | Kc par stade, estimation de stade depuis la date de plantation, marquée comme estimation. |
| `irrigation/irrigation.py` | 360 | **G+** | Dose, durée, coût. Ajouter le plafond de quota (cf. §7.2). |
| `irrigation/scenarios.py` | 299 | **G** | Simulateur −10/−20/−30 %. **Ne pas fusionner** dans le moteur d'alternatives (§7.2). |
| `irrigation/decision_engine.py` | 789 | **G+** | Orchestrateur pur. Renommer `IrrigationEngine` : « DecisionEngine » sera pris par le contrat partagé. |
| `irrigation/constants.py` | 211 | **G+** | Tous les seuils au même endroit, chacun sourcé. Modèle à généraliser. |
| `irrigation/explain.py` | 217 | **→ services/explanation** | Fusionne avec `atlasagri.domain.provenance` (§2.2). |
| `weather/`, `services/weather_service.py` | 854 | **G+ / →adapters** | Cache PostgreSQL conservé, abstraction remplacée par celle d'`atlasagri` (§2.3). |
| `sensors/`, `satellite/` | 395 | **G** | `SoilMoistureProvider` / `SatelliteProvider`. C'est l'interface derrière laquelle SWEB arrivera (§16.3). |
| `services/geometry.py` | 191 | **G** | Excès sphérique WGS84, test d'accord 0,3 % contre PostGIS. |
| `migrations/` (Alembic) | 3 rév. | **G+** | **Le seul cadre de migration des trois dépôts.** Fondation retenue (§7.3). |
| `models/`, `schemas/` | 831 | **G+** | Absorbés dans le modèle fusionné (§3). `farms` → `Site(type=farm)`. |
| `agents/tools.py` (19 outils) | 780 | **→ tools/registry** | Ré-enregistrés dans le registre unique (§4). |
| `agents/agent.py` | 347 | **→ agent/service** | La boucle gagne sur deux points précis (§2.5). |
| `api/routes/` | 830 | **G+** | Re-préfixés, passés sous `RequestContext`. |
| `frontend/` (Leaflet) | 22 f. | **G+ / ✗carte** | Panneau d'explication conservé, `FieldMap.tsx` supprimé (MapLibre gagne). |
| `data/seed/*.json` | 4 f. | **G** | Tables FAO-56/FAO-33 avec citation de table par ligne. Actif rare. |

### 1.2 `atlasagri` — cœur multi-tenant, risque, logistique

| Module | l. | Sort | Motif |
|---|---|---|---|
| `tools/registry.py` | 247 | **G+** | Registre unique, tenant injecté, audit, erreurs françaises. Manque le schéma de sortie typé (§7.5). |
| `tools/business_tools.py` (18 outils) | 1 108 | **G+** | Base du registre fusionné. |
| `core/security.py` | 112 | **G** | `RequestContext`, JWT, bcrypt avec pré-condensé SHA-256. |
| `db/repositories.py` | 177 | **G+** | `TenantRepository` sans échappatoire. Conservé, doublé par RLS (§7.1). |
| `db/base.py` | 426 | **G+** | Modèle multi-tenant. **À reconstruire sous Alembic** (§7.3). |
| `db/session.py` | 78 | **✗** | `create_all()` synchrone. Remplacé par la session async d'`agriflow` (§7.3). |
| `services/alternatives.py` | 565 | **G** | Génération + contraintes dures. Le cœur logistique. |
| `services/optimization.py` | 355 | **G+** | Classement multicritère. À généraliser sur trois domaines, pas quatre (§7.2). |
| `domain/optimization.py` | 107 | **G+** | Profils de pondération. Devient une table surchargeable par tenant. |
| `services/route_risk.py`, `domain/road_risk.py` | 611 | **G** | Exposition par tronçon et fenêtre de passage. |
| `services/nowcasting.py` | 226 | **G** | Ligne de base à persistance amortie + interface `NowcastModel`. |
| `services/agricultural_impact.py` | 235 | **G+** | Consommera les tables FAO d'`agriflow` au lieu de seuils autonomes. |
| `domain/thresholds.py` | 256 | **G** | Calibration par quantiles locaux, refus sur historique insuffisant. |
| `services/calibration.py`, `reliability.py` | 643 | **G** | Refus de conclure. À conserver mot pour mot. |
| `domain/plausibility.py` | 120 | **G** | Refus d'un seuil agronomiquement absurde. |
| `services/simulation.py` | 230 | **G+** | Simulation logistique « et si ». Distincte du simulateur d'irrigation (§7.2). |
| `providers/weather/` | 653 | **G** | `WeatherProvider` + `_PROVIDERS`, Open-Meteo, hors-ligne étiqueté `SIMULATED`. |
| `providers/routing/` | 794 | **G** | Graphe routier de référence + adaptateur OSRM non vérifié. |
| `providers/satellite/copernicus.py` | 375 | **G** | Adaptateur écrit, aller-retour réseau jamais exécuté. |
| `core/untrusted.py` | 43 | **G+** | **`wrap_untrusted` n'est appelé nulle part.** Code mort à câbler (§7.4). |
| `agent/service.py`, `prompt.py` | 332 | **G+** | Boucle conservée ; le bloc JSON de conclusion doit disparaître (§7.6). |
| `frontend/` (MapLibre) | 24 f. | **G+** | Base de l'interface fusionnée. Zéro test. |
| `docs/` (11 fichiers FR) | — | **G+** | Base documentaire du produit fusionné. |

### 1.3 `text_to_sql` — agent d'analyse

| Module | l. | Sort | Motif |
|---|---|---|---|
| `validators/` (9 f.) | 1 861 | **G+** | Pile AST. Ajouter l'allowlist de relations (§7.1). `set_config`/`current_setting` déjà refusés — la conception GUC de la §4.3 est compatible sans modification. |
| `database/executor.py` | 296 | **G+** | Transaction lecture seule bornée. **Doit devenir inconstructible sans tenant** (§7.1). |
| `database/engine.py` | 160 | **G+** | Deux moteurs séparés, sonde `CREATE TEMP TABLE`. Ajouter la sonde inter-tenants. |
| `database/catalog.py` | 328 | **G+** | Introspection `pg_catalog` filtrée par privilège. **Les valeurs d'exemple fuient entre tenants** (§7.1). |
| `retrieval/` (7 f.) | 1 280 | **G+** | pgvector, expansion FK. Modèle d'embedding à changer (§4.4, d'accord). |
| `graph/` (5 f.) | 910 | **G+** | Graphe de nœuds. `GraphState` doit porter le tenant. |
| `agents/` (4 f.) | ~500 | **G** | Intention, réparation, explication. Repli déterministe si le modèle échoue. |
| `observability/tracing.py` | — | **G** | Meilleure des trois. Devient la trace de toute la plateforme (§4.10, d'accord). |
| `config/pricing.py` | 84 | **G** | Coût inconnu ≠ zéro. |
| `evals/` | — | **G+** | Corpus 36 cas + golden set. Extension en §7.1. |
| `web/index.html` + `test_web_page.py` | — | **✗** | Client vanilla remplacé par React. **Les tests XSS qu'il porte n'ont pas d'équivalent React** (§7.9). |
| `docker-compose.yml` | — | **G+** | Jamais démarré, de son propre aveu. |

---

## 2. Carte des duplications

Les collisions nommées en §4 de la spécification sont réelles. En voici la résolution, puis
**sept duplications que la spécification ne nomme pas**.

### 2.1 Duplications déjà nommées par la spécification

| # | Capacité | `agriflow` | `atlasagri` | `text_to_sql` | Gagnant | Motif |
|---|---|---|---|---|---|---|
| D1 | Vocabulaire de provenance | `DataSource` (8 valeurs) | `DataState` (5) + `DataSourceRef` | — | **Les deux axes** | Voir §7.5 : la §4.1 est incomplète. |
| D2 | Registre d'outils | `ToolSpec` (dataclass, JSON schema à la main) | `ToolDefinition` (Pydantic, rôles, audit) | — | **`atlasagri`** | Validation, autorisation et audit intégrés. |
| D3 | Boucle d'agent | `AgriculturalAgent` | `AgentService` | graphe LangGraph | **`atlasagri` + 2 détails d'`agriflow`** | §2.5. |
| D4 | Provider météo | `WeatherProvider` + cache PG | `WeatherProvider` + `TtlCache` mémoire | — | **Abstraction `atlasagri`, cache `agriflow`** | §2.3. |
| D5 | ET0 | Penman-Monteith complet + Hargreaves | consommé depuis Open-Meteo | — | **`agriflow`** | §7.7 : garder la valeur Open-Meteo comme contrôle croisé. |
| D6 | Carte | Leaflet + leaflet-draw | MapLibre GL | — | **MapLibre** | §4.8 correcte. Le dessin de polygone est à réécrire. |
| D7 | Moteur de décision | `scenarios.py` | `alternatives.py` + `optimization.py` | — | **Ni l'un ni l'autre : contrat partagé** | §7.2, désaccord principal. |

### 2.2 Duplications non nommées

**D8 — Deux modèles d'explicabilité, deux panneaux, un seul concept.**
`agriflow.irrigation.explain` produit `Explanation` (entrées, étapes numérotées avec équation
FAO, hypothèses, `DataQuality` pondérée par source). `atlasagri.domain.provenance` produit
`Measure` + `Evidence` + `DataSourceRef`. Côté interface, `ExplanationPanel.tsx` (5 onglets) et
`decision.tsx` (`PanneauRecommandation`, preuves, chronologie) résolvent le même problème avec
des types incompatibles.
*Décision* : un seul modèle `Explanation { inputs: list[Measure], steps, assumptions, quality }`
où `Measure` porte le couple `(DataState, DataOrigin)` + `DataSourceRef`. Les deux panneaux de
la §7 en découlent — « Pourquoi cette décision ? » lit `steps`, « Sources et preuves » lit
`inputs` et `sources`. **Coût de ne pas le faire** : deux façons d'afficher la même parcelle
selon la page.

**D9 — Trois scores de fiabilité indépendants.**
`agriflow.explain._SOURCE_WEIGHT` (dict source → poids, 0,3 à 1,0) ; `atlasagri.Confidence`
(énumération LOW/MEDIUM/HIGH, agrégée par le pire cas) ; `atlasagri.services.reliability`
(score de Brier, refuse de conclure sous échantillon insuffisant) ; et
`txtsql.SafetyReport.total_cost` qui n'est pas une fiabilité mais est lu comme telle. Quatre
notions différentes, toutes affichées comme « fiabilité ».
*Décision* : séparer explicitement **fiabilité des entrées** (pondération par `DataOrigin`,
d'`agriflow`), **confiance de la sortie** (`Confidence`, d'`atlasagri`) et **calibration
mesurée** (Brier, `reliability.py`) — trois champs, trois libellés français distincts. La §4.1
n'en mentionne qu'un.

**D10 — Deux caches, deux politiques d'expiration, deux granularités.**
Au-delà de D4 : `agriflow` stocke **au pas journalier** (`weather_observations` /
`weather_forecasts`, TTL en minutes, invalidation par requête) ; `atlasagri` récupère **au pas
horaire** (`HOURLY_VARIABLES`, 10 variables) dans un `TtlCache` en mémoire, perdu au
redémarrage. Le nowcasting a besoin de l'horaire, FAO-56 a besoin du journalier.
*Décision* : **l'horaire est le grain de stockage, le journalier est une vue dérivée.** C'est
strictement meilleur que « un seul jeu de tables » : FAO-56 définit RHmax/RHmin comme les
extrema *horaires* de la journée, que le stockage journalier d'`agriflow` ne peut pas
reconstituer. Le passage à l'horaire améliore donc l'ET0, il ne la dégrade pas.

**D11 — Deux notions de « simulation » portant le même mot en français.**
`agriflow.scenarios.simulate_irrigation_scenario` (projection physique d'une dose modifiée) et
`atlasagri.services.simulation.SimulationService` (rejeu d'une décision logistique sous
hypothèse). L'interface a une page « Simulations » ; l'outil agent d'`atlasagri` s'appelle
`simulate_scenario`, celui d'`agriflow` `simulate_irrigation_scenario`. Le modèle choisira mal.
*Décision* : renommer côté code et côté outil — `project_irrigation_variants` et
`replay_logistics_hypothesis` — et garder deux entrées distinctes dans la page Simulations.
Le mot « simulation » reste en français à l'écran, désambiguïsé par le contexte de page.

**D12 — Deux façons de refuser une capacité indisponible.**
`agriflow` lève `LLMNotConfiguredError` / renvoie « Données satellite indisponibles » ;
`atlasagri` lève `FeatureDisabledError` / `ProviderUnavailableError` avec message français ;
`txtsql` refuse **au démarrage** (`_production_guards`). Trois politiques : échec à
l'exécution, dégradation annoncée, refus de démarrage.
*Décision* : la politique de `txtsql` gagne pour tout ce qui est **structurel** (une clé
absente, un rôle trop privilégié, un DSN partagé) ; celle d'`atlasagri` gagne pour tout ce qui
est **conjoncturel** (un service tiers momentanément injoignable). La règle de partage est
explicite : *si la configuration est fausse, on ne démarre pas ; si le monde extérieur est
indisponible, on le dit en français et on continue.*

**D13 — Deux journaux d'audit et une trace, sans jointure.**
`atlasagri` écrit `AuditLog` (tenant, action, ressource, résultat) et commit **à l'intérieur**
de l'exécution d'outil ; `txtsql` produit une trace par `run_id` avec coût et jetons ;
`agriflow` renvoie `tool_calls` à l'interface. Aucun identifiant commun.
*Décision* : le `run_id` de `txtsql` devient l'identifiant de corrélation de toute la
plateforme et entre dans `AuditLog`. Effet secondaire à corriger : le `session.commit()` de
`_audit` casse toute transaction de requête englobante — l'audit doit écrire sur une session
séparée.

**D14 — Deux jeux de données de démonstration qui ne se recouvrent pas.**
`agriflow/data/seed` décrit des fermes du Souss-Massa avec cultures, sols et systèmes ;
`atlasagri/db/seed.py` décrit des expéditions, entrepôts, fournisseurs et nœuds routiers
marocains. Le scénario de démonstration de la §12 exige **les deux sur le même tenant** :
la parcelle P03 et l'expédition EXP-1842 appartiennent à « Souss Primeurs ».
*Décision* : un seul module de seed produisant un tenant de démonstration cohérent, où les
tomates de l'expédition proviennent des parcelles irriguées. C'est un travail neuf que ni la
§5 ni la §11 n'isolent, et c'est le pré-requis de la démonstration.

**D15 — Deux conventions de nommage de code, dont une en français.**
`atlasagri` nomme en français côté frontend (`PageExpeditions`, `BadgeRisque`, `pourcentage`,
`Coquille`) et en anglais côté backend. `agriflow` est en anglais des deux côtés. La règle de
`CLAUDE.md` (« code, identifiants : anglais ») est donc **déjà violée par le gagnant désigné**.
*Décision* : la règle reste, et `atlasagri`'s frontend est renommé en anglais lors de son
absorption. C'est mécanique mais non trivial : ~24 fichiers, tous les imports. À budgéter en
phase 5, pas à découvrir en phase 5.

---

## 3. Modèle de données fusionné

Origine : **A** = `agriflow`, **T** = `atlasagri`, **N** = nouveau.

```
                    ┌──────────────┐
                    │ tenants  (T) │
                    └──────┬───────┘
                           │ 1..n  (tenant_id sur TOUTE table métier, indexé, RLS)
      ┌────────────────────┼────────────────────┬──────────────────┐
      │                    │                    │                  │
┌─────▼──────┐      ┌──────▼──────┐      ┌──────▼──────┐   ┌───────▼────────┐
│ users  (T) │      │ sites   (T) │      │ suppliers(T)│   │ audit_log  (T) │
│ + AGRONOME │      │ polymorphe: │      └──────┬──────┘   └────────────────┘
│   (N)      │      │ farm·wh·hub │             │
└────────────┘      │ ·customer   │      ┌──────▼──────┐
                    │ absorbe     │      │ products (T)│
                    │ farms   (A) │      └──────┬──────┘
                    └──────┬──────┘             │
                           │ site_type=farm     │
                    ┌──────▼──────┐      ┌──────▼──────┐   ┌────────────────┐
                    │ fields  (A) │      │ inventory(T)│   │ shipments  (T) │
                    │ + geometry  │      └─────────────┘   └───────┬────────┘
                    │   mirror(N) │                                │
                    └──┬───────┬──┘                        ┌───────▼────────┐
                       │       │                           │ route_segments │
        ┌──────────────▼──┐ ┌──▼───────────────────┐       │            (T) │
        │ soil_moisture   │ │ irrigation_          │       └────────────────┘
        │ _readings   (A) │ │ recommendations (A)  │
        └─────────────────┘ └──────────┬───────────┘
                                       │  fusionne avec
                            ┌──────────▼───────────┐
                            │ recommendations  (N) │◄── decisions de TOUS
                            │ inputs·sources·      │    les domaines (§7.2)
                            │ alternatives·reasons │
                            │ ·explanation·human   │
                            └──────────────────────┘

  Référentiel global, lecture seule, surchargeable par tenant (tenant_id NULL = global) :
  crops (A) · crop_growth_stages (A) · soil_profiles (A) · irrigation_systems (A)
  · regions (T) · road_nodes (T) · optimization_profiles (T→N, était du code)

  Observations : weather_observations_hourly (N, grain horaire — cf. D10)
                 weather_daily (N, VUE dérivée)  ·  satellite_observations (T)

  meta : schema_documents (pgvector) · prompt_versions · eval_runs · run_traces
```

**Points de réconciliation notables**

1. `farms` (A) disparaît dans `sites` (T) avec `site_type='farm'`. `fields.site_id` remplace
   `fields.farm_id`. Les deux tables portaient déjà latitude/longitude/région/capacité.
2. `irrigation_recommendations` (A) et `recommendations` (T) fusionnent en une seule table
   avec un discriminant de domaine. C'est ce que la §5 demande, et c'est ce qui rend la
   question « combien de recommandations ont été acceptées » (§12) répondable.
3. Le référentiel FAO d'`agriflow` devient la source des seuils que
   `agricultural_impact.py` posait en dur. Là où FAO ne documente rien, la colonne reste
   `NULL` et la sortie dépendante est déclarée indisponible — comportement déjà implémenté par
   `scenarios._estimate_yield_impact` pour Ky, à généraliser.
4. `optimization_profiles` sort du code (`domain/optimization.py`) et devient une table à
   surcharge par tenant, comme la spécification le prévoit pour `soil_profiles`.
5. **Toute table listée « (T) » ou « (A) » ci-dessus est aujourd'hui créée par
   `Base.metadata.create_all()` ou par une migration Alembic à trois révisions.** La
   reconstruction sous un seul cadre de migration est le vrai contenu de la phase 1 (§7.3).

---

## 4. Registre d'outils unifié

37 outils existent (19 + 18). Le registre fusionné en compte **31** : 4 fusions, 2 suppressions.

| Outil | Origine | Statut |
|---|---|---|
| `list_fields` · `get_field` · `get_farm` | A | repris, `get_farm` → `get_site` |
| `get_crop_information` · `get_soil_information` · `get_irrigation_system` · `get_growth_stage` | A | repris |
| `get_soil_moisture` | A | repris, sortie enrichie de `(DataState, DataOrigin)` |
| `get_weather` · `get_weather_forecast` | A | **fusionnés avec** `get_weather_nowcast` (T) → `get_weather` (paramètre d'horizon) |
| `calculate_et0` · `calculate_crop_water_requirement` · `calculate_water_balance` | A | repris |
| `calculate_irrigation_requirement` | A | repris — chaîne tout le calcul en Python, conception correcte |
| `calculate_irrigation_duration` · `calculate_water_cost` | A | repris |
| `simulate_irrigation_scenario` | A | repris, renommé `project_irrigation_variants` (D11) |
| `get_field_status` · `get_farm_overview` | A | **fusionnés** → `get_portfolio_overview` (parcelles + expéditions + stocks) |
| `get_satellite_observation` | T | repris |
| `get_crop_risk` · `get_at_risk_regions` | T | repris, consomment le référentiel FAO |
| `get_shipment` · `get_route_options` · `get_route_risk` | T | repris |
| `generate_alternatives` | T | repris |
| `get_inventory_status` · `calculate_stock_coverage` · `get_supplier_exposure` | T | repris |
| `create_recommendation` · `create_alert` | T | repris — seuls outils non lecture seule, proposition en attente d'approbation |
| `get_threshold_calibration_status` | T | repris, rôle `AGRONOME` |
| `get_pending_outcomes` · `get_outcome_question` · `submit_shipment_outcome` | T | repris |
| `simulate_scenario` | T | repris, renommé `replay_logistics_hypothesis` (D11) |
| `get_weather_nowcast` | T | **✗ fusionné** dans `get_weather` |
| `ask_analytics` | **N** | nouveau — pont vers l'agent SQL, tenant injecté, jamais de SQL en paramètre |

**Ce qui est neuf et que la §4.2 sous-estime** : `ToolDefinition` n'a **aucun schéma de sortie**
aujourd'hui (`handler` renvoie `dict[str, Any]`). Le « typed output schema » de la §4.2 est un
développement à part entière sur 31 outils, pas un report. Voir §7.5.

---

## 5. Séquence de migration

La §11 tient, avec **une phase 1 réécrite** et **une phase 0 bis insérée**.

| Phase | Contenu ajusté | Terminée quand |
|---|---|---|
| **1** | Monorepo, réglages unifiés, **socle de persistance = session async + Alembic d'`agriflow`**, modèle tenant + `RequestContext` + repositories d'`atlasagri` **portés en async**, RLS `FORCE` sur toute table tenant, image PostgreSQL 16 portant **PostGIS *et* pgvector** (§6, R3), audit, CI | deux organisations existent, ne se voient pas, et un test le prouve à 5 couches |
| **1 bis** *(nouveau)* | Renommage anglais du frontend `atlasagri` (D15), avant que 20 pages neuves n'héritent de la convention française | `ruff`/`tsc` propres, aucun identifiant français hors chaînes affichées |
| **2** | Schéma fusionné, seed FAO, **seed de démonstration unifié parcelles × expéditions (D14)** | le scénario §12 a ses données |
| **3** | Moteurs déterministes. **Deux moteurs, un contrat** (§7.2), pas un `DecisionEngine` unique | exemples FAO passent ; alternatives classées avec motifs de rejet |
| **4** | Registre unique, MCP, agent, **schémas de sortie typés (§7.5)**, **carte de décision non rédigée par le modèle (§7.6)**, `wrap_untrusted` câblé + test (§7.4) | les deux questions de la §11 passent par le même agent |
| **5** | Frontend, MapLibre + PMTiles, deux panneaux, **tests de rendu XSS React (§7.9)** | démonstration sans développeur |
| **6** | Agent d'analyse : 4 couches + **la couche tenant (§7.1)**, retrieval multilingue, vues `analytics` | corpus étendu passe ; les tentatives inter-tenants sont refusées **par la couche qui le revendique** |
| **7** | SaaS : plans, quotas, métrage, onboarding, conformité | — |
| **8** | Production, Docker **réellement démarré**, un aller-retour Copernicus réel | — |

---

## 6. Risques non nommés par la spécification, classés

| # | Risque | Gravité | Atténuation |
|---|---|---|---|
| **R1** | **La §4.3 telle qu'écrite n'est pas implémentable** : la §3 impose « `analytics` : vues uniquement, aucune table de base », la §4.3 impose « `FORCE ROW LEVEL SECURITY` sur chaque table de `analytics` ». PostgreSQL refuse toute politique RLS sur une vue (sonde L). | **Critique** | §7.1 : politiques sur les tables de base `app`, vues `analytics` détenues par un rôle distinct. |
| **R2** | **`RLS ENABLE` sans `FORCE` derrière une vue détenue par le propriétaire de la table renvoie toutes les organisations** — vérifié, 2 lignes au lieu de 1 (sonde E). Aucune erreur, aucun avertissement. | **Critique** | `FORCE` obligatoire + test de migration énumérant `pg_class.relforcerowsecurity`. |
| **R3** | Aucune image de base ne fournit **PostGIS et pgvector ensemble**. `agriflow` utilise `postgis/postgis:16-3.4`, `txtsql` `pgvector/pgvector:pg16`. `atlasagri` n'a **ni Dockerfile ni compose**. | Élevée | Image dérivée construite en phase 1, pas en phase 8 — sinon les phases 2 à 7 se développent sur une base qui ne sera jamais celle de production. |
| **R4** | **Les valeurs d'exemple du schéma vectoriel sont des données de tous les tenants.** `catalog.py` les tire de `pg_stats.most_common_vals` ; elles sont embarquées dans l'index, envoyées au modèle, et affichées dans le panneau schéma. La §4.3 ne voit ce risque que pour les champs personnalisés. | Élevée | §7.1 : jamais d'échantillon sur table portant `tenant_id` ; valeurs d'énumération produites depuis le code. |
| **R5** | `ReadOnlyExecutor.explain()` et `.execute()` ouvrent **deux transactions distinctes**. Un `SET LOCAL` posé dans l'une n'existe pas dans l'autre : sans GUC, `EXPLAIN` échoue (sonde G). Un correctif posé au seul endroit « évident » casse la moitié du chemin. | Élevée | Lier le tenant à l'**acquisition de connexion**, pas au site d'appel. |
| **R6** | Un test inter-tenants qui interroge la ligne du tenant B en tant que tenant A **passe aussi si la ligne n'existe pas**. Le refus d'un jeu de fixtures muet ressemble exactement au succès de RLS. | Élevée | Tout test d'isolation prouve d'abord la visibilité (en tant que B) puis l'invisibilité (en tant que A). Discipline déjà appliquée par `reliability.py`, à étendre. |
| **R7** | Les vues **matérialisées ne peuvent pas porter de RLS** (sonde H). Une matview ajoutée plus tard pour la performance est une copie intégrale non protégée. | Élevée | Interdiction explicite dans `analytics` + test énumérant `relkind='m'`. |
| **R8** | `agriflow` est **async**, `atlasagri` est **sync**. Le registre d'outils d'`atlasagri` (gagnant) tient une `Session` synchrone ; les moteurs d'irrigation et l'agent sont async. Un `await` manquant sur une session sync ne casse rien visiblement — il sérialise la requête. | Élevée | Portage complet en async en phase 1 ; interdiction du `Session` sync par un test d'import. |
| **R9** | `_audit()` fait `context.session.commit()` **au milieu** de l'exécution d'un outil. Sous une transaction de requête, cela valide des écritures partielles. | Moyenne | Session d'audit séparée. |
| **R10** | La règle §1.4 affirme que des tests d'injection existent dans `atlasagri`. **Ils n'existent pas, et `wrap_untrusted` n'est appelé nulle part.** | Moyenne | §7.4. Le registre d'honnêteté doit l'enregistrer avant que le code ne soit écrit. |
| **R11** | Les deux frontends React n'ont **aucun test**. Les tests XSS que la §7 demande de « conserver » portent sur une page vanilla de `txtsql` qui est supprimée. | Moyenne | Introduire Vitest en phase 5 et écrire les tests, sans les présenter comme repris. |
| **R12** | Sous RLS, `TenantRepository.get()` reçoit `None` au lieu d'une entité d'un autre tenant : la `TenantIsolationError` — donc le **signal d'audit d'une tentative** — disparaît. | Faible | Conserver la vérification applicative comme détecteur sur le chemin opérationnel ; accepter que le chemin analytique ne puisse pas distinguer, et le documenter. |
| **R13** | Le plan `EXPLAIN` renvoyé au client nomme les **tables de base** et le prédicat de politique (sonde M), objets sur lesquels le rôle analytique n'a aucun privilège. | Faible | Résumer le plan avant la frontière HTTP : coût et lignes estimées, pas le texte. |

---

## 7. Ce que je pense être faux dans la spécification

### 7.1 §4.3 — la conception d'isolation est juste d'intention et fausse de trois façons précises

C'est le point sur lequel la revue était demandée. Il a été **vérifié sur un PostgreSQL 16.13
réel**, pas raisonné.

#### a. La §4.3 contredit la §3, et la contradiction est fatale

> §3 : « `analytics` (la surface que l'agent text-to-SQL peut voir — **vues uniquement**, RLS
> appliquée, **aucune table de base**) »
> §4.3 : « chaque **table** de `analytics` porte une politique `FORCE ROW LEVEL SECURITY` »

PostgreSQL n'attache aucune politique RLS à une vue :

```
ALTER VIEW analytics.v_shipments ENABLE ROW LEVEL SECURITY;
ERROR:  ALTER action ENABLE ROW SECURITY cannot be performed on relation "v_shipments"
DETAIL:  This operation is not supported for views.
```

Il n'existe donc aucune lecture des deux paragraphes qui soit implémentable telle quelle.

#### b. La disposition correcte, exécutée et vérifiée

Les politiques vivent sur les **tables de base de `app`**. `analytics` ne contient que des vues,
détenues par un rôle **distinct** du propriétaire des tables :

```sql
-- tables de base : politique + FORCE
ALTER TABLE app.shipments ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.shipments FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app.shipments
  USING (tenant_id = current_setting('app.current_tenant')::uuid);
GRANT SELECT ON app.shipments TO analytics_owner;   -- et à personne d'autre

-- vues : détenues par analytics_owner, PAS security_invoker
SET ROLE analytics_owner;
CREATE VIEW analytics.v_shipments AS SELECT id, reference, volume_t FROM app.shipments;
GRANT SELECT ON analytics.v_shipments TO analytics_ro;
-- analytics_ro n'a AUCUN privilège sur le schéma app
```

Résultat mesuré, même rôle, même vue, deux transactions :

```
SET LOCAL app.current_tenant = '1111…';  →  EXP-A1, EXP-A2      (2 lignes)
SET LOCAL app.current_tenant = '2222…';  →  EXP-B1              (1 ligne)
sans SET LOCAL                           →  ERROR: invalid input syntax for type uuid: ""
SELECT * FROM app.shipments              →  ERROR: permission denied for schema app
```

Trois propriétés en découlent, dont deux que la §4.3 n'obtient pas telle qu'écrite :

1. **La couche 2 redevient porteuse.** Même si l'allowlist de relations de la couche 1 est
   contournée, le rôle analytique n'a aucun privilège sur `app` : la requête échoue au
   privilège, pas au validateur. C'est exactement l'ambition affichée par la §4.3.
2. **L'échec est fermé et bruyant.** Une organisation absente provoque une erreur, jamais un
   jeu vide. C'est essentiel ici : un résultat vide serait expliqué par l'agent d'analyse comme
   « vous n'avez aucune expédition ce mois-ci » — un chiffre faux énoncé avec assurance, soit
   précisément ce que la doctrine interdit. Utiliser `current_setting(..., true)`
   (`missing_ok`) transformerait la panne en mensonge : **à proscrire par un test**.
3. `SET LOCAL` ne survit pas au `COMMIT` (vérifié : `[]` après validation), donc le partage de
   pool de connexions est sûr.

#### c. `FORCE` n'est pas une précaution, c'est la totalité de la garantie

La sonde la plus importante. Table avec RLS **activée**, politique correcte, vue détenue par le
propriétaire de la table — configuration qu'une revue de code approuverait :

```
=== ENABLE mais pas FORCE, vue détenue par le propriétaire de la table ===
 id |              tenant_id               | secret
----+--------------------------------------+--------
  1 | 11111111-…                           | A
  2 | 22222222-…                           | B      ← organisation d'un autre client
(2 rows)

=== la même vue après ALTER TABLE … FORCE ROW LEVEL SECURITY ===
  1 | 11111111-…                           | A
(1 row)
```

La §4.3 écrit « une RLS silencieusement désactivée ressemble exactement à une RLS qui
fonctionne » — c'est vrai, et le cas ci-dessus est pire : la RLS **est** activée, la politique
**est** présente, `\d` l'affiche, et toutes les organisations sortent. Le mot `FORCE` de la
§4.3 est donc correct et doit être promu de détail d'implémentation à **invariant testé** :
un test de migration énumérant `pg_class.relrowsecurity AND relforcerowsecurity` sur toute
table portant `tenant_id`.

#### d. Refuser `security_invoker`, et savoir pourquoi

La tentation moderne (PostgreSQL 15+) est `CREATE VIEW … WITH (security_invoker = true)`, qui
évalue les privilèges et la RLS avec le rôle appelant. Elle est à rejeter ici :

```
CREATE VIEW analytics.v_inv WITH (security_invoker = true) AS SELECT … FROM app.shipments;
SELECT * FROM analytics.v_inv;   →  ERROR: permission denied for table shipments
```

Pour fonctionner, elle exige que `analytics_ro` détienne `SELECT` sur les tables de base — donc
qu'il puisse les interroger **directement**, hors des vues. Le confinement « le rôle analytique
ne voit que des vues » retombe alors entièrement sur l'allowlist AST, c'est-à-dire sur la
couche 1, c'est-à-dire sur notre propre code. C'est l'inverse de ce que la §4.3 cherche.

#### e. Deux transactions, pas une

La §4.3 dit « posé avec `SET LOCAL` dans la même transaction en lecture seule que la requête ».
Il y en a **deux** : `ReadOnlyExecutor.explain()` et `ReadOnlyExecutor.execute()` ouvrent chacune
leur connexion et leur transaction. Sans GUC, `EXPLAIN` échoue au moment de la planification :

```
BEGIN; EXPLAIN (COSTS FALSE) SELECT * FROM analytics.v_shipments;
ERROR:  invalid input syntax for type uuid: ""
```

**Proposition, plus forte que la spécification** : ne pas ajouter un `SET LOCAL` à deux endroits.
Rendre le tenant **inséparable de l'obtention d'une connexion analytique** —

```python
class AnalyticsScope:                    # construit depuis RequestContext, jamais d'un outil
    def __init__(self, engine: AsyncEngine, tenant_id: UUID) -> None: ...
    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection]:
        async with self._engine.connect() as conn:
            tx = await conn.begin()
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            await conn.execute(text("SET LOCAL app.current_tenant = :t"), {"t": str(self._tenant)})
            ...

class ReadOnlyExecutor:
    def __init__(self, scope: AnalyticsScope, settings: ExecutionSettings) -> None: ...
```

`ReadOnlyExecutor` devient alors **inconstructible sans tenant**, et `mypy --strict` fait
respecter la règle. C'est la doctrine §1.5 appliquée : une garantie structurelle plutôt qu'un
appel qu'un nœud pourrait oublier. `GraphState` porte le tenant pour la trace, jamais pour
l'exécution.

#### f. L'allowlist de relations : ne pas la maintenir à la main

La §4.3 demande « une allowlist de vues analytiques protégées par RLS ». Elle existe déjà :
`CatalogReader` filtre par `has_table_privilege(c.oid, 'SELECT')`, exécuté **sur le moteur
analytique**, donc en tant que rôle analytique. L'instantané du catalogue *est* l'ensemble des
relations autorisées. Écrire une seconde liste, c'est créer une source de vérité qui divergera
au premier `GRANT`.
*Décision* : `NameResolver` refuse toute relation absente de l'instantané — comportement déjà
présent — et l'on ajoute uniquement la contrainte que l'instantané soit construit sur les
schémas `analytics` seulement (`DatabaseSettings.schemas`). Un test vérifie que `app` n'y figure
jamais.

#### g. Ce que la §4.3 ne voit pas : l'index vectoriel fuit des **valeurs de lignes**

La §4.3 conditionne la fuite du catalogue à l'existence de champs personnalisés : « *si* les
tenants peuvent ajouter des champs ou des cultures ». La fuite existe **sans aucune
personnalisation**. `catalog.py` renseigne `ColumnInfo.sample_values` depuis
`pg_stats.most_common_vals`, `documents.py` les écrit dans le document de colonne
(« Example values: … »), et ce document part à l'embedding, au modèle, et au panneau schéma de
l'interface. Sur une colonne `sites.name_fr`, ce sont les noms de sites des plus gros clients.

Trois garde-fous existent déjà (`index_sample_values`, motifs PII, cardinalité) ; aucun n'est
un garde-fou de tenant. Une bonne nouvelle mesurée : `pg_stats` masque les lignes des tables où
`row_security_active()` est vrai, et `analytics_ro` voit bien **0 ligne** pour le schéma `app`.
Mais l'indexation est faite par le rôle **métadonnées**, pas par le rôle analytique.

*Décision* : **aucun échantillon sur une table portant `tenant_id`.** Et la conséquence
importante — cela casserait le test exigé par la §4.9 (« une question française sur *annulé*
filtre sur `'cancelled'` »), puisque c'est précisément `sample_values` qui porte cette
correspondance. La réparation est meilleure que ce qu'elle remplace : les valeurs d'énumération
sont **déclarées dans le code** (`domain/enums.py` porte déjà `ShipmentStatus`, `RiskLevel`,
`DataState` avec leurs `label_fr`), donc l'index les reçoit d'un registre d'énumérations,
complet et déterministe, plutôt que des `most_common_vals` qui ne contiennent que les valeurs
fréquentes et qui varient avec `ANALYZE`.

#### h. Le corpus adverse ne peut pas, par construction, noter la couche RLS

La §4.3 a raison sur le principe — passer de 36 cas à ~45, voir le chiffre baisser, ne pas
ajuster le corpus. Mais « ajouter des cas de franchissement d'organisation au corpus adverse »
ne marche pas tel quel, et la raison est dans le harnais lui-même.

`evals/harness/runner.py` note chaque cas par la couche qui l'a arrêté —
`SECURITY` / `VALIDATION` / `EXPLAIN` / `MISSED` — et publie déjà deux chiffres distincts
(« attrapé » et « attrapé par la couche attendue »), ce qui est exactement la bonne forme. Le
problème est la règle de sûreté qui suit, et qui est correcte :

> *« Nothing here is ever executed: a case that no layer catches is recorded as `missed` and
> left unrun, because the corpus contains statements whose whole point is that running them is
> harmful. »*

Or un `SELECT` inter-tenants est **valide, résoluble, bon marché et sémantiquement correct** :
il traverse la couche 1, traverse `EXPLAIN`, et se fait donc enregistrer `MISSED` — puis n'est
jamais exécuté. Le corpus ne peut par conséquent **jamais observer que la RLS l'a arrêté**.
Il noterait comme échec la seule couche que la §4.3 rend porteuse.

*Décision* : les cas de franchissement forment une **suite distincte** où l'exécution *est*
l'assertion, avec une cinquième couche `RLS` dans l'énumération `CatchLayer` — exécuter en tant
que A, constater zéro ligne du jeu de B. Le titre « 36/36 » ne les absorbe pas : deux suites,
deux conditions d'exécution, deux lignes de rapport. Le CI de `txtsql` fait déjà la bonne chose
sur ce plan — il **échoue** si les DSN sont absents plutôt que de sauter silencieusement les
tests d'intégration — et c'est ce motif qu'il faut étendre.

Et le piège de R6 s'applique en plein ici : « en tant que A, lire la ligne de B » réussit aussi
quand la fixture de B n'existe pas. Chaque cas prouve d'abord la visibilité en tant que B.

#### Verdict §4.3

L'intention est juste et le choix de faire porter la garantie par la couche 2 est le bon.
Trois corrections sont nécessaires (`analytics` en vues sur tables `app` porteuses de la
politique ; `FORCE` érigé en invariant testé ; tenant lié à l'acquisition de connexion), une
tentation à documenter comme refusée (`security_invoker`), et une fuite à fermer que la
spécification ne voit pas (les valeurs d'exemple de l'index vectoriel).

---

### 7.2 §4.6 — non : le simulateur d'irrigation et l'`AlternativeEngine` n'ont pas la même forme

C'est le second point sur lequel la revue était demandée. **Vous forcez une fusion qui coûte
plus qu'elle ne rapporte.**

#### Ce que la §4.6 affirme

> « Ce sont la même forme : *générer des candidats → filtrer les infaisables → noter par
> critères pondérés → classer → expliquer → conserver les écartés avec leur motif.* »

Vrai pour la logistique. Faux pour l'irrigation, sur trois des six étapes.

#### Ce que le code fait réellement

**Côté logistique** (`alternatives.py` + `optimization.py`), les six étapes existent
littéralement. Les candidats sont **hétérogènes et incomparables par construction** — un
itinéraire côtier, un fournisseur de Berkane, un entrepôt intermédiaire — et ne deviennent
comparables que par normalisation min-max sur un vecteur de critères commun, pondéré par profil
produit. Sans le classement, il n'y a pas de réponse.

**Côté irrigation** (`irrigation.py:155-250`), la décision est une **cascade de seuils sur une
seule grandeur physique** :

```python
if forecast_rainfall_postpones:      recommendation = POSTPONE_RAIN
elif projected >= trigger:           recommendation = IRRIGATE      # trigger = RAW × fraction
elif projected >= monitor:           recommendation = MONITOR
else:                                recommendation = NO_IRRIGATION
```

Les quatre valeurs de `Recommendation` sont **des régions mutuellement exclusives d'un même
axe** — le déficit projeté — délimitées par des seuils FAO-56. Il n'y a pas de candidats à
générer : il y a une position sur un axe à lire. Et le simulateur `scenarios.py` ne classe rien
non plus : il projette le bilan hydrique de la dose retenue à −10/−20/−30 %, et rend une
**trajectoire de stress dans le temps**. C'est une analyse de sensibilité d'une action déjà
décidée, pas un choix entre options.

Les trois étapes qui n'existent pas :

| Étape §4.6 | Logistique | Irrigation |
|---|---|---|
| filtrer les infaisables | `AlternativeStatus.INFEASIBLE` + motifs, sortie du classement | il n'y a pas d'option écartée : la dose est **plafonnée** (capacité d'infiltration) ou **annulée** (< dose minimale utile) avec un avertissement français. Transformer un plafonnement en rejet perdrait le conseil réel — « fractionnez l'irrigation » |
| noter par critères pondérés | 5 critères, 4 profils, poids sommant à 1, affichés | **aucun vecteur de poids n'existe, et il ne doit pas en exister** |
| classer | tri par score composite | les scénarios sont ordonnés par leur variation, qui est une **entrée**, pas un rang |

#### Pourquoi la pondération de l'irrigation serait une régression doctrinale

Introduire un vecteur de poids « eau / rendement / coût / stress » pour classer *irriguer
maintenant* contre *reporter* contre *fractionner* exigerait un modèle d'arbitrage agronomique
que la FAO-56 **ne fournit pas**. Ce serait une valeur inventée, présentée avec la même
apparence d'objectivité que les autres — soit exactement la doctrine §1.3 (« *missing is
missing* ») contournée par une couche d'architecture. Et à l'écran, cela remplacerait une chaîne
auditable — « déficit projeté 41,2 mm ≥ seuil 38,0 mm, soit la RFU » — par « report noté 0,62 ».
La première est contestable par un agronome ; la seconde ne l'est par personne.

L'exemple que la §4.6 donne à l'appui — « un périmètre en pénurie sous quota pondère le volume
au-dessus du rendement » — est réel, mais ce n'est **pas un poids** : c'est une **contrainte**,
un plafond de volume alloué. Elle appartient au calcul de dose (`irrigation.py`, à côté du
plafond d'infiltration existant), pas à une fonction de score. Elle est d'ailleurs absente des
trois dépôts et devrait être ajoutée — c'est une remarque utile de la §4.6, mal placée.

#### Ce que la fusion coûterait, concrètement

`Alternative` est un modèle Pydantic gelé de ~25 champs typés et nommés en métier :
`duration_hours`, `distance_km`, `estimated_cost_mad`, `sla_compliant`, `sla_margin_hours`,
`exposure_fraction`, `route_assessment`… `CRITERIA` extrait ses critères par **nom d'attribut**.
`_infeasible()` fabrique une option écartée avec `risk_score=1.0`, `risk_level=CRITICAL`,
`distance_km=0.0`.

Généraliser à l'irrigation impose l'un des deux :

- **une union de champs optionnels** — chaque variante d'irrigation porterait `distance_km=0.0`
  et `sla_compliant=False`. Dans un produit où chaque valeur porte son état épistémique, écrire
  un zéro là où la grandeur n'a pas de sens est un mensonge de schéma ;
- **un vecteur de critères générique** `dict[str, float]` — ce qui jette les champs typés dont
  dépendent le classement, le panneau de preuves, la carte et les tests.

Coût estimé : réécriture de `alternatives.py` (565 l.), `optimization.py` (355 l.) et
`scenarios.py` (299 l.), plus l'adaptation de `test_engines.py`. Bénéfice : un nom de classe
partagé.

#### Ce qu'il faut faire à la place

Deux choses, l'une gratuite et l'autre réellement utile.

1. **Unifier le contrat de sortie, pas les moteurs.** Ce qui est réellement commun est la forme
   de ce qui est *produit* et *persisté* : `Decision { recommendation, alternatives_considered
   (avec motif de rejet), tradeoffs, explanation, evidence, confidence, human_decision }`. C'est
   exactement la table `recommendations` de la §5 et les deux panneaux de la §7. Les deux
   moteurs remplissent le même contrat ; ils ne partagent pas d'implémentation. Coût : faible.
   Bénéfice : la page Alternatives, le panneau de preuves, la persistance auditable et la
   question analytique « combien de recommandations acceptées » marchent pour les deux domaines.

2. **Généraliser `OptimizationService` sur trois domaines, pas quatre.** Logistique, sourcing et
   stocks partagent réellement la forme : candidats hétérogènes, contraintes dures, arbitrage
   coût/risque/délai. `OptimizationService.rank` est déjà presque agnostique. Sortir `Alternative`
   du module logistique vers un protocole `Rankable` (les cinq attributs de `CRITERIA`) suffit —
   les trois domaines l'implémentent. L'irrigation ne l'implémente pas, parce qu'elle n'a rien à
   classer.

*Statut* : ceci contredit une résolution mandatée de la §4. Une décision sera écrite dans
`docs/decisions/` avant la phase 3, quelle que soit votre réponse.

---

### 7.3 §11 phase 1 — « le cœur d'`atlasagri` relevé et durci » demande de durcir le maillon faible

`atlasagri` gagne pour le modèle tenant, `RequestContext`, `TenantRepository` et le registre
d'outils : ces choix sont bons. Mais sa **couche de persistance est la plus faible des trois** :

| | `agriflow` | `atlasagri` | `text_to_sql` |
|---|---|---|---|
| SQLAlchemy | **async** | sync | **async** |
| Migrations | **Alembic, 3 révisions** | `Base.metadata.create_all()` | SQL brut de seed |
| RLS | aucune | **aucune** | aucune |
| PostGIS | migration optionnelle avec repli | aucune | aucune |

Un produit multi-tenant sans cadre de migration ne peut pas évoluer en production : la §5 exige
« un test de migration qui énumère les tables et échoue si l'une manque `tenant_id` ou sa
politique » — il n'y a rien pour l'accrocher.

*Proposition* : phase 1 = **modèle tenant d'`atlasagri`, socle de persistance d'`agriflow`**
(session async + Alembic + le motif de migration PostGIS optionnelle), les modèles d'`atlasagri`
portés en async et re-décrits en révisions Alembic. La frontière de phase ne bouge pas ; son
contenu, si. R8 en dépend : un `Session` synchrone conservé dans le registre d'outils, appelé
depuis une boucle d'agent async, ne casse rien de visible — il sérialise silencieusement.

---

### 7.4 §1 règle 4 — l'affirmation « il y a des tests pour cela dans `txtsql` et `atlasagri` » est fausse pour moitié

`txtsql` a `tests/integration/test_concurrency_and_injection.py` et une consigne d'enveloppe
dans `prompts/v1.py` (`<question>`, `<query>`, `<result_data>` déclarés non fiables).

`atlasagri` a `core/untrusted.py` avec `wrap_untrusted()` — **qui n'est appelé nulle part**.
Seul `sanitize_user_text` est utilisé, sur la question de l'utilisateur. Les charges météo, les
lignes de base et les notes de terrain arrivent au modèle via `_serialize(outcome)`, c'est-à-dire
en JSON brut, hors enveloppe. Aucun test d'injection n'existe dans `backend/tests/`.

La consigne de la §1 — « étendez-les, ne les réécrivez pas » — conduirait à étendre une garantie
absente. *Décision* : câbler `wrap_untrusted` sur tout champ textuel d'origine externe dans les
résultats d'outils, et **écrire** les tests. Une ligne entre dans `docs/ce-qui-nest-pas-mesure.md`
dans le même commit, avant que le code ne soit écrit.

---

### 7.5 §4.1 — deux axes, oui, mais la table de correspondance est incomplète et un troisième vocabulaire existe déjà

La §4.1 traduit trois valeurs d'`agriflow` sur huit. Les cinq absentes ne sont pas des détails :

| `agriflow.DataSource` | §4.1 | Couple proposé | Remarque |
|---|---|---|---|
| `MANUAL` (`USER_INPUT`) | ✓ | `(OBSERVED, MANUAL_ENTRY)` | |
| `ESTIMATED` | ✓ | `(DERIVED, MODEL)` | |
| `SIMULATED` | ✓ | `(SIMULATED, SEED_DEMO)` | |
| `SENSOR` | ✗ | `(OBSERVED, SENSOR)` | |
| `WEATHER_API` | ✗ | `(OBSERVED, EXTERNAL_API)` **ou** `(FORECAST, EXTERNAL_API)` | **l'état dépend de la ligne, pas de la colonne** : une correspondance enum→couple ne suffit pas, c'est le service qui décide |
| `DATABASE` | ✗ | `(DERIVED, REFERENCE_TABLE)` | |
| `CALCULATED` | ✗ | `(DERIVED, MODEL)` | collision avec `ESTIMATED` : deux origines distinctes chez `agriflow` (Kc estimé depuis la date de plantation ≠ ET0 calculée) écrasées sur le même couple. Il faut `(DERIVED, MODEL)` pour un calcul et `(INFERRED, MODEL)` pour une estimation |
| `DEFAULT` | ✗ | **aucun** | voir ci-dessous |

**`DataSource.DEFAULT` doit disparaître, pas être traduit.** Il pèse 0,4 dans
`explain._SOURCE_WEIGHT` et sert de repli à `_fill_data_quality`. C'est littéralement « une
valeur plausible substituée à une valeur manquante », soit la doctrine §1.3 violée dans le
dépôt que la spécification cite en exemple sur ce point. *Décision* : supprimer, et rendre la
sortie dépendante indisponible — comme `scenarios.py` le fait déjà correctement pour Ky.

**Et un troisième vocabulaire existe.** `atlasagri.DataSourceRef {id, label_fr, kind, detail}`
n'est pas `DataOrigin` : c'est une **référence à une source nommée** (« Open-Meteo, archive
ERA5 »), pas une catégorie. Les deux sont nécessaires et répondent à des questions
différentes — `DataOrigin` porte le poids de fiabilité et la contrainte de schéma,
`DataSourceRef` remplit le panneau de preuves. La §4.1 en décrit deux et il y en a trois ; le
dire maintenant évite qu'on n'aplatisse `DataSourceRef` dans `DataOrigin` en phase 2.

---

### 7.6 §7 — la carte de décision est aujourd'hui rédigée par le modèle, ce qui contredit la §1.1

`atlasagri` obtient sa sortie structurée en demandant au modèle de terminer par un bloc
```` ```json ```` que `_extract_structured` récupère par expression régulière. Le prompt est
prudent (« ne remplis ce bloc qu'avec des valeurs issues des outils »), et le bloc ne contient
aucun montant. Mais il contient `niveau_risque` et `confiance` : deux valeurs que
`RankingResult` a **déjà calculées** et que le modèle **re-énonce**. Si le modèle écrit
« Modéré » là où le moteur a produit `ÉLEVÉ`, c'est la valeur du modèle qui s'affiche sur la
carte. Une consigne de prompt est un indice, pas une garantie (§1.5).

*Proposition* : le modèle ne rend qu'un `option_recommandee_id` et des raisons rédigées ; tous
les champs affichés (niveau de risque, confiance, écarts de coût et de délai, tronçons exposés)
sont relus depuis le `RankingResult` **stocké dans la trace d'outil**, par jointure sur cet
identifiant. Si l'identifiant ne correspond à aucune option renvoyée, la carte n'est pas
affichée. Coût : faible. Effet : la carte devient structurellement incapable d'afficher un
chiffre que le moteur n'a pas produit. Cela retire aussi l'analyse d'un bloc Markdown du chemin
critique.

---

### 7.7 §4.5 — d'accord sur le fond, avec deux précisions que la spécification ne donne pas

« Une seule cache, un seul jeu de tables `weather_observations` / `weather_forecasts` » sous-
estime le problème (D10). Le grain diffère : journalier chez `agriflow`, horaire chez
`atlasagri`. *Décision* : **horaire au stockage, journalier en vue dérivée.** Cela améliore
l'ET0, car FAO-56 définit RHmax/RHmin comme extrema horaires, que le stockage journalier ne
peut pas reconstituer.

Second point : `atlasagri` demande déjà `et0_fao_evapotranspiration` à Open-Meteo. Il existe
donc **deux ET0**. La §4.5 a raison de retenir celle d'`agriflow`. Mais jeter la valeur du
fournisseur serait un gâchis : conservée comme **contrôle croisé**, un écart supérieur à un
seuil configuré devient un signal de qualité de données (station aberrante, coordonnées
fausses, unité de vent erronée) — sans jamais être affichée comme une ET0 concurrente.

---

### 7.8 §4.4 — d'accord, et le dépôt le dit déjà

`text_to_sql/README.md` documente déjà l'échec silencieux de `bge-small-en-v1.5` et recommande
`intfloat/multilingual-e5-small`. La §4.4 est correcte et sans coût. Une seule addition :
le changement de modèle règle la **récupération** (trouver la bonne table à partir d'une
question française) ; il ne règle pas la **correspondance de valeurs** (« annulé » →
`'cancelled'`), qui passait par `sample_values`. Voir §7.1.g : registre d'énumérations déclaré
dans le code.

---

### 7.9 §7 — « conserver les tests XSS » conserve des tests qui ne survivent pas au portage

Les tests que la §7 demande de conserver (`innerHTML`, `insertAdjacentHTML`, `document.write`
absents des chemins de rendu) vivent dans `txtsql/tests/unit/test_web_page.py` et portent sur
`app/web/index.html`, un client vanilla que le portage React supprime. Les deux frontends React
n'ont **aucune infrastructure de test** (ni Vitest ni Testing Library dans les deux
`package.json`).

Le principe est juste — `customers.name` valant `<img src=x onerror=…>` est une ligne réaliste,
et une base multi-tenant avec saisie libre en produira. Mais il faut dire que ces tests sont à
**écrire**, pas à conserver, sinon la phase 5 les cochera comme reprises. Portage minimal :
règle ESLint `react/no-danger` en erreur + un test de rendu par composant affichant une valeur
issue de la base.

---

### 7.10 §8 et §11 phase 8 — l'image de conteneur est un problème de phase 1

Le produit fusionné a besoin de **PostGIS et pgvector dans le même serveur**. `postgis/postgis:16-3.4`
(utilisée par `agriflow`) n'a pas pgvector ; `pgvector/pgvector:pg16` (utilisée par `txtsql`)
n'a pas PostGIS ; `atlasagri` n'a **ni Dockerfile ni docker-compose**. Reporter cela en phase 8
signifie développer les phases 2 à 7 sur une base de données qui ne sera jamais celle de
production, et découvrir en fin de parcours quelle extension manque. *Décision* : image dérivée
construite et démarrée en phase 1, et la ligne 8 du registre d'honnêteté fermée à ce
moment-là — pas plus tôt, puisque rien n'a encore été construit ici.

---

## 8. Ajouts au registre d'honnêteté

À écrire dans `docs/ce-qui-nest-pas-mesure.md` **dans le commit qui ouvre la phase 1**, pas
plus tard :

| # | Affirmation non démontrée | Ce qui la comblerait |
|---|---|---|
| 11 | L'enveloppe de contenu non fiable n'est appliquée à aucun résultat d'outil ; `wrap_untrusted` est du code mort et aucun test d'injection n'existe côté plateforme métier | câblage + suite adverse par outil |
| 12 | Aucun rendu React n'est testé contre l'injection de contenu de base de données | Vitest + un test par composant affichant une valeur |
| 13 | Aucune isolation par RLS n'existe dans aucun des trois dépôts : l'isolation est aujourd'hui uniquement applicative | migration + sonde de démarrage inter-tenants + test aux 5 couches |
| 14 | Les valeurs d'exemple de l'index de schéma proviennent de `pg_stats` sur des tables multi-tenants | registre d'énumérations déclaré ; interdiction d'échantillonner une table portant `tenant_id` |
| 15 | Le corpus adverse de 36 cas ne contient aucun cas de franchissement d'organisation | ~9 cas d'intégration exécutés contre une base RLS, comptés séparément |

Les lignes 1 à 10 existantes restent valides ; aucune n'a été fermée par cette phase.

---

## 9. Résumé des demandes de décision

Rien ici ne bloque le démarrage de la phase 1, sauf accord sur deux points :

| | Point | Recommandation | Impact si refusée |
|---|---|---|---|
| **A** | §4.3 : politiques sur `app`, vues `analytics` détenues par un rôle distinct, `FORCE` en invariant testé, tenant lié à la connexion | adopter | la conception actuelle est inimplémentable (sonde L) et sa variante naturelle fuit (sonde E) |
| **B** | §4.6 : deux moteurs, un contrat de sortie ; `OptimizationService` généralisé sur 3 domaines | adopter | ~1 200 lignes réécrites pour un nom de classe partagé, et un vecteur de poids agronomique inventé |
| **C** | §11 phase 1 : socle de persistance d'`agriflow`, modèle tenant d'`atlasagri` | décidé, documenté | — |
| **D** | §4.1 : suppression de `DataSource.DEFAULT`, conservation de `DataSourceRef` comme troisième vocabulaire | décidé, documenté | — |
| **E** | §7 : carte de décision reconstruite depuis la trace d'outil, pas depuis le bloc JSON du modèle | décidé, documenté | — |

Conformément à la §15 bis, C, D et E sont appliquées sans attendre et feront l'objet d'un
fichier dans `docs/decisions/`. A et B touchent des résolutions mandatées de la §4 : la phase 0
s'arrête ici pour votre revue.
