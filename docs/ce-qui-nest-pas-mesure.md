# Ce qui n'est pas mesuré

Registre unique des affirmations que ce dépôt **ne démontre pas**. Distinct des limites de
conception : il s'agit ici de lacunes de preuve. Mettre à jour dans le même commit que la
lacune créée ou comblée.

Règle : **un chiffre absent est absent, pas estimé.** Aucun espace réservé, aucun « environ »,
aucune valeur reprise d'une exécution voisine.

| # | Affirmation non démontrée | Ce qui la comblerait | État |
|---|---|---|---|
| 1 | Aucune campagne de calibration n'a tourné sur l'archive météo marocaine réelle | `python -m app.cli calibrer --region SOUSS_MASSA --annees 10` exécuté et versionné | ouvert |
| 2 | Aucun retour terrain collecté en production ; la boucle de correction n'a rien corrigé | première saison de retours, partition d'évaluation réservée | ouvert |
| 3 | Les probabilités de perturbation ne sont pas calibrées sur un historique d'incidents marocains | score de Brier sur échantillon suffisant | ouvert |
| 4 | Les seuils agronomiques sont des valeurs de départ, non validées par des agronomes marocains | revue par un agronome, `is_measured=true` par profil | ouvert — le mécanisme de surcharge existe depuis la phase 2 (une organisation peut poser sa propre ligne mesurée) ; aucune surcharge réelle n'a été saisie |
| 5 | L'aller-retour réseau Copernicus (OAuth + réponse statistique) n'a jamais été exécuté | des identifiants CDSE, un réseau qui joigne `identity.dataspace.copernicus.eu`, puis une acquisition réelle sur une parcelle de démonstration | ouvert — **et deux fois bloqué** : la phase 8 a tenté les deux hôtes CDSE, refusés par la politique réseau de cet environnement (403 sur le CONNECT), et aucun identifiant n'existe ici. Aucun adaptateur Copernicus n'a été écrit : l'humidité racinaire satellitaire n'est pas portée (lignes 9 et 10), donc ce serait un branchement sans consommateur |
| 6 | L'aller-retour réseau OSRM n'a jamais été exécuté | un itinéraire réel contre une instance OSRM | ouvert |
| 7 | La suite d'évaluation multilingue n'a jamais tourné contre un modèle réel | une clé, puis `python -m app.analytics.evals.run` étendu à la génération, résultat publié | ouvert — la suite existe (12 questions, français et arabe) et **le corpus** est mesuré : 11/11 requêtes de référence valides contre le catalogue réel. Ce chiffre mesure le corpus, pas le modèle, et le harnais l'imprime sur la ligne suivante |
| 8 | La pile Docker n'a jamais été construite ni démarrée | `docker compose up --build` réussi et documenté | **comblée en phase 8** — construite et démarrée sur un volume vide jusqu'à une recommandation d'irrigation servie par l'API du conteneur. Quatre défauts que le fichier jamais exécuté conservait ont été trouvés en le lançant : un point d'entrée ASGI inexistant, un `CREATE EXTENSION` en échec qui avortait la migration entière, des rôles sans mot de passe incapables d'ouvrir une session par TCP, et une organisation de démonstration sans personne dedans |
| 9 | L'humidité racinaire dérivée du satellite (SWEB) n'a aucune validation marocaine | comparaison contre sondes ou mesures gravimétriques locales | non commencé |
| 10 | Les paramètres French & Schultz sont australiens | calibration sur données d'essai locales | non commencé |
| 11 | L'enveloppe de contenu non fiable n'est appliquée à aucun résultat d'outil : `wrap_untrusted` est du code mort dans `atlasagri` et aucun test d'injection n'existe côté plateforme métier | câblage de l'enveloppe + une suite adverse par outil | **comblée en phase 4** — l'enveloppe est appliquée par le **registre**, donc à tout résultat d'outil, agent et MCP compris ; une balise de sortie glissée dans une valeur est neutralisée et le test le vérifie sur le chemin réel |
| 12 | Aucun rendu React n'est testé contre l'injection de contenu venu de la base | Vitest, un test par composant affichant une valeur issue d'une ligne | **comblée en phase 5** — `react/no-danger` en erreur, un balayage de l'arbre source qui échoue sur `dangerouslySetInnerHTML`, `innerHTML`, `insertAdjacentHTML` et `document.write`, et des tests de rendu utilisant un nom de produit contenant `<img src=x onerror=…>`. Ces tests sont **écrits**, pas repris : ceux de `text_to_sql` portaient sur une page vanilla que le portage supprime (§7.9 du plan) |
| 13 | Les seuils affichés dans l'interface ne sont pas encore reliés au référentiel FAO : les moteurs arrivent en phase 3 | phase 3 livrée, seuils lus depuis `crops` / `soil_profiles` | **comblée en phase 5** — Kc, `p`, enracinement et propriétés hydriques sont lus depuis le référentiel, portés par la réponse HTTP et affichés dans « Pourquoi cette décision ? » avec le numéro d'équation FAO de chaque étape |
| 16 | Les valeurs FAO chargées sont des références **pour conditions standard**, pas des mesures marocaines : Kc, `p`, capacité au champ et efficience d'application n'ont été confrontés à aucune parcelle du Souss ni du Gharb | campagne de mesures locale, lignes `is_measured=true` par profil de sol et par culture | ouvert |
| 17 | L'ajustement climatique du Kc (FAO-56 éq. 62, pour RHmin et vent hors conditions sub-humides) n'est pas appliqué : les conditions de l'intérieur marocain le violent régulièrement, dans le sens d'une **sous-estimation** de l'ETc | hauteur de culture par stade citée depuis la FAO-56 table 12, puis implémentation de l'éq. 62 | ouvert — la limite est désormais **déclarée avec le chiffre** (`CropWaterRequirement.caveats_fr`) et nomme le sens du biais ; elle n'est pas corrigée |
| 18 | La dose est calculée sur la surface entière de la parcelle : sous goutte-à-goutte ou micro-aspersion, seule une fraction est humectée, donc le besoin est **surestimé** | passage au Kc dual (Kcb + Ke) avec un terme de fraction humectée — modification du moteur, pas d'un paramètre | non commencé — la colonne `wets_whole_surface` du référentiel existe pour cela |
| 19 | Aucun scénario d'irrigation n'a été confronté à un résultat de terrain : le simulateur projette un bilan hydrique, il ne prédit pas un rendement | campagne de suivi sur une saison, comparaison dose recommandée / dose appliquée / rendement | ouvert — **la moitié « dose recommandée » existe désormais** : les recommandations sont archivées avec leur décision figée et le verdict humain. Reste à collecter la dose appliquée et le rendement |
| 20 | La boucle d'agent n'a **jamais** été exécutée contre une clé d'API Anthropic réelle : aucun appel n'a quitté cet environnement, et rien ne démontre que le modèle choisit `calculate_irrigation_requirement` plutôt que d'estimer une dose lui-même | une clé, un jeu de questions, une exécution publiée — puis des échanges enregistrés et rejoués (décision 0010) | ouvert — la **mécanique** de la boucle est vérifiée hors ligne contre un fournisseur scripté ; le **jugement** du modèle ne l'est pas |
| 21 | L'aller-retour réseau Open-Meteo n'a jamais été exécuté : `OpenMeteoProvider` est écrit, son analyse de charge utile n'a été confrontée à aucune réponse réelle | une acquisition réelle sur une parcelle de démonstration | ouvert — toutes les recommandations produites ici viennent du fournisseur hors ligne, entièrement `(SIMULATED, SEED_DEMO)` |
| 22 | La table de tarifs `MODEL_PRICING` est recopiée d'un tarif publié ; aucun total n'a été rapproché d'une facture | une facture réelle comparée à la somme des estimations d'une session | ouvert — le coût vaut `null` dès qu'un appel n'est pas tarifé, jamais un total partiel présenté comme complet |
| 23 | Aucun transport MCP n'existe : `McpBridge` traduit le registre et est testé, mais aucun processus stdio n'a été lancé ni connecté à un client MCP réel | un serveur stdio, démarré et attaché à un client, avec un appel d'outil aboutissant | ouvert |
| 24 | Le prompt système n'a été soumis à aucune suite adverse : rien ne démontre qu'un contenu injecté dans un nom de site ou une note de terrain ne détourne pas le modèle | corpus adverse par outil, exécuté contre une clé réelle | ouvert — l'encapsulation est structurelle (ligne 11) ; son **efficacité sur un modèle** n'est pas mesurée |
| 25 | **La moitié logistique de la démonstration de la §12 n'existe pas** : aucun moteur de risque routier, d'exposition d'itinéraire ni de génération d'alternatives n'a été porté depuis `atlasagri`. Dette de la **phase 3**, découverte en phase 5 | portage de ces moteurs, purs et testés, puis un outil `analyze_shipment_risk` et le panneau de recommandation correspondant | **comblée** — moteurs portés et rendus purs (décision 0014), 23 tests sur ce que la source ne testait pas, outil d'agent, route HTTP et panneau de recommandation livrés |
| 26 | Les tuiles vectorielles PMTiles prévues par la §11 ne sont pas hébergées : la carte utilise un fond raster public | une archive PMTiles servie par l'installation, et un style vectoriel | ouvert — dans cet environnement le fond **ne se charge pas du tout** (le réseau ne joint pas le serveur de tuiles) ; la carte le dit à l'écran et affiche les parcelles à leur position réelle sans fond |
| 27 | L'interface n'a jamais été ouverte par un utilisateur autre que l'auteur, ni sur un écran de terrain, ni sur une tablette | une session d'observation avec un agronome, sur son matériel | ouvert |
| 28 | La localisation `ar-MA` est une coquille : le mécanisme de bascule, la direction du texte et le repli existent et sont testés ; **le catalogue arabe est vide et le reste délibérément** | traduction par un relecteur natif, puis vérification RTL de la carte, des graphiques et des deux panneaux | ouvert — une traduction automatique d'une interface agronomique est refusée : un niveau de stress ou une unité de dose mal traduits sont un problème de sûreté, pas de finition |
| 29 | Les chaînes de l'interface hors couche partagée ne sont pas extraites : les pages portent encore leurs littéraux français | extraction dans le catalogue, page par page | ouvert — le vocabulaire agronomique et logistique, lui, n'est **pas** concerné : il arrive du serveur en `*_label_fr`, ce qui garantit que l'écran, le copilote et le journal d'audit emploient le même mot |
| 30 | Le jeu de démonstration fige ses dates au moment du semis : quelques semaines plus tard, l'expédition affiche « échéance dépassée » et aucune expédition n'est en transit | re-exécuter `seed-demo`, ou dater le jeu relativement à l'exécution | ouvert — sans conséquence sur l'irrigation, qui recalcule à la météo du jour |
| 31 | Les seuils de vigilance routière — intensité de pluie, cumul antérieur, rafales, gel — sont des **valeurs de départ** tirées des pratiques usuelles de gestion de trafic. Aucune n'a été confrontée à un incident routier marocain | relevé d'incidents (coupures, retards) sur les axes concernés, puis calibration par quantiles locaux | ouvert — les seuils sont affichés avec leur `rationale_fr` à côté du chiffre, et l'avertissement accompagne chaque analyse |
| 32 | `avg_speed_kmh` et `reliability` du graphe routier ne sont pas mesurés : ce sont des ordres de grandeur par classe d'axe. Les **coordonnées** et les **distances routières**, elles, sont des faits | relevés de temps de parcours réels, par axe et par saison | ouvert — l'usage légitime est de comparer des corridors entre eux, jamais d'annoncer une heure d'arrivée |
| 33 | Les paramètres de coût du fret (0,40 MAD/t·km frigorifique, 2 500 MAD de frais fixes par camion, 55 MAD/h de groupe froid) n'ont été confrontés à aucune facture de transporteur | comparaison avec des factures réelles, puis passage en donnée d'organisation | ouvert — un défaut a déjà été corrigé au portage : les frais fixes ne s'appliquaient qu'une fois quel que soit le nombre de camions (décision 0014) |
| 34 | La pondération des profils d'arbitrage — 40 % risque pour une denrée périssable, 45 % coût pour un produit stockable — n'a été validée par aucun exploitant | entretiens avec des responsables logistique, puis surcharge par organisation | ouvert — le profil et sa justification sont affichés avec la recommandation, donc contestables |
| 35 | Le rendu cartographique n'est vérifié par aucun test automatique : jsdom n'a pas de WebGL, et les stubs de la suite permettent seulement d'importer les pages qui contiennent une carte | tests de bout en bout dans un vrai navigateur, sur les tracés et la légende | ouvert — la carte a été ouverte manuellement dans Chromium, et l'échec de rendu est désormais **contenu** : une carte qui ne s'affiche pas ne fait plus tomber la décision |
| 36 | La **génération** de SQL n'a jamais été exécutée contre un modèle réel : rien ne démontre qu'une question française produit une requête correcte, ni qu'un modèle ne trouve pas de formulation que le validateur laisse passer et qui répond à autre chose | une clé, le corpus multilingue exécuté de bout en bout, résultat publié | ouvert — la **mécanique** du pipeline est vérifiée hors ligne contre un fournisseur scripté ; le **jugement** ne l'est pas |
| 37 | Le seuil de coût de plan (`explain_cost_threshold`) n'est calibré sur aucune charge réelle : le coût de plan s'exprime dans des unités qui dépendent de l'installation | mesure des coûts de plan des requêtes légitimes sur une base peuplée, puis calage par quantiles | ouvert — c'est un contrôle de **ressource**, jamais une frontière de sécurité, et la documentation le dit à l'endroit où il est configuré |
| 38 | Aucun test ne vérifie que le résumeur **résiste** réellement à une consigne injectée dans une ligne : ce qui est vérifié est la **structure de l'invite** — consigne de langue hors des délimiteurs, régions encadrées, instruction système explicite | corpus adverse de résumé exécuté contre une clé réelle | ouvert — la structure est ce que nous contrôlons ; l'obéissance du modèle ne l'est pas |
| 39 | Rien ne garantit qu'un verdict enregistré corresponde à ce qui a été fait au champ : un exploitant qui clique « Accepter » sans lancer le tour d'eau produit une trace fausse | rapprochement avec une mesure d'application — compteur, relevé de vanne, ou seconde mesure d'humidité après l'apport | ouvert — l'écran n'a encore été utilisé par personne |
| 14 | Les valeurs d'exemple de l'index de schéma proviendraient de `pg_stats` sur des tables multi-tenants | registre d'énumérations déclaré en Python ; interdiction d'échantillonner une table portant `tenant_id` | **comblée en phase 6** — `app/analytics/enums.py` ne contient que des membres d'énumération, dérivés des énumérations du domaine ; aucune lecture de `pg_stats`, donc rien à filtrer et rien à tenir |
| 15 | Le corpus adverse de 36 cas ne contient aucun cas de franchissement d'organisation | suite d'intégration distincte où l'exécution *est* l'assertion, avec une couche RLS dans le classement | **comblée en phase 6** — corpus porté à 47 cas dont **9 de franchissement**, et le harnais vérifie la **couche** qui attrape, pas seulement qu'un cas est attrapé. Une suite séparée envoie à la base du SQL qu'aucun chemin applicatif ne laisserait passer, pour montrer que la couche 2 tient sans la couche 1 |
| 40 | La limitation de débit vit **dans la mémoire du processus** : avec plusieurs travailleurs, le seuil effectif est multiplié par leur nombre, et un redémarrage remet les compteurs à zéro | un magasin partagé (Redis ou équivalent), puis une mesure du seuil effectif sous charge répartie | ouvert — c'est une protection contre une boucle client partie en vrille, **pas** contre un attaquant déterminé, et le module le dit à l'endroit où il est écrit |
| 41 | La promesse de la §6 — première valeur en **moins de dix minutes** — n'a été chronométrée par personne : le parcours existe et il est testé de bout en bout, mais aucun exploitant réel ne l'a suivi | une session d'observation chronométrée, avec quelqu'un qui découvre le produit | ouvert — ce que la suite démontre est que le parcours **aboutit**, pas qu'il est rapide |
| 42 | La durée de conservation du journal d'audit est **annoncée et non appliquée** : aucune tâche ne purge les lignes échues | une tâche planifiée, et un test qui vérifie qu'une ligne échue disparaît | ouvert — la page de conformité rend `retention_enforced: false` et l'écrit en toutes lettres plutôt que d'afficher une durée qui n'engage rien |
| 43 | La résidence des données et le numéro de déclaration CNDP sont **déclarés par l'exploitant** dans la configuration : le code ne peut pas vérifier où tourne sa base, ni qu'une déclaration a été déposée | attestation de l'hébergeur et récépissé CNDP, rattachés au déploiement | ouvert — non renseignés, ils rendent `null` et l'écran affiche « non déclarée » ; le démarrage en production est **refusé** tant que le pays n'est pas déclaré. Le chiffrement au repos relève du même registre : il n'est ni assuré ni vérifié par l'application |
| 44 | La plateforme ne peut rien garantir des **sauvegardes** de l'hébergeur : ce que le reçu de suppression compte est le nombre de lignes effacées **en base**, et rien d'autre | une procédure d'effacement couvrant les sauvegardes, écrite avec l'hébergeur et exercée | ouvert — la suppression elle-même a été exercée sur une organisation réelle en développement (2 comptes, 10 lignes de journal, zéro ligne de référentiel touchée) |
| 45 | Les quotas sont vérifiés avant l'acte, dans la transaction de la requête : **deux requêtes simultanées peuvent passer la même vérification** et dépasser le plafond du nombre d'appels concurrents | une réservation atomique (verrou consultatif ou contrainte) mesurée sous concurrence | ouvert — le dépassement possible est borné par le nombre de requêtes en vol, et l'usage réel reste comptabilisé exactement puisqu'il est écrit en événements |
| 46 | L'inscription est ouverte et **aucune mention d'information n'est présentée** : la personne saisit nom et courriel sans qu'un texte lui dise qui traite ces données, pour quelle finalité, ni comment exercer ses droits | un texte rédigé par le responsable de traitement, affiché au formulaire, plus un registre des consentements | ouvert — c'est le manquement le plus visible de `docs/conformite.md`, et il se comble par un texte, pas par du code |
| 47 | La plateforme n'envoie **aucun courriel** : pas de vérification d'adresse, pas de réinitialisation de mot de passe en autonomie, et un membre ajouté reçoit son mot de passe provisoire par un canal que la plateforme ne connaît pas | un acheminement de courrier, puis une adresse vérifiée avant la première connexion | ouvert — l'interface le **dit** au lieu d'annoncer « invitation envoyée », ce qui serait la pire des deux options |
| 48 | La pile Docker a été démarrée sur **un seul hôte**, avec un seul travailleur, et jamais derrière un terminaison TLS ni un reverse proxy. Aucune configuration systemd, aucun certificat, aucun déploiement sur un vrai serveur | un déploiement réel, puis le guide relu à partir de ce qui s'est passé | ouvert — `docs/deployment.md` distingue explicitement ce qui a tourné de ce qui est écrit |
| 49 | La sauvegarde a été exercée sur une base de **démonstration** de quelques centaines de lignes : rien ne dit ce que durent `pg_dump` et `pg_restore` sur une installation réelle, ni si la fenêtre de restauration tient un engagement | un exercice de restauration chronométré sur un volume représentatif | ouvert — ce qui **est** vérifié est que la restauration conserve la propriété des tables, `FORCE`, les politiques et l'isolation : connecté comme `atlas_app`, une organisation étrangère voit 0 parcelle là où celle de démonstration en voit 7 |
| 50 | Aucune sauvegarde n'est **planifiée** : `scripts/backup.sh` est écrit et exercé, rien ne l'appelle | une tâche planifiée, une rétention, et une alerte quand une sauvegarde manque | ouvert — une sauvegarde qui dépend de quelqu'un qui y pense n'est pas une sauvegarde |

