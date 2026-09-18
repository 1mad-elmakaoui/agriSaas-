# MASTER PROMPT — Unify three existing systems into one production Moroccan agri-SaaS

> Paste this whole file as the first message of a fresh Claude Code session. Clone the three
> repositories listed below, or have them present on disk. One placeholder remains: the
> product name.

---

## 0. Who you are and what this is

You are the lead engineer on a **merge and productionisation project**, not a greenfield build.
Three working systems already exist. All three were written to a high standard, all three
contain honest "what has not been measured" sections, and all three share one design doctrine:
**deterministic Python computes; the language model interprets, compares and explains.**

Your task is to fold them into a single multi-tenant SaaS product for Moroccan agricultural
enterprises and cooperatives, without losing the property that made each of them credible.

**The repositories:**

| Repo | Path / remote | What it contributes |
|---|---|---|
| `agriflow-ma` | `github.com/imadelmakaoui-UJ/agriflow-` — branch `claude/agriflow-backend-startup-fobs8u` | FAO-56 irrigation engine (ET0, ETc, water balance, dose, scenarios), agronomic reference tables, per-value provenance, French explanation panel, 19-tool Claude agent |
| `atlasagri` | `github.com/imadelmakaoui-UJ/atlasagri` — branch `claude/start-175noj` | Multi-tenant core, auth/roles/audit, single business-tool registry serving both MCP and the agent, nowcasting baseline, agro + supply-chain risk engines, route/alternative/optimisation engines, MapLibre operational map, provenance `DataState` |
| `txtsql` | `github.com/imadelmakaoui-UJ/text_to_sql` — default branch | Text-to-SQL agent: four-layer safety stack, pgvector schema retrieval with FK expansion, repair loop with AST fingerprinting, eval harness, per-run tracing with token/cost accounting |

**Product name:** `<<<PRODUCT_NAME>>>` (if left blank, use `AtlasAgri` and keep the tagline
*Prévoir. Anticiper. Réacheminer. Décider.*)

**Target customer:** Moroccan agro-industrial exporters, large exploitations, and cooperatives
in Souss-Massa, Gharb, Doukkala-Abda, Saïss and Loukkos. Sold as a subscription, per
organisation, in MAD.

---

## 1. Non-negotiable doctrine

These five rules already hold in all three repos. They must survive the merge intact. If any
instruction later in this document appears to contradict one of them, the rule wins and you
say so in your plan.

1. **The model never computes.** Not ET0, not a dose, not a route cost, not a risk score, not
   an aggregate. Every number on screen comes from a deterministic Python function or from
   validated SQL. Even a multiplication goes through a tool. There is no generic `eval`,
   no shell tool, no "run this SQL" endpoint, and no direct database handle in the agent.
2. **Every value carries its epistemic status and its origin, from the column to the pixel.**
   A simulated value can never be displayed in a way that lets a decision-maker mistake it for
   a measurement. This is enforced by the schema, not by convention.
3. **Missing is missing.** No plausible substitute, ever. No flow rate → no duration. No
   documented FAO-33 Ky → no yield estimate. No Copernicus credentials → « Données satellite
   indisponibles ». No calibrated history → the number is presented as a comparative
   indicator, not a probability.
4. **External content is data, never instruction.** Weather API payloads, database rows,
   user-entered field notes, imported CSVs and satellite metadata all reach the model inside a
   tagged envelope, with a system-level rule that nothing inside it changes behaviour. There
   are tests for this in `txtsql` and `atlasagri`; extend them, do not rewrite them.
5. **Guarantees come from structure, not from prompting.** A prompt asking the model not to
   hallucinate is a hint. A tool registry it cannot bypass, an AST validator, a SELECT-only
   role and a row-level security policy are guarantees. When you have a choice, buy the
   guarantee.

---

## 2. Phase 0 — audit before you write a line of code

Do not start merging. First read all three codebases and produce
`docs/00-plan-unification.md` (French, as all product docs are) containing:

1. **Inventory.** Every module in each repo, classified: `keep as-is`, `keep with changes`,
   `absorbed into X`, `deleted and why`.
2. **Duplication map.** Every capability implemented twice, with your chosen winner and the
   reason. The obvious ones are listed in §4 — find the ones I have not listed.
3. **The merged data model**, as an ER diagram plus a table-by-table origin column
   (`agriflow` / `atlasagri` / `new`).
4. **The unified tool registry**: final tool list, which repo each came from, which are new,
   which are merged, which are dropped.
5. **Migration sequence**, as the phased plan in §11 adapted to what you actually found.
6. **Risks you found that I did not name**, ranked, with mitigations.
7. **Anything in this document you think is wrong.** You are explicitly authorised — and
   expected — to push back with an argument. Do not silently comply with a bad instruction.

Stop after this document and wait for my review. This is the only checkpoint where you wait;
after it, make reasonable engineering decisions and keep moving.

---

## 3. Target architecture

