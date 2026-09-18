"""Explainability: data quality, data lineage and the explanation object.

Trust is the product here. Three things are tracked for every recommendation:

* **Data quality** — which of the required inputs were actually available, and
  how reliable the resulting recommendation is.
* **Data lineage** — for every important value, where it came from (weather
  API, farmer input, IoT sensor, agronomic database, or calculation).
* **Explanation** — the inputs, the formulas applied, the assumptions made and
  the sources used, in French, so that a farmer can audit the decision without
  reading Python.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain.enums import DataOrigin, ReliabilityLevel
from app.domain.provenance import (
    HIGH_RELIABILITY_FLOOR,
    MEDIUM_RELIABILITY_FLOOR,
)

__all__ = [
    "DataQuality",
    "DataQualityCheck",
    "Explanation",
    "LineageEntry",
]


# La fiabilité et la pondération par origine viennent de `app.domain.provenance`
# et de `app.domain.enums`, pas d'ici.
#
# `agriflow` portait sa propre table de poids et ses propres bornes ; le produit
# fusionné en avait alors trois (cf. D9 du plan d'unification), toutes affichées
# sous le mot « fiabilité ». Deux échelles qui divergent d'un dixième produisent
# deux écrans qui se contredisent sur la même parcelle.


@dataclass
class DataQualityCheck:
    """One required input and whether we really have it."""

    key: str
    label_fr: str
    available: bool
    source: DataOrigin | None = None
    detail_fr: str = ""
    critical: bool = True

    @property
    def source_label_fr(self) -> str | None:
        return self.source.label_fr if self.source else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label_fr": self.label_fr,
            "available": self.available,
            "source": self.source.value if self.source else None,
            "source_label_fr": self.source_label_fr,
            "detail_fr": self.detail_fr,
            "critical": self.critical,
        }


@dataclass
class DataQuality:
    checks: list[DataQualityCheck] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(
        self,
        key: str,
        label_fr: str,
        available: bool,
        source: DataOrigin | None = None,
        detail_fr: str = "",
        critical: bool = True,
    ) -> None:
        self.checks.append(
            DataQualityCheck(
                key=key,
                label_fr=label_fr,
                available=available,
                source=source,
                detail_fr=detail_fr,
                critical=critical,
            )
        )

    @property
    def score(self) -> float:
        """0..1 quality score, weighted by source and by criticality."""
        if not self.checks:
            return 0.0
        total_weight = 0.0
        earned = 0.0
        for check in self.checks:
            weight = 2.0 if check.critical else 1.0
            total_weight += weight
            if check.available:
                # Une entrée présente mais sans origine connue ne reçoit aucun
                # crédit. L'ancienne version lui accordait 0,4 par défaut, ce
                # qui revenait à récompenser une provenance qu'on ignore.
                earned += weight * (
                    check.source.reliability_weight if check.source else 0.0
                )
        return earned / total_weight if total_weight else 0.0

    @property
    def reliability(self) -> ReliabilityLevel:
        """Classe de fiabilité affichée.

        Une entrée critique absente plafonne à « Faible » quel que soit le
        score : dix entrées excellentes et une manquante ne font pas une
        recommandation fiable, elles font une recommandation incalculable.
        """
        if any(c.critical and not c.available for c in self.checks):
            return ReliabilityLevel.LOW
        score = self.score
        if score >= HIGH_RELIABILITY_FLOOR:
            return ReliabilityLevel.HIGH
        if score >= MEDIUM_RELIABILITY_FLOOR:
            return ReliabilityLevel.MEDIUM
        return ReliabilityLevel.LOW

    @property
    def reliability_label_fr(self) -> str:
        return self.reliability.label_fr

    @property
    def missing_critical(self) -> list[str]:
        return [c.label_fr for c in self.checks if c.critical and not c.available]

    def to_dict(self) -> dict[str, Any]:
        return {
            "checks": [c.to_dict() for c in self.checks],
            "score": round(self.score, 3),
            "reliability": self.reliability.value,
            "reliability_label_fr": self.reliability_label_fr,
            "missing_critical": self.missing_critical,
            "notes": self.notes,
        }


@dataclass
class LineageEntry:
    """Where one displayed value comes from."""

    key: str
    label_fr: str
    value: Any
    unit: str
    source: DataOrigin
    detail_fr: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label_fr": self.label_fr,
            "value": self.value,
            "unit": self.unit,
            "source": self.source.value,
            "source_label_fr": self.source.label_fr,
            "detail_fr": self.detail_fr,
        }


@dataclass
class Explanation:
    """The object behind the "Pourquoi cette décision ?" button."""

    summary_fr: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    calculations: dict[str, Any] = field(default_factory=dict)
    steps: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    data_sources: list[LineageEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_source(
        self,
        key: str,
        label_fr: str,
        value: Any,
        unit: str,
        source: DataOrigin,
        detail_fr: str = "",
    ) -> None:
        self.data_sources.append(
            LineageEntry(
                key=key,
                label_fr=label_fr,
                value=value,
                unit=unit,
                source=source,
                detail_fr=detail_fr,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary_fr": self.summary_fr,
            "inputs": self.inputs,
            "calculations": self.calculations,
            "steps": self.steps,
            "assumptions": self.assumptions,
            "data_sources": [s.to_dict() for s in self.data_sources],
            "warnings": self.warnings,
        }
