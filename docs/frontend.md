# Interface

React 18, TypeScript strict, Vite, Tailwind, MapLibre. Français intégral.

```bash
cd frontend
npm install
npm run dev      # http://127.0.0.1:5173, l'API est mandatée sous /api
npm test         # Vitest
npm run lint     # ESLint, react/no-danger en erreur
npm run build    # tsc -b && vite build
```

L'API doit tourner sur `127.0.0.1:8000` :

```bash
cd backend && uvicorn "app.main:create_app" --factory --port 8000
```

## Ce que l'interface ne fait jamais

**Elle ne calcule rien.** Chaque nombre affiché est lu dans la réponse de l'API.
Aucun composant ne refait une division pour l'affichage — un chiffre recalculé
côté écran est une seconde version du même chiffre, et les deux divergeront le
jour où quelqu'un changera un arrondi d'un seul côté. Les formateurs de
`lib/format.ts` mettent en forme ; ils ne dérivent pas.

**Elle n'invente pas ce que la plateforme ne sait pas faire.** Les capacités non
livrées arrivent du serveur — `risk_analysis_fr` sur une expédition,
`not_delivered_fr` sur la vue générale. Le jour où le moteur correspondant
arrivera, l'écran cessera de l'annoncer sans être modifié.

**Elle ne fait pas disparaître ce qui ne va pas.** Une parcelle dont la
recommandation est impossible reste dans la liste et sur la carte, en gris, avec
son motif. Une parcelle absente d'un écran est indiscernable d'une parcelle qui
va bien.

## Les deux panneaux

**« Pourquoi cette décision ? »** — les entrées avec leur puce de provenance,
puis les étapes numérotées avec le numéro d'équation FAO, puis les hypothèses,
puis les avertissements, puis la liste de qualité des données. L'ordre est celui
dans lequel un agronome conteste : qu'as-tu mesuré, qu'en as-tu fait, qu'as-tu
supposé, et quelle confiance dois-je accorder à l'ensemble.

La qualité des données est une **liste par entrée**, pas un score. « Fiabilité :
Moyenne » ne dit rien d'actionnable ; « l'humidité du sol vient du jeu de
démonstration » dit exactement quoi améliorer.

**« Sources et preuves »** — langage métier en surface, détail derrière un
dépliant, jamais un journal brut.

## La puce de provenance

Deux axes, jamais fusionnés en un mot. `SourceChip` porte `data-state` et
`data-origin` dans le DOM, ce qui permet à un test de vérifier qu'une valeur
n'a pas perdu sa provenance en chemin.

« Simulé » seul ne dirait pas si le nombre vient du jeu de démonstration ou d'un
fournisseur hors ligne remplaçant un fournisseur réel — deux faits différents.

Un élément de preuve porte un `DataState` et un nom de source, mais **pas**
d'origine. Il utilise donc `StateChip`, pas `SourceChip` : réutiliser la puce à
deux axes obligerait à inventer une origine pour remplir la case, ce qui est
exactement le défaut que les deux axes existent pour empêcher.

## Le copilote

La trace d'outils **est** la réponse ; la prose du modèle est un commentaire, dans
un panneau qui le dit. Voir `decisions/0012-la-trace-avant-la-prose.md`. Un test
donne au modèle une phrase annonçant « environ 300 m³ » alors que le moteur a
rendu 84 m³, et vérifie que l'écran affiche 84 m³.

Sans clé d'API, la page affiche le refus français du serveur. Elle ne fabrique
pas de réponse.

## La décision logistique

Trois composants, dans `components/logistics.tsx` : le bandeau de recommandation, le tableau des
options et la chronologie d'exposition. Plus une carte, `RouteMap`, qui trace le corridor actuel,
le corridor recommandé et les tronçons exposés.

Le couplage va dans un seul sens : **la recommandation pilote la carte.** Sélectionner une ligne
du tableau trace ce corridor et recadre ; la carte ne modifie jamais en retour ce que le tableau
considère comme sélectionné.

Deux règles portées par ces composants :

- **L'indicateur de perturbation ne s'affiche jamais sans sa réserve.** Un test le vérifie sur le
  paragraphe rendu, pas sur la donnée.
- **Une option écartée ne porte aucun chiffre.** Elle apparaît dans un dépliant, dénombrée, avec
  son motif chiffré. Une recommandation qui n'affiche que l'option gagnante ressemble à un
  oracle ; une qui montre ses rejets se laisse contester, ce qui est la seule façon d'être crue.

## Quand la carte ne peut pas s'afficher