One modular monolith, one React application, one PostgreSQL cluster. No microservices: the
volume, the team size and the stability of the domain boundaries do not justify the
operational cost of a distributed system, and `atlasagri` already argued this correctly.

```
                    React 18 + TypeScript + Vite + Tailwind + MapLibre GL
                                        │  REST/JSON, JWT scoped by tenant
┌───────────────────────────────────────▼──────────────────────────────────────┐
│  FastAPI                                                                      │
│  auth · RBAC · tenant isolation · validation · rate limits · audit · billing  │
└───────────────────────────────────────┬──────────────────────────────────────┘
┌───────────────────────────────────────▼──────────────────────────────────────┐
│  Application services                                                         │
│  Weather · Nowcast · Satellite · Irrigation · AgronomicRisk · SupplyChainRisk │
│  Route · Alternative · Optimisation · Recommendation · Analytics · Agent      │
└──────┬────────────────────────────────────────────────┬──────────────────────┘
       │                                                │
┌──────▼─────────────────┐                  ┌───────────▼──────────────────────┐
│ Domain — pure Python   │                  │ Business tool registry (SINGLE)  │
│ FAO-56 engine, water   │                  │ name · description · Pydantic in │
│ balance, thresholds,   │                  │ typed out · roles · audit hook   │
│ scoring, constraints,  │                  └───────┬─────────────────┬────────┘
│ optimisation. No I/O.  │                          │                 │
└──────┬─────────────────┘                  ┌───────▼──────┐   ┌──────▼────────┐
       │                                    │  MCP server  │   │ Claude agent  │
┌──────▼─────────────────┐                  │   (stdio)    │   │  (real API)   │
│ Repositories (SQLA 2.0)│                  └──────────────┘   └───────────────┘
└──────┬─────────────────┘
┌──────▼───────────────────────────────────────────────────────────────────────┐
│ PostgreSQL 16 + PostGIS + pgvector                                            │
│ operational schema (RLS) · analytics schema (RLS, read-only role) · vector idx│
│ Adapters: Open-Meteo · Copernicus · OSRM · payment gateway · object storage    │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Dependency rule, enforced by a test:** `api → services → domain`, and
`services → repositories/adapters`. `domain/` imports neither SQLAlchemy, nor FastAPI, nor
httpx, nor `anthropic`. That is what keeps the agronomy auditable by an agronomist who does
not read web code, and testable in microseconds.

**One database cluster, three logical schemas:** `app` (operational, RLS on every
tenant-scoped table), `analytics` (the surface the text-to-SQL agent may see — views only,
RLS enforced, no base tables), `meta` (pgvector schema index, prompt versions, eval runs).

---

## 4. Mandated conflict resolutions

These are the collisions between the three repos. I have decided them. Implement them; argue
in Phase 0 if you disagree.

### 4.1 Provenance — one vocabulary, two orthogonal axes

`agriflow` tags soil moisture `manual | sensor | estimated | simulated`. `atlasagri` tags every
exposed value `OBSERVED | FORECAST | DERIVED | INFERRED | SIMULATED`. These are not the same
question and must not be flattened into one enum.

Adopt **two columns everywhere a value is exposed**:

```
DataState  (epistemic)   OBSERVED · FORECAST · DERIVED · INFERRED · SIMULATED
DataOrigin (provenance)  SENSOR · MANUAL_ENTRY · EXTERNAL_API · REFERENCE_TABLE ·
                         MODEL · SEED_DEMO