## Valeurs importées — interdiction de reprise

Les seuils, coefficients et chiffres de validation publiés appartiennent au lieu où ils ont été
mesurés. Les quantiles de risque colombiens, la RMSE sol-eau australienne et la pente
d'efficience d'utilisation de l'eau australienne prouvent qu'une **méthode** fonctionne — jamais
une performance au Maroc.

Un test de garde par méthode importée échoue si une constante étrangère apparaît comme seuil
local configuré.


## Ce que la phase 5 a réellement vérifié

**Exécuté :** 266 tests Python sur un PostgreSQL 16.13 réel et 37 tests Vitest ;
`ruff`, `mypy --strict`, `tsc --noEmit`, `eslint --max-warnings 0` et la
construction de production. Et — c'est le point de la phase — **l'interface a été
réellement ouverte** : un navigateur Chromium a parcouru connexion → vue générale
→ parcelles → P03 → carte → expéditions → copilote contre l'API et la base de
cette machine, page par page, avec une capture par étape. La chaîne complète
apparaît à l'écran : les quatorze étapes numérotées de la recommandation P03,
chacune avec son numéro d'équation FAO, les puces de provenance sur chaque
entrée, la liste de qualité des données nommant l'entrée qui a fait baisser la
fiabilité, et le bandeau de démonstration non masquable.

