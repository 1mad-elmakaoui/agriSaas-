# 0010 — Tenir la frontière de vérification du copilote, et la nommer

**Date** : 2026-08-25  ·  **État** : Acceptée

## Contexte

La phase 4 relie les moteurs déterministes à un agent. Deux choses très différentes peuvent
être appelées « l'agent fonctionne » :

1. **La mécanique de la boucle** — les bons outils sont appelés avec la bonne organisation, les
   résultats sont encapsulés, ils repartent en un seul message utilisateur, la borne
   d'itérations produit une issue visible, un refus est relayé sans relance, un outil qui échoue
   ne casse pas la conversation.
2. **Le jugement du modèle** — devant « Est-ce que je dois irriguer P03 ? », Claude choisit
   `calculate_irrigation_requirement` plutôt que d'estimer une dose lui-même.

La première est de l'ingénierie ordinaire et se vérifie hors ligne. La seconde demande une clé
d'API et un appel réseau, dont cet environnement ne dispose pas. Le risque n'est pas de ne pas
pouvoir vérifier la seconde : c'est de livrer une suite verte qui laisse croire qu'on l'a fait.

## Options

**A — Ne rien tester sans clé.** Honnête et inutile : la mécanique de la boucle est exactement
l'endroit où les erreurs coûteuses se logent (résultats répartis sur plusieurs messages, tenant
passé en paramètre, résultat non encapsulé), et aucune de ces erreurs ne se voit dans une
réponse de modèle réussie.

**B — Un fournisseur scripté, présenté comme « les tests de l'agent ».** Le piège. Une suite
nommée `test_agent` qui passe sans clé se lit comme « l'agent est vérifié ».

**C — Un fournisseur scripté, avec la frontière écrite dans le code, dans les tests et dans le
registre d'honnêteté.** Le fournisseur ne *simule* pas un modèle : il rejoue une séquence
décidée par le test. La distinction est portée par le nom (`FakeProvider`, `scripted_*`), par
les en-têtes de module, et par une ligne du registre qui dit que la boucle n'a jamais été
exécutée contre une clé réelle.

**D — Enregistrer des échanges réels et les rejouer.** Ce serait la meilleure réponse ; elle
demande une clé pour produire l'enregistrement. Reste ouverte.

## Décision

**C**, avec deux conséquences de conception :

- Le fournisseur de modèle devient une **dépendance FastAPI** (`get_llm_provider`) plutôt qu'un
  objet construit dans la route. Sans cela, les deux questions de la phase ne pourraient être
  exercées qu'en instanciant `AgentService` à la main, et le chemin HTTP — authentification,
  portée de session, journal d'audit, sérialisation de la trace — resterait non testé.
- Substituer un fournisseur scripté reste **impossible par configuration** : `llm_provider =
  "fake"` est refusé hors développement par `_production_guards`, et une surcharge de dépendance
  ne peut être formulée que depuis du code de test. La porte de vérification n'est pas une porte
  de simulation.

## Conséquence

Ce qui est désormais vérifié, sans clé et sans réseau : l'appel parallèle et le message unique
de retour, la réinjection des blocs bruts, l'encapsulation systématique, la borne d'itérations,
le relais d'un refus, la traduction d'une panne de fournisseur en erreur française, le
filtrage des outils par rôle, l'absence de paramètre d'organisation, et les deux questions de
la phase de bout en bout à travers le routeur.

Ce qui ne l'est pas, et qui est inscrit au registre : que le modèle choisisse le bon outil, que
sa prose ne retape pas un chiffre au lieu de le relayer, et que l'estimation de coût corresponde
à une facture. Une clé, un jeu de questions et une exécution publiée le combleraient — et le
jour où cette exécution aura lieu, l'option D devient réalisable presque gratuitement.
