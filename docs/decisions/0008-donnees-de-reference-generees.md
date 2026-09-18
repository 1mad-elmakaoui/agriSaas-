# 0008 — Le référentiel est généré depuis sa source, jamais retranscrit

**Date** : 2026-08-25 · **État** : Acceptée

## Contexte

La compétence `agri-water-science` pose une règle qu'aucun test ne peut remplacer :

> Un nombre agronomique faux mais plausible ne peut être attrapé par aucun test en aval.

Un exploitant ne verra pas qu'une dose est 12 % trop haute ; un relecteur ne verra pas qu'un Kc
a été interpolé sur le mauvais stade. Or `agriflow` porte déjà dix cultures, sept sols et cinq
systèmes, chacun avec sa citation FAO — et le travail de la phase 2 était de les faire entrer
dans le schéma fusionné.

Recopier un tableau de Kc à la main est exactement l'endroit où une valeur fausse entre.

## Décision

La transformation est **exécutée par un script** (`scripts/build_reference.py`), pas faite à la
main. Le script renomme les codes en anglais majuscule, réunit les attributs logistiques
d'`atlasagri`, éclate les stades en lignes — et **ne touche à aucune valeur numérique**.

Deux conséquences de conception en découlent.

**Aucun défaut implicite.** La chaîne du froid n'existe que pour six des dix cultures dans
`atlasagri`. Un `dict.get(code, défaut)` aurait fait hériter les quatre autres d'un choix que
personne n'a fait — et ce choix décide si un entrepôt sans froid est écarté comme infaisable. Le
script lève sur une culture non couverte.

**Une absence porte sa raison.** La base impose qu'un Ky présent porte sa source et qu'un Ky
absent porte son explication ; le silence — ni valeur, ni raison — est ce que la contrainte
`ck_crops_ky_documented_or_explained` interdit. C'est ainsi qu'une absence cesse de ressembler
à un oubli.

## Conséquence

Les tests ne peuvent pas vérifier qu'un Kc est *juste* : seule la publication le peut. Ils
vérifient ce qui est vérifiable et ce qui, en pratique, attrape les vraies erreurs — que chaque
valeur cite une publication, que la capacité au champ dépasse le point de flétrissement, que les
quatre stades existent dans l'ordre, que la somme des stades fait un cycle plausible, et qu'au
moins une culture exerce le chemin « pas de Ky documenté ».

Ce dernier point mérite d'être dit : si toutes les cultures avaient un Ky, le chemin
« estimation de rendement indisponible » ne serait parcouru ni par les tests ni par la
démonstration, et il casserait le jour où une culture sans Ky arrive. Quatre des dix cultures
n'ont pas de Ky publié, et c'est une propriété du jeu, pas un accident.