**Trois défauts trouvés en regardant l'écran, que la suite de tests ne montrait
pas :**

1. La trace passait de « dose nette 127,5 mm » à « dose brute = 48,0 mm » sans
   étape intermédiaire — le plafond d'infiltration n'était qu'un avertissement.
   Un nombre apparaissait au milieu du raisonnement. Le plafond est désormais une
   étape (décision : un plafond fait partie du calcul, pas de la marge).
2. Les phrases des moteurs écrivaient « 195.0 mm » sous un titre « 4,78 mm/j », et
   `fr-MA` rendait un coût de 3 936 MAD en « 3.936 MAD ». Corrigé à la source
   (décision 0013).
3. L'ET0 émettait « Rayonnement solaire : mesurée/API. » comme *avertissement* à
   chaque calcul, en doublon de l'avertissement détaillé lorsqu'il était estimé.
   Une entrée mesurée n'est pas une dégradation, et une section d'avertissements
   toujours pleine cesse d'être lue.

**Ce que cela ne prouve pas :** que la démonstration de la §12 fonctionne. Elle en
demande la moitié logistique — fenêtre d'exposition, douze alternatives, quatre
écartées avec motif, départ avancé de dix heures — et **ces moteurs n'ont jamais
été portés** (ligne 25). Ce qui tourne de bout en bout est le parcours
d'irrigation : ouvrir le tableau de bord, voir les parcelles à irriguer, ouvrir
P03, lire la recommandation en m³ avec durée et coût, déplier « Pourquoi cette
décision ? » et y trouver l'ET0 Penman-Monteith, le Kc du stade estimé et
étiqueté comme estimé, le bilan hydrique et une humidité étiquetée. Le reste de
la §12 est annoncé absent, à l'écran, par le serveur.

