# 0017 — Les quotas comptent des stocks ou des flux, et l'usage s'écrit en événements

**Date** : 2026-09-18  ·  **État** : Acceptée

## Contexte

La §6 demande quatre grandeurs métrées : parcelles, expéditions, messages au
copilote, requêtes d'analyse. Écrites côte à côte dans une table de plans, elles
ont l'air d'être la même chose. Elles ne le sont pas.

Une parcelle est un **stock** : la question est « combien en existe-t-il
maintenant ». Un message au copilote est un **flux** : la question est « combien
en ont été consommés cette période ».

Les confondre produit deux défauts opposés, et tous deux silencieux :

* compter un stock en événements fait consommer deux unités à une parcelle
  supprimée puis recréée ;
* compter un flux en lignes vivantes fait consommer une unité éternelle à un
  message, ou — pire — rend un mois de consommation en supprimant une ligne.

Un exploitant ne peut découvrir ni l'un ni l'autre : il voit un chiffre, il n'a
aucun moyen de savoir qu'il est faux.

## Options

**A — Un compteur par métrique, incrémenté à chaque acte.** Simple, rapide à
lire. Un compteur entretenu à côté de ses actes dérive : un acte annulé,
une transaction reprise, une migration, et le compteur cesse de correspondre à ce
qui s'est passé. La dérive se découvre le jour où un client conteste sa facture,
c'est-à-dire trop tard, et elle n'est pas rattrapable — les actes ne sont plus là
pour être recomptés.

**B — Un événement par acte, agrégé à la lecture.** Chaque appel écrit une ligne
avec ses jetons réels, son coût dérivé, son modèle et son identifiant de
corrélation. Le compteur est une somme, donc il ne peut pas diverger de ce qu'il
compte. Coût : une agrégation par vérification de quota.

**C — Les deux : événements pour les flux, comptage de lignes pour les stocks.**

## Décision

**C.** Les flux produisent des événements (`app.usage_events`) ; les stocks se
comptent en interrogeant la table concernée (`app.fields`, `app.shipments`).

Entretenir un second compteur de parcelles à côté de `app.fields` créerait deux
vérités, et c'est toujours la seconde qu'on finit par croire. À l'inverse,
compter un flux en lignes vivantes n'a aucun sens : rien ne représente « un
message consommé le 3 du mois » sinon l'événement lui-même.

La distinction est portée par le type `QuotaKind`, et le message de refus en
dépend : un stock se libère en supprimant une entrée, un flux repart au début du
mois. Dire « supprimez une entrée » à quelqu'un qui a épuisé ses questions serait
un conseil faux.

Trois conséquences s'ensuivent, et méritent d'être écrites :

**`None` signifie illimité, jamais zéro.** Un plan Entreprise sans plafond de
parcelles porte `NULL`. Un zéro s'y lirait comme une interdiction totale, et la
vérification refuserait la première parcelle.

**`used >= limit` refuse, et non `used > limit`.** `used` est la consommation
*avant* l'acte demandé : autoriser à égalité vendrait une unité de plus que le
plan n'en contient, à chaque plafond et à chaque période.

**Les jetons et le coût vivent dans deux colonnes distinctes.** Les jetons sont
**réels** — ils viennent de la réponse de l'API. Le coût est **dérivé** d'une
grille tarifaire recopiée, et vaut `NULL` pour un modèle non tarifé, jamais zéro
qui se lirait « gratuit ». La §6 demande « des données réelles, pas une
estimation » ; le nombre d'appels et les jetons le sont, le coût ne peut pas
l'être tant qu'aucun total n'a été rapproché d'une facture (grand livre,
ligne 22). Les séparer est la seule façon de ne pas présenter l'un pour l'autre.

## Conséquence

`app/domain/quotas.py` décide sans base ; `QuotaService` compte. L'arithmétique
d'un plafond se teste donc sans PostgreSQL, et c'est là que les erreurs se
logent.

La vérification a lieu **avant** l'acte et l'enregistrement **après** : facturer
une panne est la façon la plus sûre de discréditer un compteur d'usage. Deux
requêtes simultanées peuvent en revanche passer la même vérification et dépasser
le plafond du nombre d'appels en vol ; c'est inscrit au grand livre (ligne 45)
plutôt que corrigé par un verrou dont personne n'a encore mesuré le besoin.

Descendre de plan ne supprime rien. Les parcelles existantes restent, la création
suivante est refusée. Supprimer des données pour faire tenir un plan serait une
décision que personne n'a prise.
