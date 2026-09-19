# Autorités de certification supplémentaires

Déposez ici les certificats (`*.crt`, format PEM) d'une autorité interne — celle
d'un mandataire d'entreprise qui inspecte le trafic TLS, typiquement. Ils sont
ajoutés au magasin du conteneur pendant la construction, avant l'installation
des dépendances.

Sans cela, `pip` échoue avec `CERTIFICATE_VERIFY_FAILED` derrière un tel
mandataire — et l'erreur ne dit pas que la cause est le réseau de l'entreprise.

Le dossier est **vide par défaut**, et c'est voulu : un certificat versionné dans
un dépôt est un certificat que personne ne révoque.