**Ce qui n'a pas pu être vérifié ici :** le copilote reste sans clé d'API, donc la
page affiche son refus français plutôt qu'une réponse (lignes 20 et 24) ; le fond
de carte ne se charge pas dans cet environnement (ligne 26) ; aucun utilisateur
autre que l'auteur n'a ouvert l'interface (ligne 27).

## Ce que la fermeture de la dette « recommandations » a vérifié

**Le défaut :** `create_recommendation`, le seul outil d'écriture du registre,
rendait `recorded=true` **sans rien écrire**. Le modèle annonçait à l'utilisateur
que sa décision était consignée, l'utilisateur le croyait, et rien n'existait. Un
outil qui se trompe sur son propre effet est pire qu'un outil absent.

**Exécuté :** 339 tests Python et 66 tests Vitest ; migration `0003` appliquée
sur la base réelle ; la boucle complète ouverte dans un navigateur — enregistrer
une proposition depuis une parcelle, la retrouver en attente, rendre un verdict,
voir le décideur nommé et le taux gagner son dénominateur.

**Ce que la table impose plutôt que ce que le service promet :** une contrainte
refuse un verdict non attribué. « Acceptée » sans décideur ni horodatage est
exactement la ligne qu'un audit cherche — celle où personne n'a approuvé mais où
l'action a eu lieu — et un second chemin d'écriture arrivera un jour.

