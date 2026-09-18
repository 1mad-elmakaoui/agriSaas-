"""Configuration, et les configurations refusées au démarrage.

Le principe vient de `text_to_sql` et il est meilleur que l'échec à
l'exécution : **si la configuration est fausse, on ne démarre pas.** Une clé
absente découverte à la première requête d'un client est un incident ; la même
découverte au démarrage est un déploiement qui n'a pas eu lieu.

La règle de partage avec la dégradation gracieuse d'`atlasagri` est explicite :

* configuration fausse (rôle trop privilégié, DSN partagé, authentification
  désactivée en production) → **refus de démarrer** ;
* monde extérieur indisponible (Copernicus injoignable, OSRM absent) →
  **message français nommant le correctif**, et le reste du produit continue.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.errors import ConfigurationError

Environment = Literal["local", "test", "staging", "production"]

__all__ = ["Settings", "get_settings"]


class Settings(BaseSettings):
    """Réglages de la plateforme, préfixe `ATLAS_`, séparateur `__`."""

    model_config = SettingsConfigDict(
        env_prefix="ATLAS_",
        env_nested_delimiter="__",
        env_file=".env",
        extra="ignore",
    )

    environment: Environment = "local"

    # -- bases de données ------------------------------------------------
    #: Rôle applicatif : lecture-écriture sur le schéma `app`.
    database_url: str = "postgresql+asyncpg://atlas_app@127.0.0.1:5432/atlas"
    #: Rôle analytique : SELECT sur les vues de `analytics`, et rien d'autre.
    #: Doit être un rôle *différent* — c'est ce qui rend la couche 2 porteuse.
    analytics_database_url: str = (
        "postgresql+asyncpg://atlas_analytics_ro@127.0.0.1:5432/atlas"
    )
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_echo: bool = False

    # -- authentification ------------------------------------------------
    # Placeholder refusé en production par `_production_guards` : un défaut
    # utilisable serait bien plus dangereux qu'un défaut manifestement faux.
    jwt_secret: str = "change-me-in-any-real-deployment"  # noqa: S105
    jwt_ttl_minutes: int = Field(default=720, ge=5)
    require_auth: bool = True

    # -- modèle de langage -----------------------------------------------
    anthropic_api_key: str | None = None
    llm_model: str = "claude-opus-5"
    llm_provider: Literal["anthropic", "fake"] = "anthropic"
    agent_max_tool_iterations: int = Field(default=8, ge=1, le=30)

    # -- récupération ----------------------------------------------------
    #: `intfloat/multilingual-e5-small` et non `BAAI/bge-small-en-v1.5` : les
    #: questions arrivent en français et en arabe, et un modèle anglais-seul
    #: échoue *silencieusement* — il renvoie quand même des tables, donc la
    #: requête est produite, validée, exécutée et expliquée sur les mauvaises.
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_dim: int = Field(default=384, ge=8, le=8192)
    embedding_provider: Literal["local", "hashing"] = "local"

    # -- démonstration ---------------------------------------------------
    demo_mode: bool = False

    # -- conformité (loi 09-08 / CNDP) -----------------------------------
    #: Pays où les données sont **effectivement** hébergées, déclaré par
    #: l'exploitant. Vide par défaut, et l'interface affiche alors « non
    #: déclarée » : le code ne peut pas savoir où tourne sa base, et annoncer
    #: « Maroc » par défaut serait une affirmation de conformité que personne
    #: n'a vérifiée.
    data_residency_country: str | None = None
    #: Nom de l'hébergeur, pour la même raison et avec la même réserve.
    data_residency_provider: str | None = None
    #: Numéro de déclaration CNDP, quand la déclaration a été faite.
    cndp_declaration_number: str | None = None
    #: Durée de conservation des lignes du journal d'audit. La purge n'est pas
    #: automatisée : la valeur est **annoncée**, et le grand livre dit qu'elle
    #: n'est pas encore appliquée par une tâche.
    audit_retention_days: int = Field(default=1_095, ge=30)

    # -- géospatial ------------------------------------------------------
    #: PostGIS est requis en production (§4.7). En développement, son absence
    #: dégrade les fonctions spatiales avec un avertissement — elle ne casse
    #: pas le démarrage.
    require_postgis: bool = False

    @model_validator(mode="after")
    def _reject_shared_dsn(self) -> Settings:
        if self.database_url == self.analytics_database_url:
            raise ValueError(
                "database_url and analytics_database_url must differ. The analytics "
                "role must hold SELECT on the analytics views and nothing else; "
                "sharing the operational DSN discards the layer that holds when our "
                "own code is the bug."
            )
        return self

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        """Configurations refusées en production.

        La liste étend celle de `text_to_sql` avec ce que la multi-location
        ajoute. Chaque entrée correspond à une garantie annoncée ailleurs dans
        le produit : la laisser passer ferait mentir la documentation.
        """
        if self.environment != "production":
            return self

        refusals: list[str] = []
        if not self.require_auth:
            refusals.append("require_auth cannot be disabled in production")
        if self.jwt_secret == Settings.model_fields["jwt_secret"].default:
            refusals.append("jwt_secret is still the built-in default")
        if self.llm_provider == "fake":
            refusals.append("llm_provider='fake' must not serve production traffic")
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            refusals.append("anthropic_api_key is required when llm_provider='anthropic'")
        if self.embedding_provider == "hashing":
            refusals.append(
                "embedding_provider='hashing' is lexical-only with no semantic "
                "understanding; it must not serve production retrieval"
            )
        if self.demo_mode:
            refusals.append("demo_mode=true must never be set in production")
        if not self.data_residency_country:
            refusals.append(
                "data_residency_country must be declared in production: the "
                "compliance page states where the data lives, and an undeclared "
                "location cannot be stated honestly"
            )
        if not self.require_postgis:
            refusals.append(
                "require_postgis must be true in production: field polygons, route "
                "segments and zonal statistics all depend on it"
            )
        if refusals:
            raise ConfigurationError(
                "Refusing to start in production:\n  - " + "\n  - ".join(refusals)
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Réglages mis en cache : une variable mal posée échoue une fois, au
    démarrage, et non à chaque requête."""
    return Settings()
