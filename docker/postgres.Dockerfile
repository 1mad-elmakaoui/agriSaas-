# PostgreSQL 16 portant **PostGIS et pgvector**.
#
# Aucune image publique ne fournit les deux : `postgis/postgis` n'a pas
# pgvector, `pgvector/pgvector` n'a pas PostGIS. Le produit fusionné a besoin
# des deux dans le même serveur — polygones de parcelle et tronçons
# d'itinéraire d'un côté, index de schéma de l'agent d'analyse de l'autre.
#
# Cette image est construite en phase 1 et non en phase 8, délibérément :
# développer les phases 2 à 7 sur une base qui ne sera jamais celle de
# production, c'est découvrir l'extension manquante à la fin.
FROM postgis/postgis:16-3.4

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-pgvector \
    && rm -rf /var/lib/apt/lists/*