**La décision est figée, pas référencée.** Le `payload` porte le
`Decision.to_dict()` complet. Recalculer six mois plus tard donnerait un autre
chiffre, sur d'autres données, et l'audit porterait sur une décision qui n'a
jamais été prise. C'est aussi la seule base possible d'une quantification
ultérieure des pertes évitées (ligne 19), et elle ne se reconstitue pas après
coup.

**Ce que cela ne prouve pas :** que les verdicts enregistrés reflètent ce qui a
réellement été fait au champ. Personne n'a encore utilisé cet écran, et un
exploitant qui clique « Accepter » sans lancer le tour d'eau produirait une
trace fausse que rien ne rattrape (ligne 39).

## Ce que la phase 8 a réellement vérifié

**Exécuté, dans Docker, sur un volume vide :** `docker compose up -d --build`, les quatre
migrations, le référentiel, l'organisation de démonstration, le compte pour s'y connecter, puis
— par l'API du conteneur — une connexion, la liste des sept parcelles et la recommandation
d'irrigation de P03 (« Irrigation recommandée : 2 186,7 m³ sur 15h05 »). Les neuf sondes passent
dans le conteneur, dont `cross_tenant`.

**Quatre défauts trouvés en le lançant**, qu'un fichier jamais exécuté conservait :