Un poste sans WebGL — machine verrouillée, accélération désactivée, pilote ancien — fait échouer
le constructeur de MapLibre. Sans garde, l'exception remonte et emporte **toute** la page :
l'exploitant perd la recommandation en même temps que la carte.

L'échec est contenu, et les deux cas sont distingués : « la carte n'a pas pu s'afficher »
(rien n'est tracé) et « le fond de carte n'a pas pu être chargé » (les itinéraires sont là, le
fond manque). Dire le premier quand c'est le second décrédibilise l'écran.

## Sécurité de rendu

Les valeurs atteignent le DOM comme du **texte**. `react/no-danger` est une
erreur ESLint, et `src/test/rendering-safety.test.ts` balaie l'arbre source :
`dangerouslySetInnerHTML`, `innerHTML`, `insertAdjacentHTML` et `document.write`
n'apparaissent nulle part. La carte est le seul endroit où du contenu quitte
l'échappement de React — MapLibre possède ces nœuds — et elle construit ses
bulles avec `textContent`, ce qu'un test vérifie nommément.

Un nom de culture ou de fournisseur valant `<img src=x onerror=…>` est une ligne
réaliste dans un système qui accepte de la saisie libre, et celui-ci en accepte.

## Couleur

Cinq teintes portent du sens, et elles seules : vert faible, jaune modéré, orange
élevé, rouge critique, bleu recommandé. Tout le reste est neutre. Un tableau de
bord où chaque carte a son accent apprend à ignorer la couleur, ce qui est
précisément le signal que ce produit doit préserver.

## Localisation

Le mécanisme existe : locale, direction du texte, `lang` et `dir` sur l'élément
racine, repli sur la chaîne française pour toute clé manquante. Le catalogue
`ar-MA` est **vide, délibérément** — une interface agronomique ne se
machine-traduit pas.

Le vocabulaire agronomique et logistique n'est pas dans le catalogue : il arrive
du serveur en `*_label_fr`. C'est ce qui garantit que l'écran, les résultats
d'outils du copilote et le journal d'audit emploient le même mot.

Les chaînes de chrome des pages ne sont pas encore extraites (ligne 29 du
registre d'honnêteté).

## Les jauges de quota

Une jauge affiche **toujours** son dénominateur. « 87 » ne dit rien ; « 87 sur
100 questions » dit qu'il en reste treize, et c'est la seule forme sur laquelle
quelqu'un peut agir.

Trois cas, trois rendus distincts :

* **plafonné et passant** — la barre et les deux nombres ;
* **illimité** — aucune barre. Une barre vide se lirait « rien de consommé », une
  barre pleine « plafond atteint » ; les deux seraient faux, alors qu'il n'y a
  simplement pas de plafond à représenter ;
* **atteint** — le message du serveur et son remède, mot pour mot. L'interface ne
  rédige aucun refus : trois écrans qui le feraient chacun finiraient par en dire
  trois choses différentes.

L'unité s'accorde en nombre : « 1 expédition », pas « 1 expéditions ». Un
décompte s'écrit sans décimale, une dépense en porte deux.

## Démarrage, abonnement, administration

L'écran de **démarrage** ne connaît ni les étapes ni les valeurs qu'il propose :
les premières viennent de `/demarrage`, les secondes du catalogue de
l'organisation. Le jour où une étape disparaît, la page cesse de la demander sans
qu'on la modifie. Le formulaire ne demande **ni débit ni tarif de l'eau** : sans
eux la durée et le coût sont déclarés indisponibles, et les demander au démarrage
inciterait à inventer un chiffre pour passer à l'étape suivante.

L'**abonnement** dit qu'aucun paiement n'est traité, plutôt que de laisser un
bouton absent poser la question. L'avertissement sur la dépense du modèle est à
côté du chiffre, pas dans une page d'aide : un total présenté nu se lirait comme
un montant dû.

L'**administration** ne masque pas ses commandes à qui n'est pas administrateur :
elle affiche le refus du serveur, avec son remède. Un écran qui cache ses boutons
laisse croire qu'ils n'existent pas. Le panneau de conformité montre les
déclarations **et** les limites côte à côte ; la suppression de l'organisation
demande de recopier son identifiant, parce qu'une case à cocher se coche par
réflexe.

## Ce qui n'est pas livré

Stocks, fournisseurs, alertes et simulations ne sont **pas** des écrans vides :
ils n'existent pas. Un lien de navigation menant à un écran vide promet quelque
chose ; une absence assumée, énumérée sur la vue générale, n'engage rien.

L'interface ne peut pas afficher un calcul qui n'existe pas, et elle ne le simule
pas.
