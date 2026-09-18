"""Moteurs logistiques : réseau, exposition, coût, alternatives.

Purs, comme tout ce qui vit sous `domain/`. Aucun de ces modules n'ouvre de
connexion, n'appelle de fournisseur météo ni ne lit la base : la météo leur est
**donnée**, et c'est ce qui rend l'exposition d'un itinéraire reproductible à
l'identique dans un test.

C'est la différence principale avec le code d'origine, où l'évaluation d'un
itinéraire appelait un registre global de fournisseurs depuis la couche service.
Elle n'y était donc testable qu'avec un réseau ou un monkeypatch, et la partie
intéressante — le déplacement du véhicule dans le temps face à une fenêtre de
perturbation — n'avait aucun test.
"""