1. `uvicorn app.main:app` — il n'existe aucun objet `app` importable, l'application est
   construite par une fabrique. Le conteneur échouait à l'import ;
2. `CREATE EXTENSION vector` dans un `contextlib.suppress` : l'exception était attrapée, mais la
   transaction restait avortée et **toute la migration** échouait ensuite ;
3. les rôles créés sans mot de passe n'ouvrent aucune session par TCP — ce qui ne se voit pas
   sur une base locale en authentification `trust` ;
4. `seed-demo` peuplait une organisation **sans personne dedans** : la pile offrait un produit
   complet que personne ne pouvait ouvrir. Et rejouée, elle échouait sur une contrainte
   d'unicité.

**Sauvegarde et restauration exercées** sur cette base : dump, restauration dans une base
neuve, puis vérification que les garanties survivent — 19 tables détenues par `atlas_owner`,
16 tables en `FORCE ROW LEVEL SECURITY`, 20 politiques, 8 vues analytiques, `atlas_app` toujours
`NOBYPASSRLS`. Connecté comme `atlas_app`, une organisation étrangère voit **0** parcelle là où
celle de démonstration en voit 7.

**Journalisation vérifiée dans le conteneur** : hors `local`, chaque ligne est un objet JSON,
y compris celles d'`uvicorn`, et chaque requête écrit `http_request` avec son `run_id`.

**Ce qui n'a pas pu tourner :** l'aller-retour Copernicus (ligne 5), refusé deux fois — hôtes
CDSE bloqués par la politique réseau, et aucun identifiant présent. Un serveur réel, un
certificat, un reverse proxy : lignes 48 à 50.

## Ce que la phase 7 a réellement vérifié

**Exécuté :** 412 tests Python sur un PostgreSQL 16.13 réel et 91 tests Vitest ;
`ruff`, `mypy --strict`, `tsc`, `eslint` et la construction de production. La
migration `0004` appliquée sur la base de développement.

**Exécuté dans un vrai navigateur** (Chromium, session complète) : le parcours de
démarrage de bout en bout — création d'une parcelle par le formulaire, saisie
d'un relevé d'humidité, puis avis d'irrigation FAO-56 sur cette parcelle neuve.
La parcelle créée affiche « Observé · Saisie manuelle » là où les parcelles du
jeu de démonstration affichent « Simulé · Jeu de démonstration », et sa fiche
annonce « Durée non calculable : le débit du système n'est pas renseigné » plutôt
qu'une durée inventée. La page d'administration, ouverte par un compte
**agronome**, affiche le refus du serveur sur le journal d'audit et continue de
rendre le reste.

**Exécuté sur une vraie organisation, hors suite de tests** : l'inscription autonome depuis
le formulaire, l'ajout d'un membre par l'administrateur, puis la **suppression définitive** de
l'organisation par la route d'effacement. Le reçu compte 2 comptes, 2 entrées d'annuaire et 10
lignes de journal supprimés, et **zéro** ligne de référentiel : le catalogue FAO global est
resté intact, vérifié en base après coup. Une confirmation erronée est refusée en nommant
l'identifiant à recopier.

**Ce que cela ne prouve pas :** que le parcours tient en moins de dix minutes
pour quelqu'un qui découvre le produit (ligne 41). Le chronomètre n'a pas tourné,
et l'auteur connaissait déjà chaque champ.

**Non vérifié :** ce que l'hébergeur conserve en sauvegarde après une suppression
(ligne 44), le comportement du limiteur de débit sous plusieurs travailleurs
(ligne 40), et la concurrence sur la vérification de quota (ligne 45).

## Ce que la phase 6 a réellement vérifié

**Exécuté :** 325 tests Python sur un PostgreSQL 16.13 réel et 57 tests Vitest ;
`ruff`, `mypy --strict`, `tsc`, `eslint` et la construction de production. Les
deux corpus tournent hors ligne, sans clé ni réseau :

```
Corpus adverse        47/47   dont security 34/34 (9 franchissements), validation 11/11
Suite multilingue     11/11   requêtes de référence valides — le corpus, pas le modèle
```