```

`agriflow`'s `manual` becomes `(OBSERVED, MANUAL_ENTRY)`; `estimated` becomes
`(DERIVED, MODEL)`; `simulated` becomes `(SIMULATED, SEED_DEMO)`. The pair travels
database → domain object → API response → a source chip in the UI. Reliability scoring weights
`SENSOR > MANUAL_ENTRY > EXTERNAL_API > MODEL > SEED_DEMO`, which is why the demo tenant reports
« Fiabilité : Moyenne ». Keep `agriflow`'s test asserting a client cannot claim a manual reading
came from a sensor, and generalise it: **no HTTP endpoint may accept `DataOrigin` from the
caller.** The server decides.

### 4.2 One tool registry, one agent

`agriflow` has 19 agent tools; `atlasagri` has ~20 registry tools exposed over MCP. Two agents
and two registries would diverge in silence — exactly the failure `atlasagri`'s architecture
document warns about. Therefore:

- `atlasagri`'s `app/tools/registry.py` is the single source of truth. It wins.
- Every `agriflow` tool is re-registered there with a Pydantic input schema, a typed output
  schema, allowed roles, and an audit hook. `calculate_irrigation_requirement` in particular
  keeps its behaviour of chaining the whole computation inside Python and returning one
  structured recommendation — that design is correct and is why the agent cannot drift.
- `tenant_id` is **never a tool parameter.** It is injected from the authenticated execution
  context. Claude cannot cross a tenant boundary even if explicitly asked to. Test this.
- One agent loop, bounded by `AGENT_MAX_TOOL_ITERATIONS`. Keep `agriflow`'s two hard-won
  details: **all tool results go back in a single user message** (splitting them trains the
  model out of parallel calls), and **a failing tool becomes a French error payload the model
  can relay, never an exception that kills the conversation** — with nothing substituted for
  the missing result.
- Keep the manual loop rather than the SDK tool runner, for the same two reasons `agriflow`
  documents: request-scoped database session injection, and an exact tool trace to hand to the
  UI so a user can audit which call produced each figure.

### 4.3 Text-to-SQL meets multi-tenancy — the hardest problem in this merge

`txtsql` explicitly lists multi-tenancy as a non-goal. It is now a hard requirement, and its
four-layer stack does not address it: **a SELECT-only role isolates writes, not tenants.** A
correct, validated, cheap `SELECT * FROM shipments` returns every organisation's shipments.

Resolve it structurally, in the spirit of the existing layers:

- **Layer 2 becomes the tenant guarantee.** The analytics role gets `SELECT` only, and every
  table in `analytics` carries a `FORCE ROW LEVEL SECURITY` policy on `tenant_id` keyed to a
  session GUC (`app.current_tenant`), set with `SET LOCAL` inside the same read-only
  transaction as the query. Like the original layer 2, this holds **even when our own code is
  the bug** — which is precisely why it must be the load-bearing layer and not the AST check.
- **Extend layer 1** with a rule: every referenced relation must appear in an allowlist of
  RLS-protected analytics views. An unrecognised relation is refused, on the same "we have not
  heard of it is not evidence that it is safe" logic as the function allowlist.
- **Extend the startup probe.** `atlasagri` refuses to boot in production if `CREATE TEMP TABLE`
  succeeds on the analytics role. Add a second probe: open a session as tenant A, query a
  fixture row belonging to tenant B, and refuse to boot if it returns. RLS that is silently
  disabled looks exactly like RLS that works.
- **The pgvector schema index is per-tenant-visible catalog.** If tenants can add custom
  fields or crops, another tenant's identifiers must not appear in retrieval, because the
  schema panel and the generated SQL both leak them.
- Add tenant-crossing attempts to the adversarial corpus. The corpus currently has 36 cases at
  100%; this makes it ~45 and the number will drop. **That is the point.** Do not tune the
  corpus to keep the headline.

### 4.4 Retrieval must be multilingual from day one

`txtsql` defaults to `BAAI/bge-small-en-v1.5`, which is English-only and, as its own README
says, **fails silently**: it still returns tables, so the query is generated, validated,
executed and explained against the wrong ones, with nothing reporting a problem. Questions here
arrive in French and Arabic.

Default to `intfloat/multilingual-e5-small` (also 384-dimensional, so the pgvector column is
unchanged) and re-index. Carry over the model-identifier prefix handling (`query:` / `passage:`)
and the startup refusal when a configured dimension contradicts a known model. Keep the
`retrieval_cross_lingual` script-based warning. Run the multilingual eval suite against a live
model before launch — `txtsql` admits it never has.

### 4.5 Weather, ET0 and nowcasting — one path

Three near-duplicates exist: `agriflow`'s `WeatherService` (PostgreSQL-cached, TTL-bounded),
`atlasagri`'s `WeatherProvider` adapter, and `atlasagri`'s nowcasting baseline.

- One `WeatherProvider` abstraction with `_PROVIDERS` registration. Open-Meteo default, no key.
  OpenWeatherMap second. Keep the offline provider — but keep its rule that every value is
  labelled `SIMULATED` and shown as such.
- One cache, one set of `weather_observations` / `weather_forecasts` tables.
- **ET0 becomes a shared service.** `agriflow`'s Penman-Monteith implementation with its
  Hargreaves-Samani fallback and its five FAO-56 worked-example tests is the best-validated
  code in any of the three repos. Expose it to the agronomic risk engine and to the nowcasting
  variable set. Do not reimplement it.
- **Nowcasting feeds irrigation.** `agriflow` discounts forecast rain by probability and a
  global reliability factor. The 6/12/24/48 h nowcast is a better short-horizon input than a
  raw daily forecast. Wire it in, keep the discounting discipline, and keep
  « Irrigation à reporter (pluie prévue) » as an outcome distinct from a reduced dose.
- Keep the baseline. Do not train a GRU or LSTM in this phase. `atlasagri`'s argument stands:
  without validated multi-season Moroccan hourly history, a neural model produces forecasts
  whose quality cannot be measured or defended. The `NowcastModel` interface stays so a
  successor can be dropped in and must beat the baseline in strictly chronological validation.

### 4.6 Decisions and alternatives — one engine, two domains

`agriflow` has a scenario simulator (−10/−20/−30 % irrigation, water balance rolled forward
with Ks-reduced uptake). `atlasagri` has an `AlternativeEngine` + hard constraints +
multi-criteria optimisation. These are the same shape: *generate candidates → filter infeasible
→ score by weighted criteria → rank → explain → keep the discarded ones with their reason.*

Unify into one `DecisionEngine` with domain-specific candidate generators:

| Domain | Candidates |
|---|---|
| Irrigation | irrigate now · postpone for rain · split the dose · deficit at −10/−20/−30 % · skip the turn |
| Logistics | alternative route · departure shift · alternative carrier · split shipment |
| Sourcing | alternative supplier · split sourcing · geographic diversification |
| Inventory | reposition · inter-warehouse transfer · raise safety stock · reallocate |

Keep both properties that matter: **hard constraints filter before any ranking** (ranking an
infeasible option is worse than useless — it is a dangerous recommendation), and **weights are
per profile, never universal** (refrigerated tomatoes weight risk and time; bulk grain weights
cost; a water-scarce perimeter under quota weights volume above yield).

Keep `atlasagri`'s demo finding where the coastal route is *worse* because of 90 km/h gusts.
A system that always finds the obvious answer helps nobody.

### 4.7 PostGIS becomes required in production

Both repos deferred it. With field polygons, route segments, risk zones and Sentinel-2 zonal
statistics in one product, it is now justified. But keep `agriflow`'s design: **the canonical
boundary stays a portable GeoJSON column**, a migration adds the `geometry(Polygon, 4326)`
mirror with a GiST index when the extension is present, and Python computes geodesic area with
the spherical-excess formula so the figure a farmer sees is identical either way. Keep the
0.3 % agreement test against `ST_Area(...::geography)`. Development without PostGIS must still
boot, with a logged warning and spatial features disabled — not crashed.

### 4.8 Map: one stack

MapLibre GL JS everywhere; migrate any Leaflet view. Basemap: **self-hosted Protomaps PMTiles
from a Morocco OSM extract**, served from object storage. No per-tile vendor billing, works in
a restricted network, and it is the right call for a product whose customers are cost-sensitive
and sometimes offline in the field. Keep a configurable raster fallback.

One layer registry so the AI recommendation drives the map (`atlasagri` §47): when a
recommendation is selected, the map highlights the chosen option, dims the ranked-lower ones,
outlines the risk zone that caused the decision, and opens the evidence panel.

### 4.9 Language

- **Product UI: French**, complete, including error messages and empty states.
- **Arabic (`ar-MA`): full i18n scaffolding now, RTL layout supported, strings extracted.**
  Ship French first; Arabic when a native reviewer is available — do not machine-translate an
  agronomic interface and call it localised.
- **Code, identifiers, SQL, column names, stored enum values and commit messages: English.**
  A French question about *annulé* still filters on `'cancelled'`, because that is what is in
  the column. Keep that rule from `txtsql` and test it.
- Structural error messages and the deterministic fallback summary stay English internally and
  are mapped to French at the boundary — never translated by the model call that just failed.

### 4.10 Observability

`txtsql`'s tracing is the best of the three. Make it platform-wide: every service node emits a
trace in a `finally` block so a node that raises still contributes one; model calls reach the
trace through a `ContextVar` sink so `app/llm` knows nothing about graph nodes; a whole request
is reconstructable from its `run_id` including every repair attempt and the exact error that
caused it; a model missing from the pricing table reports cost as **unknown, never zero**.

Per-tenant token and cost accounting rolls up into billing (§6).

---

## 5. Merged data model

Reconcile — do not concatenate. Notable merges:

- **`Site` (polymorphic: farm · warehouse · hub · customer) absorbs `agriflow.farms`.** These
  objects share coordinates, region and capacity; three tables would duplicate the same
  geolocation column. `Field` belongs to a `Site` of type `farm`.
- **`agriflow`'s FAO reference tables are the agronomic grounding `atlasagri` admits it lacks.**
  `crops`, `crop_growth_stages`, `soil_profiles`, `irrigation_systems` carry Kc, stage lengths,
  rooting depths, depletion fractions, field capacity, wilting point, application efficiency and
  FAO-33 Ky — each row citing its source table (FAO-56 Tables 11/12/19/22, FAO-33). The
  agricultural impact engine consumes these instead of free-standing thresholds. Where FAO
  documents no value the column stays `NULL` and the dependent output is reported unavailable.
- **Reference data is global and read-only; tenants may override.** A `soil_profiles` row with
  `is_measured = true` and a `tenant_id` shadows the reference row. Local calibration must
  remain a data task, not an engineering one.
- **Every tenant-scoped table carries `tenant_id`, indexed, with an RLS policy.** A migration
  test enumerates tables and fails if one lacks either.
- **Recommendations persist with their full explanation**, inputs, sources, alternatives
  considered with rejection reasons, optimisation result, agent explanation and the human
  decision. A decision must be auditable months later even after the weather data and crop
  parameters have changed. This is also the dataset that makes §6's avoided-loss quantification
  possible later, and the most interesting thing the analytics agent can query.

---

## 6. The SaaS layer — new, none of the three repos has it

- **Tenant provisioning**: self-serve signup → organisation → seeded reference data → guided
  onboarding (draw your first parcelle on the map, choose crop and soil, enter one moisture
  reading, get your first recommendation). Time-to-first-value under ten minutes.
- **Plans and quotas**: `Coopérative` / `Exploitation` / `Entreprise`, metered on parcelles,
  shipments, agent messages and analytics queries. Quotas are enforced server-side with clear
  French messages, never a silent truncation. **No payment processing in this scope** — plans
  are provisioned by an administrator. The metering data is what a billing integration would
  later consume; do not build the integration.
- **Usage metering** reuses the tracing cost accounting: LLM spend per tenant is real data, not
  an estimate, and a plan can be limited on it.
- **Roles**: `ADMIN · SUPPLY_CHAIN_MANAGER · OPERATIONS_MANAGER · AGRONOME · ANALYST ·
  EXECUTIVE`. `AGRONOME` is new and matters — it is the role that calibrates thresholds and
  approves reference overrides.
- **Compliance**: Morocco's Loi 09-08 / CNDP. Data residency statement, retention policy,
  export and deletion endpoints, audit log of every access to another user's data. Write
  `docs/conformite.md` and be honest about what is implemented versus planned.

---

## 7. Frontend information architecture

```
Vue générale     risque global · alertes · parcelles à irriguer · expéditions exposées ·
                 stocks critiques · opportunités d'action
