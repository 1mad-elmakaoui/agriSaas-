# Image de l'API.
#
# Deux choses méritent une phrase, parce qu'elles ne se devinent pas.
#
# **`--factory`.** L'application est construite par `create_app(settings)`, il
# n'existe aucun objet `app` importable au niveau du module. C'était le défaut
# que cette image portait avant d'avoir jamais été démarrée : `uvicorn
# app.main:app` échoue à l'import. Une image qu'on ne lance pas ne dit rien.
#
# **Le dossier `ca/`.** Derrière un mandataire d'entreprise qui inspecte le
# trafic TLS, `pip` échoue sur `CERTIFICATE_VERIFY_FAILED` sans dire que la cause
# est le réseau. Les certificats déposés dans `ca/` sont ajoutés au magasin avant
# l'installation. Le dossier est vide par défaut.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Autorités supplémentaires, ajoutées au magasin système **et** utilisées par
# pip. Ajoutées au fichier plutôt que posées comme fichiers séparés :
# `update-ca-certificates` exige un certificat par fichier, et un opérateur qui
# dépose un bundle recevrait un avertissement silencieux et une construction qui
# échoue plus loin.
COPY ca /usr/local/share/ca-certificates/atlas-extra
RUN set -eu; \
    if ls /usr/local/share/ca-certificates/atlas-extra/*.crt >/dev/null 2>&1; then \
        cat /usr/local/share/ca-certificates/atlas-extra/*.crt \
            >> /etc/ssl/certs/ca-certificates.crt; \
    fi
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

COPY pyproject.toml alembic.ini ./
COPY app ./app
COPY migrations ./migrations
RUN pip install --no-cache-dir .

# Utilisateur non privilégié : le conteneur n'a aucune raison d'être root.
RUN useradd --create-home --uid 10001 atlas && chown -R atlas:atlas /app
USER atlas

EXPOSE 8000

# La sonde interroge la route de santé du produit, celle qui énumère les
# contrôles d'isolation — pas un port ouvert. Un port ouvert prouve qu'un
# processus vit, pas que le produit est en état de servir.
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=6 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/sante', timeout=4)"]

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