Le harnais vérifie la **couche** qui attrape, pas seulement qu'un cas est attrapé.
Un franchissement d'organisation refusé par la résolution de noms plutôt que par
la liste blanche compterait en échec : la garantie tiendrait par accident,
jusqu'au prochain changement de catalogue.

Une suite distincte envoie à la base du SQL qu'aucun chemin applicatif ne
laisserait passer — `SELECT id FROM app.shipments`, `CREATE TEMP TABLE` — pour
montrer que la couche 2 tient **sans** la couche 1. C'est la seule couche qui ne
dépende pas de la correction de ce dépôt.

**Deux défauts trouvés en écrivant les tests :**

1. Un `CROSS JOIN` explicite était tenu pour délibéré. C'est juste quand un humain
   écrit le SQL ; ici l'auteur est un modèle, et « le modèle a écrit CROSS JOIN »
   n'est pas une preuve d'intention. Le produit cartésien de `v_fields` et
   `v_sites` s'exécute, renvoie des lignes, et l'explication décrit un résultat
   qui ne veut rien dire.
2. Une `LIMIT` injectée qui « remplit » ne déclenchait aucune troncature : le
   plafond est poussé dans la requête — ce qui est la bonne façon de faire — donc
   l'exécuteur ne voit jamais son propre plafond. Deux lignes rendues sur sept
   étaient résumées comme s'il n'y en avait que deux. « Exactement N lignes, où N
   est le plafond » est désormais déclaré comme possiblement incomplet.

**Ce que cela ne prouve pas :** que le système répond correctement à une question.
Le fournisseur est scripté ; la mécanique est vérifiée, la génération ne l'est pas
(lignes 7, 36 et 38). Aucun appel n'a quitté cet environnement.

**Deux désaccords avec la spécification, argumentés :** la récupération
vectorielle n'est pas portée — six vues tiennent dans une invite, et la
récupération n'apporterait qu'un mode d'échec silencieux (décision 0016) ; et le
taux d'attrape ne baisse **pas** en ajoutant les cas de franchissement, contrairement
à ce que la compétence prévoyait, parce que la couche qui les attrape a été
ajoutée en même temps qu'eux (décision 0015).

## Ce que la reprise de la dette logistique a réellement vérifié

**Exécuté :** 294 tests Python et 50 tests Vitest ; `ruff`, `mypy --strict`,
`tsc`, `eslint` et la construction de production. Les moteurs portés sont
couverts par 23 tests qui n'existaient pas dans la source, dont ceux qui portent
sur sa propriété centrale : un orage prévu après l'arrivée n'expose rien ; un
tronçon ralenti décale l'heure de passage sur tous les suivants ; un tronçon sans
météo est *non évalué*, pas *sans risque* ; un tronçon critique court n'est pas
dilué par la moyenne pondérée.

L'analyse a ensuite été ouverte dans un navigateur, sur la base de cette machine :
le corridor A7 de montagne ressort exposé sur trois tronçons (Imi n'Tanoute →
Chichaoua → Marrakech → Ben Guerir), et la recommandation est le corridor
littoral par Safi et El Jadida — 669 MAD de plus, 1,5 h de plus, 0,62 point de
risque en moins. Le tableau montre les dix options examinées avec leurs écarts au
plan actuel.

**Un défaut trouvé en portant :** les frais fixes de transport ne s'appliquaient
qu'une seule fois quel que soit le nombre de camions, par une expression
(`fixed_cost_mad * trucks / max(1, trucks)`) qui contredisait son propre
commentaire. Sur 180 t, l'écart est de 17 500 MAD. Corrigé, documenté et pinné.

**Un défaut trouvé en regardant l'écran :** une carte qui échoue à s'initialiser
— poste sans WebGL, pilote ancien — faisait remonter l'exception jusqu'à la
racine React et emportait **toute** la page. L'exploitant perdait la
recommandation en même temps que le fond de carte. L'échec est désormais contenu,
et distingue « la carte ne s'affiche pas » de « le fond manque ».

**Ce que cela ne prouve pas :** que ces recommandations sont bonnes. Les seuils
routiers, les vitesses, les fiabilités d'axe, les coûts et les pondérations
d'arbitrage sont tous des valeurs de départ (lignes 31 à 34). Le moteur classe
correctement selon ces valeurs ; il ne dit pas qu'elles sont justes. La météo de
la démonstration est simulée, donc la fiabilité affichée est « Faible » — ce qui
est le comportement voulu.

**Divergence avec la §12, assumée :** la démonstration écrite attendait « partir
dix heures plus tôt » comme recommandation. Le moteur préfère le réacheminement
littoral, qui évite la zone au lieu de la devancer. Les deux options figurent au
tableau avec leurs écarts ; c'est le calcul qui tranche, pas le scénario.

## Ce que la phase 4 a réellement vérifié

