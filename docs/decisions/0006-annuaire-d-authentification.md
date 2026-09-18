# 0006 — Un annuaire global pour l'authentification

**Date** : 2026-08-24 · **État** : Acceptée · **Découverte de mise en œuvre**

## Contexte

`FORCE ROW LEVEL SECURITY` rend visible un problème que l'isolation applicative masquait : pour
lire la fiche d'un utilisateur il faut connaître son organisation, et pour connaître son
organisation il faut avoir lu sa fiche. La connexion est la seule opération légitimement
inter-organisations du produit.

## Options

1. **Politique `USING (true)` pour un rôle de connexion.** Lui rend tous les utilisateurs
   lisibles — un rôle de plus, et une politique permissive de plus à ne jamais élargir.
2. **Fonction `SECURITY DEFINER`.** Ne fonctionne pas : `FORCE` soumet aussi le propriétaire de
   la table à ses politiques, donc la fonction n'échappe à rien.
3. **Table d'aiguillage explicitement globale.**

## Décision

Option 3. `app.user_directory` contient un courriel, un identifiant d'utilisateur et une
organisation — **aucun secret, aucune donnée métier**. L'empreinte du mot de passe reste dans
`users`, sous politique. La table n'est jamais exposée par un point d'entrée HTTP.

C'est la seule table du schéma qui porte `tenant_id` sans être soumise à l'isolation. La
dérogation est énumérée dans `RLS_EXEMPT_TABLES` et un test vérifie que l'ensemble est
exactement celui-là : une seconde dérogation fait échouer la suite.

Deux détails accompagnent la décision. Un **message d'échec unique** pour courriel inconnu, mot
de passe faux et compte désactivé — distinguer renseignerait sur les comptes existants. Et une
**vérification factice** sur courriel inconnu : sans elle, l'écart entre un retour immédiat et un
bcrypt à douze tours est un oracle mesurable de l'extérieur.

## Conséquence

Ce que cela coûte : deux écritures à garder en accord, faites dans la même transaction.

Ce que cela ferme, et qu'il faut assumer : `atlasagri` distinguait « ressource inexistante » de
« ressource d'une autre organisation » et comptait les secondes comme tentatives. Sous RLS la
ligne est simplement invisible, la distinction disparaît, et la reconstituer exigerait une
lecture hors politique — c'est-à-dire rouvrir le trou. Les deux cas deviennent un 404.
