# Serveur MCP

Le pont MCP est le **second** consommateur du registre d'outils. Il ne définit aucune capacité,
et c'est tout son intérêt.

```
backend/app/tools/registry.py          ← le seul endroit où une capacité est définie
        ├── backend/app/mcp_server.py     l'expose en MCP (Claude Desktop, Claude Code)
        └── backend/app/agent/service.py  la consomme en process via l'API Anthropic
```

Définir chaque outil deux fois — une fois pour MCP, une fois pour la boucle d'agent — produit
deux définitions qui divergent en silence, et l'écart se découvre chez un client externe,
c'est-à-dire au pire endroit. Ici, ajouter un outil au registre le rend immédiatement disponible
aux deux, avec la même validation, les mêmes rôles et le même journal d'audit. Un test le
vérifie : la liste MCP et la liste du registre doivent être égales.

## Les cinq outils

| Outil | Décision soutenue | Écrit ? |
|---|---|---|
| `list_fields` | Identifier la parcelle dont il est question | non |
| `calculate_irrigation_requirement` | Irriguer aujourd'hui, reporter, ou surveiller | non |
| `get_shipment` | Identifier l'expédition et ses contraintes | non |
| `analyze_shipment_risk` | Maintenir le plan, changer d'itinéraire, ou décaler le départ | non |
| `create_recommendation` | Formaliser une proposition, sans l'appliquer | oui — et il n'exécute rien |

Les outils sont **taillés pour la décision, pas pour le calcul**.
`calculate_irrigation_requirement` résout à lui seul la culture, le sol, le système, le stade,
l'humidité et la météo, puis enchaîne ET0 → ETc → bilan → besoin → volume → durée → coût.
`analyze_shipment_risk` fait de même côté logistique : rattachement au graphe routier,
échantillonnage météo, exposition tronçon par tronçon, génération d'options, filtrage des
contraintes dures, classement multicritère. Exposer ces étapes comme autant d'outils inviterait
le modèle à les assembler lui-même, c'est-à-dire à faire exactement l'arithmétique qui lui est
interdite.

Un test vérifie que l'outil et la route HTTP rendent **le même nombre** : les deux passent par le
même service, et un écart entre eux serait invisible jusqu'au jour où un exploitant compare deux
captures.

Un seul outil écrit, et il ne déclenche aucune action : il enregistre une proposition en attente
de validation humaine. Aucun outil n'irrigue, ne déplace une expédition ni n'engage une dépense.

## L'isolation traverse le protocole

Une session MCP est ouverte **pour une organisation**, à partir d'un jeton. `tenant_id` n'est le
paramètre d'aucun outil — il n'existe aucun champ par lequel un client pourrait en désigner une
autre — et la session de base de données est ouverte liée à cette organisation, sous politique
`FORCE ROW LEVEL SECURITY`. Un test le montre dans les deux sens : la même référence
d'expédition résout depuis son organisation et renvoie « introuvable » depuis une autre.

Le filtrage par rôle est celui du registre, pas un second contrôle : un `ANALYST` ne voit pas
`create_recommendation` dans la liste, donc il ne l'apprend pas, donc il ne la tente pas.

## Le contenu reste une donnée

Un client MCP est un modèle. Chaque résultat repart encapsulé dans `<donnees_externes>`, avec un
rappel placé **après** le bloc fermé : ce que le modèle lit en dernier est la consigne, pas la
donnée. Une balise de fermeture glissée dans un nom de site est neutralisée avant l'envoi.
L'encapsulation est appliquée par le registre, donc elle ne dépend pas de la vigilance de celui
qui ajoute un outil.

## Format

`list_tools()` rend le dialecte MCP (`inputSchema`), l'agent le dialecte Anthropic
(`input_schema`) ; la traduction est le seul travail du pont. Chaque schéma d'entrée porte
`additionalProperties: false` : un champ inconnu est refusé, jamais ignoré. Ignoré, un
`tenant_id` glissé par un client laisserait croire qu'il a été pris en compte.

`call_tool()` rend `{"content": [{"type": "text", "text": …}], "isError": bool}`. Un échec est
un `isError` avec une charge utile française ; jamais une exception qui remonterait au client, et
jamais une valeur substituée au résultat manquant.

## Ce qui n'existe pas encore

**Aucun transport.** `McpBridge` traduit et exécute, et il est testé ; aucun processus stdio n'a
été lancé ni attaché à un client MCP réel. C'est la ligne 23 de `docs/ce-qui-nest-pas-mesure.md`.