**Exécuté :** 254 tests sur un PostgreSQL 16.13 réel, dont la suite du registre d'outils (une
définition par capacité, aucun paramètre d'organisation sur aucun outil — balayage de tous les
outils enregistrés, donc de ceux qu'on ajoutera —, sortie typée obligatoire, filtrage par rôle
avant déclaration, encapsulation systématique, échec relayé en français sans rien substituer,
portée par organisation) ; la suite de la boucle d'agent contre un fournisseur scripté
(résultats parallèles rendus en **un seul** message utilisateur, réinjection des blocs bruts,
borne d'itérations avec issue visible, refus relayé sans relance, panne de fournisseur traduite
en erreur française, absence de fournisseur assumée plutôt que simulée) ; la suite du pont MCP
(mêmes outils, même filtrage par rôle, même encapsulation, isolation à travers le protocole) ;
et les deux questions de la phase — « Est-ce que je dois irriguer P03 ? » et « Mon transport de
tomates vers Casablanca est-il à risque ? » — de bout en bout à travers le routeur HTTP, jeton
compris, avec la trace d'outils sérialisée telle que l'interface la lira. `ruff` et
`mypy --strict` passent.

**Ce que cela ne prouve pas :** que le copilote fonctionne. Le fournisseur est scripté ; c'est la
plomberie qui est vérifiée, pas le jugement du modèle (lignes 20 et 24, décision 0010). Aucun
appel n'a quitté cet environnement — ni vers Anthropic, ni vers Open-Meteo (ligne 21).

**Corrigé en chemin, et pinné par un test :** la vitesse d'infiltration d'un sol peut être
absente du référentiel ; le plafond anti-ruissellement n'était alors simplement pas appliqué, en
silence, et la dose affichée était indiscernable d'une dose vérifiée. Elle est désormais
annoncée comme non plafonnée. Aucune vitesse par défaut n'a été substituée : elle varie d'un
facteur dix entre un sable et une argile, donc une valeur « raisonnable » y serait une invention.

**Sans objet à ce stade :** l'interface (phase 5), l'agent d'analyse SQL (phase 6) et la couche
SaaS (phase 7). Le copilote répond en JSON ; aucun écran ne le rend.

## Ce que la phase 3 a réellement vérifié

**Exécuté :** 218 tests, dont les sept exemples publiés de la FAO-56 reproduits à l'arrondi près
(exemples 2, 3, 5, 8, 14, 18, 20) ; les propriétés de monotonie agronomique (réduire une dose ne
réduit jamais le stress) ; les propriétés d'absence (pas de débit → pas de durée ; pas de tarif →
pas de coût ; pas de Ky documenté → aucun chiffre de rendement) ; la divulgation du repli
Hargreaves-Samani ; `ruff` et `mypy --strict`.

**Ce que cela ne prouve pas :** que le moteur donne la bonne dose sur une parcelle marocaine.
Les exemples FAO valident l'arithmétique contre la publication ; ils ne valident ni les
paramètres locaux (lignes 4 et 16), ni l'absence de biais systématique (lignes 17 et 18), ni la
conséquence agronomique réelle (ligne 19).

**Sans objet à ce stade :** le moteur n'est branché sur aucun point d'entrée HTTP ni sur aucun
outil d'agent. Il est pur et appelé par les tests seulement ; la phase 4 le relie au registre
d'outils.

## Ce que la phase 2 a réellement vérifié

**Exécuté :** la migration `0002` sur une base vide à la suite de `0001` ; le chargement du
référentiel (10 cultures, 40 stades, 7 sols, 5 systèmes, 9 régions) ; le semis de
l'organisation de démonstration (4 sites, 7 parcelles, 7 mesures, 1 produit, 1 expédition) ;
les neuf sondes sur la base ainsi construite ; 117 tests ; `ruff` et `mypy --strict`.

**Ce que les tests du référentiel ne prouvent pas, et ne peuvent pas prouver :** qu'un Kc est
*juste*. Seule la publication le peut. Ils vérifient que chaque valeur cite sa table, qu'aucune
absence n'est silencieuse, et que les valeurs sont mutuellement cohérentes — ce qui attrape une
erreur de transcription, pas une erreur de lecture de la FAO.

## Ce que la phase 1 a réellement vérifié

Distinguer ce qui a tourné de ce qui est écrit est le seul moyen de garder ce registre utile.

**Exécuté, sur un PostgreSQL 16.13 réel, avec PostGIS 3.4.2 et pgvector 0.6.0 :** la migration
`0001_core` de bout en bout sur une base vide ; les neuf sondes de démarrage ; les cinquante
tests, dont treize d'isolation à cinq couches et six qui cassent délibérément une garantie pour
vérifier que la sonde correspondante sait dire non ; `ruff` et `mypy --strict`.

**Écrit et non vérifié :** le `Dockerfile`, le `docker-compose.yml` et l'image PostgreSQL
dérivée (ligne 8 ci-dessus). Le fichier de workflow d'intégration continue n'a jamais été
exécuté par un ordonnanceur — il est vérifié uniquement en ce que ses commandes sont celles qui
tournent ici.

**Sans objet à ce stade :** tout ce qui relève des moteurs déterministes, de l'agent et de
l'interface. Les phases 3 à 7 n'ont pas commencé ; aucune capacité agronomique ou logistique
n'est présente dans ce dépôt.