Parcelles        liste + carte, état hydrique, recommandation, « Pourquoi cette décision ? »
Irrigation       plan d'irrigation, tours d'eau, volumes, coûts, historique
Carte            surface opérationnelle unique : parcelles, sites, itinéraires, zones de risque
Risques          registre, filtres, horizon, calibration des seuils (rôle agronome)
Expéditions      liste puis détail : carte + alternatives + recommandation + preuves
Stocks           couverture, points de commande, ruptures potentielles
Fournisseurs     exposition, délais, alternatives de sourcing
Alternatives     comparaison visuelle des options pour une perturbation
Simulations      what-if, situation actuelle vs simulée
Analyse          questions en langage naturel sur l'entrepôt de données (text-to-SQL)
Copilote IA      interface langage naturel du moteur de décision — jamais la page d'accueil
Alertes          QUOI / OÙ / QUAND / IMPACT / ACTION
Administration   organisation, utilisateurs, rôles, abonnement, facturation, journal d'audit
```

**Design constraints.** Restrained enterprise palette; colour is semantic only
(vert faible · jaune modéré · orange élevé · rouge critique · bleu recommandé). No rainbow
dashboard. The agent's answer is **never rendered as a paragraph** — it is transformed into
structured UI: decision, risk delta, cost delta, SLA badge, ranked reasons, evidence panel, map
actions. Free-form prose from the model is explicitly forbidden as the primary output.

Two panels are load-bearing and must be built well:

- **« Pourquoi cette décision ? »** — inputs with their `DataState`/`DataOrigin` chips, numbered
  calculation steps with the FAO equation number where applicable, assumptions, data-quality
  checklist, and the tool trace under « Voir les calculs ».
- **« Sources et preuves »** — observation, forecast, satellite indicator, inventory, supplier,
  route, historical pattern, model confidence. Business language on the surface; technical
  detail behind a disclosure. Never a raw log.

**Security carry-over from `txtsql`'s browser client**: everything reaches the DOM through
`textContent`. A `customers.name` of `<img src=x onerror=...>` is a realistic row in any
warehouse accepting user input. Keep the tests asserting `innerHTML`, `insertAdjacentHTML` and
`document.write` appear nowhere in rendering paths, adapted to React (`dangerouslySetInnerHTML`
is banned outside a single audited markdown renderer, if you need one at all).

Consult the `frontend-design` skill before building UI. This must not look like an admin
template.

---

## 8. External adapters and their real state

| Provider | Use | Required state |
|---|---|---|
| Open-Meteo | hourly forecast, history, soil, ET0 | Real, no key. Default. |
| OpenWeatherMap | second source | Real, key optional. Removes a single point of failure. |
| Copernicus Data Space (Sentinel-2) | NDVI / NDWI, zonal statistics per parcelle, SCL cloud masking | Adapter exists in `atlasagri` but **the network round-trip has never been executed** — the dev environment blocks third-party hosts. Getting one real OAuth exchange and one real statistical response is a launch blocker. Until then: « Données satellite indisponibles ». Never estimated. |
| OSRM | real road geometry | Adapter exists, unverified against a live host. Default remains the embedded reference road graph (real Moroccan cities and axes, reference road distances). Good enough to compare options; insufficient for turn-by-turn, and the product must say so. |
| Object storage | PMTiles, invoices, satellite crops | S3-compatible. |

Every adapter degrades explicitly: refused credentials or an unreachable service produce a
French message naming the fix, never a substitute value.

---

## 9. Security

Carry over what exists, then add:

- JWT auth, RBAC, tenant isolation enforced in the repository layer **and** by RLS.
- Rate limits, with a dedicated stricter one on the copilot and the analytics agent (each
  question costs real money).
- Prompt-injection defence: tagged envelopes for all external content; no tool performs an
  irreversible action; `create_recommendation` creates a proposal awaiting human approval, never
  an execution. Re-audit at every new tool.
- Secrets from the environment only. `.env.example` documents every variable. **Never a key in
  `frontend/.env`** — anything there is bundled into the JavaScript and publicly visible. The
  browser never talks to a weather, satellite or model provider directly.
- Audit log: who saw what, who approved what, who changed a threshold.
- Configurations refused at boot in production, extending `txtsql`'s list: auth disabled or no
  API key; lexical-only embedding provider; fake LLM provider; analytics DSN equal to the
  operational DSN; analytics role able to `CREATE TEMP TABLE`; RLS cross-tenant probe returning
  a row; `DEMO_MODE=true`.

---

## 10. Testing and evaluation

Merge the suites (`agriflow` pytest, `atlasagri` 131 tests, `txtsql` 536 tests) into one run.
Nothing is deleted to make the count look tidy; a test that no longer applies is deleted with a
one-line reason in the commit.

Keep the three test philosophies, because they are different and all correct:

- **`agriflow`: numerical truth.** Five ET0 tests reproduce published FAO-56 worked examples to
  within rounding. Reducing irrigation never reduces stress. A crop without a documented Ky
  produces no yield figure.
- **`atlasagri`: refusal to conclude.** Calibration on simulated data is refused. An
  agronomically absurd threshold is refused. A reliability score on an insufficient sample is
  refused. A guard asserts no Colombian threshold from the source paper was ever copied. **A
  product that shows a confident wrong number is more dangerous than one that says it does not
  know.**
- **`txtsql`: adversarial corpus + golden set + honest reporting.** Extend the corpus with
  tenant-crossing cases (§4.3). Add golden sets for irrigation decisions, risk levels and agent
  tool selection. Report the metric that drops.

Also required:
- Tenant isolation: a fixture with two organisations, asserted at the API, repository, RLS,
  tool-registry, MCP and analytics layers.
- Dependency rule: `domain/` imports nothing from infrastructure.
- Provenance: no exposed value lacks `DataState` + `DataOrigin`; no endpoint accepts either
  from the caller.
- A `pytest -q` suite that runs in CI without network and without an Anthropic key, degrading
  the report rather than the measurement — `txtsql`'s split-by-credential design.

---

## 11. Phased delivery

Every phase ends with the whole product running end-to-end. No phase leaves the tree broken.
Commit at each numbered step with a message explaining *why*, not *what*.

**Phase 1 — Skeleton and core.** Monorepo, unified settings, PostgreSQL 16 + PostGIS + pgvector,
auth, RBAC, tenant model, RLS on every tenant table, audit log, migration framework, CI.
`atlasagri`'s core lifted and hardened. *Done when:* two organisations exist, cannot see each
other, and a test proves it at five layers.

**Phase 2 — Data model and reference data.** Merged schema, FAO seed data with source citations,
demo tenant clearly flagged, `Site`/`Field` reconciliation, provenance columns everywhere.
*Done when:* seeding produces a coherent Souss-Massa and Gharb dataset and every value shows its
two provenance tags.

**Phase 3 — Deterministic engines.** ET0/ETc/water balance/dose/scenarios from `agriflow`; risk,
route exposure, alternatives, optimisation from `atlasagri`; nowcasting baseline; unified
`DecisionEngine`. Pure, no I/O, fully unit-tested. *Done when:* the FAO worked examples pass and
the ranked-alternatives output includes rejected options with reasons.

**Phase 4 — Tool registry, MCP, agent.** One registry, both consumers, tenant injected from
context, single-message tool results, French error payloads, structured final response, full
tracing. *Done when:* « Est-ce que je dois irriguer P03 ? » and « Mon transport de tomates vers
Casablanca est-il à risque ? » both work through the same agent, and the tool trace renders.

**Phase 5 — Frontend.** MapLibre + PMTiles, the pages in §7, the two explanation panels, the
recommendation-drives-the-map coupling, i18n scaffolding, full French. *Done when:* the demo
flow in §12 runs without a developer present.

**Phase 6 — Analytics agent.** `txtsql` absorbed: four layers plus the tenant layer, multilingual
retrieval, repair loop, analytics views, `/analyse` page. *Done when:* the extended adversarial
corpus passes, tenant-crossing attempts are refused at the layer that claims them, and the
multilingual suite has a real measured number.

**Phase 7 — SaaS.** Plans, quotas, metering, onboarding, admin console, compliance
documentation. No payment processing.

**Phase 8 — Production.** Docker Compose *actually built and started* (`txtsql`'s is documented
as never having been run — do not inherit that), migrations, backups, health checks, structured
logging, deployment runbook, and one verified live Copernicus round-trip.

---

## 12. The demonstration that must work

A single connected scenario, because disconnected features do not sell a decision platform.

*Souss Primeurs, Agadir.* An agronomist opens the dashboard. Three parcelles need irrigation
today; one, P03, needs notably more. She opens it, sees the recommendation in m³ with duration
and cost, and unfolds « Pourquoi cette décision ? » — ET0 from Penman-Monteith with today's real
weather, Kc for the current stage (estimated from the planting date, labelled as an estimate),
the water balance, and a moisture reading tagged `MANUAL_ENTRY`. She asks the copilot what
happens at −20 %: the scenario runs in Python, the stress trajectory is charted, and no yield
figure appears because this crop has no documented Ky — stated, not hidden.

The same afternoon, shipment EXP-1842 leaves for Casablanca with 180 t of cherry tomatoes. Rain
is forecast on the A7 mountain section. The engine computes that the truck will be inside the
disruption window, generates twelve alternatives, discards four as infeasible with reasons, and
recommends **departing ten hours earlier** — 30 risk points lower, no extra cost, deadline met
with margin. The obvious coastal alternative ranks worse: 90 km/h gusts. The map highlights the
recommendation, dims the rest, outlines the risk zone. She accepts.

At the end of the month the director opens *Analyse* and types
« Combien d'eau avons-nous économisé par ferme ce mois-ci, et combien de recommandations ont été
acceptées ? » The query is validated, executed read-only inside her tenant, and explained from
the returned rows only.

---

## 13. Out of scope — do not build

Payment processing and invoicing, livestock management, ERP/TMS/WMS integration, turn-by-turn
navigation, price forecasting,
carbon accounting, a mobile app, a trained neural nowcast, automatic valve control, multi-country
deployment. The architecture must not preclude them. The MVP must not contain them.

Also: do not build an impressive thing where a useful one exists. When the choice is between
technically impressive and useful to the business user, choose the second. Between complex AI
and an understandable reliable decision, choose the second. Between more features and a
stronger end-to-end workflow, choose the second.

---

## 14. Documentation

French, honest, in `docs/`: `architecture.md`, `setup.md`, `api.md`, `mcp.md`, `ml.md`,
`risk-engine.md`, `irrigation.md`, `alternatives.md`, `analytics-sql.md`, `security.md`,
`conformite.md`, `deployment.md`, `methodologie-recherche.md`, `calibration-et-fiabilite.md`.

**One consolidated honesty ledger** at `docs/ce-qui-nest-pas-mesure.md`, and a section in the
README. All three repos have one; the merged product keeps exactly one, and it starts with the
known gaps: no calibration campaign has run on real Moroccan weather archive; no field feedback
has been collected in production; disruption probabilities are rule-derived comparative
indicators, not calibrated probabilities; agronomic thresholds are documented starting values
awaiting validation by Moroccan agronomists; the Copernicus and OSRM network round-trips have
never been executed; the multilingual eval suite has never been run against a live model; the
Docker stack has never been started.

**Never claim a feature is operational if it is not.** The interface must carry the same
qualification the documentation does — `atlasagri` already flags unvalidated thresholds on every
affected evaluation, and that pattern extends to everything above.

---

## 15. Skills available to you

Seven skills accompany this prompt. They are not optional reading — each encodes a rule set
whose violation is invisible in review. Consult the relevant one **before** writing code, not
after.

| Skill | Consult before |
|---|---|
| `agri-water-science` | any ET0, ETc, Kc, water-balance, dose, stress, yield or soil-hydraulics calculation, its tests, or its seed data |
| `provenance-discipline` | adding any column, schema field, API field or displayed value |
| `tool-registry` | adding or changing any agent or MCP tool, or the agent loop |
| `sql-safety-stack` | anything on the analytics path, or any feature that builds SQL from input |
| `agent-boundary` | any system prompt, agent workflow, or unstructured-input ingestion |
| `french-agri-ui` | any user-facing string, error code, or component that shows a value |
| `evidence-discipline` | any README, capability table, metric, limitation, or evaluation case |

If you find a rule in a skill that the code contradicts, resolve it explicitly in Phase 0 and
update the skill — a stale skill is worse than none.

---

## 15 bis. Working agreement

- Do not ask me to confirm small engineering decisions. Decide, document, move.
- One checkpoint: the Phase 0 plan. After that, report at phase boundaries.
- Prefer deleting duplicated code over abstracting it prematurely.
- If a merge reveals that one repo solved something better than my instructions here, say so
  and do it the better way.
- Commit messages in English, explaining the reason. Documentation in French.
- Run `ruff`, `mypy --strict` and the full test suite before declaring a phase done.
- When you cannot verify something (a network call, a credential, a container build), say
  explicitly that it is unverified. Do not describe untested instructions as tested.

---

## 16. Research foundation, and what it adds

Four papers sit behind this product. Two are already reflected in the existing code; two are
new to this merge. In all four cases the rule is the same: **reproduce the method, derive the
values locally, never import a foreign number as local truth.** A guard test exists for the
first paper; write one for each of the others.

### 16.1 Silva-Sosa — climate-risk nowcasting for supply chains (UNICIENCIA, Colombia)

Already the methodological basis of the risk layer. What is taken: the three-stage translation
(nowcast → agricultural impact → supply-chain signal), the 6/12/24/48 h horizons, lead time as
a first-class output, and above all **threshold derivation by local quantiles (33rd/67th
percentiles)** — a reproducible method rather than a set of values.

What is refused, and must stay refused: the Colombian numeric thresholds, the LSTM-by-default
choice, and validation on synthetic data. The paper is candid about its own limits — synthetic
calibration, an F1 for extreme events of 0.51–0.66, a precipitation–rice correlation of 0.09,
and no stakeholder validation at all. That candour is the reason to trust the method and not
the numbers.

### 16.2 Ahmadi et al. — agentic data collection and prediction (McGill)

Already the basis of the field-feedback loop. Now also the basis of the agent design rules in
§4.2 and the `agent-boundary` skill: typed artifacts between stages rather than free-form
model-to-model text, immutable raw records with corrections stored as separate operations,
deterministic parsing first with model-assisted coding only on failure and only above a
confidence floor, and evaluation partitioned by entity rather than by record.

Its measured prompting results are worth following because they were measured: expert framing
beat role-play; adding real behavioural history moved accuracy far more than model size did;
few-shot gains saturated around ten examples; personas helped only when history was missing;
and the largest model was not the best. Spend context on a parcelle's irrigation history or a
route's disruption record before spending it on more elaborate instructions.

### 16.3 Yu, Tilse, Filippi & Bishop — SoilWaterNow / SWEB (University of Sydney) — NEW

**This is the most valuable addition of the four, because it attacks the irrigation engine's
two binding limitations directly:** that one manual reading at one depth stands for a whole
root zone, and that a single point stands for a whole parcelle.

SWEB couples a satellite surface-energy balance to a soil water balance to produce daily
actual ET and root-zone soil moisture at 30 m resolution, from thermal imagery, gridded
weather and a soil map — no probe required, within-field variability preserved. Soil hydraulic
properties come from pedotransfer functions driven by sand and clay fractions, so it runs
anywhere a digital soil map exists. Three parameters are fitted by differential evolution
against surface soil moisture, and the fit is refused below a minimum observation count.

The full method, the Moroccan data substitutions (ERA5-Land or the existing Open-Meteo archive
for climate, ISRIC SoilGrids for texture, Landsat 8/9 Collection 2 for thermal, SMAP for
calibration), and one genuine ambiguity in the published water-balance equation that must be
resolved rather than transcribed, are in the `agri-water-science` skill under
`references/remote-sensing-soil-water.md`. **Read it before implementing anything here.**

Scope for this project: build it **after the merge is stable**, behind the existing
`SoilMoistureProvider` interface, as one more implementation that touches no engine code —
which is the whole point of that abstraction. Do not start before the Copernicus adapter has
fetched one real scene. Rank its output below a manual reading in the quality score, tag it
`DERIVED`/`SATELLITE`, and put "no Moroccan validation" in the honesty ledger. The published
RMSE of 0.05–0.11 m³/m³ is Australian and is not this product's accuracy in the Souss.

The paper also points at **French & Schultz water-limited potential yield**, which is
better-founded here than it looks: it was built for Mediterranean-type cereal systems, which
is exactly Morocco's. Mid-season plant-available water feeds a yield estimate that feeds a
nitrogen decision. Treat its slope and intercept as configured, versioned, source-carrying
parameters marked « à calibrer sur données locales » — the same treatment as every other
agronomic threshold.

---

## 17. Engineering judgment — you are expected to disagree

This document is a specification written before you had read the code. You have read it; I
have not, recently. Where those two facts conflict, yours wins.

**You are explicitly authorised, and expected, to propose something better.** For any
significant proposal, state: what you propose, why, what it replaces, what it costs, and
whether it belongs in this scope or a later one. Then, unless it changes the doctrine in §1 or
the phase boundaries in §11, **just do it and document the decision** — do not wait for me.

Push back in particular when you find:

- a conflict resolution in §4 that the actual code makes wrong or unnecessary;
- a capability one repo already solved better than the winner I named;
- unnecessary complexity, including complexity I introduced here;
- a missing component the three systems all assumed someone else handled;
- a technology choice that has aged badly, or one where a simpler option would do;
- a data-quality risk, an external-API limitation, or a security hole;
- a UX problem — especially one where the interface is technically correct and practically
  unusable by a farmer or a supply-chain manager;
- a commercial weakness: something that would stop a Moroccan agro-industrial buyer from
  paying for this.

Two things are not up for revision, because they are what makes the product credible rather
than impressive: **the model never computes**, and **nothing is claimed that is not backed by
something a reviewer can run**. Everything else is open.

Record every significant decision — including the ones where you overrode this document — in
`docs/decisions/`, one short file each: context, options, choice, consequence. The difference
between what was specified and what was built is where the interesting engineering is, and
throwing it away is the one mistake that cannot be recovered later.

---

Begin with Phase 0.
