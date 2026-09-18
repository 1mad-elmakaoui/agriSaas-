"""Live-catalog domain model.

:class:`CatalogSnapshot` is the single source of truth that the AST validator
checks generated SQL against. Everything here is a plain immutable value type
with no database dependency, so the entire validation layer -- the highest-value
component in the system -- is unit-testable against a fixture catalog with no
PostgreSQL instance running.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, PrivateAttr

DEFAULT_SCHEMA = "analytics"


class TypeCategory(StrEnum):
    """Coarse type grouping used for join-compatibility checks.

    Deliberately coarse. The goal is to catch a join of ``orders.total`` to
    ``customers.name``, not to reimplement PostgreSQL's operator resolution --
    that is what ``EXPLAIN`` is for.
    """

    NUMERIC = "numeric"
    TEXT = "text"
    TEMPORAL = "temporal"
    BOOLEAN = "boolean"
    UUID = "uuid"
    JSON = "json"
    BINARY = "binary"
    ARRAY = "array"
    UNKNOWN = "unknown"


_NUMERIC_TYPES = frozenset(
    {
        "smallint", "integer", "bigint", "decimal", "numeric", "real",
        "double precision", "smallserial", "serial", "bigserial", "money",
        "int2", "int4", "int8", "float4", "float8",
    }
)
_TEXT_TYPES = frozenset(
    {"character varying", "varchar", "character", "char", "text", "citext", "name", "bpchar"}
)
_TEMPORAL_TYPES = frozenset(
    {
        "date", "time", "timetz", "timestamp", "timestamptz", "interval",
        "timestamp without time zone", "timestamp with time zone",
        "time without time zone", "time with time zone",
    }
)
_BOOLEAN_TYPES = frozenset({"boolean", "bool"})
_JSON_TYPES = frozenset({"json", "jsonb"})
_BINARY_TYPES = frozenset({"bytea"})


def categorize_type(data_type: str) -> TypeCategory:
    """Map a PostgreSQL type name to a :class:`TypeCategory`.

    Unrecognised types map to ``UNKNOWN``, which is treated as compatible with
    everything by :func:`types_compatible`. Failing open on ignorance is
    deliberate: rejecting a valid query because we do not recognise a domain or
    enum type is a worse outcome than letting ``EXPLAIN`` catch a real mismatch.
    """
    normalized = data_type.strip().lower()
    if normalized.endswith("[]") or normalized.startswith("_"):
        return TypeCategory.ARRAY
    if normalized in _NUMERIC_TYPES:
        return TypeCategory.NUMERIC
    if normalized in _TEXT_TYPES:
        return TypeCategory.TEXT
    if normalized in _TEMPORAL_TYPES:
        return TypeCategory.TEMPORAL
    if normalized in _BOOLEAN_TYPES:
        return TypeCategory.BOOLEAN
    if normalized == "uuid":
        return TypeCategory.UUID
    if normalized in _JSON_TYPES:
        return TypeCategory.JSON
    if normalized in _BINARY_TYPES:
        return TypeCategory.BINARY
    if normalized.startswith(("character varying", "numeric", "decimal", "timestamp", "time ")):
        # Parameterised spellings, e.g. "character varying(64)", "numeric(12,2)".
        return categorize_type(normalized.split("(", 1)[0])
    return TypeCategory.UNKNOWN


def types_compatible(left: TypeCategory, right: TypeCategory) -> bool:
    """Whether two categories can plausibly be compared with ``=``.

    ``UNKNOWN`` is compatible with everything (see :func:`categorize_type`).
    Note ``UUID`` is *not* compatible with ``TEXT``: PostgreSQL has no implicit
    cast and ``uuid = text`` raises.
    """
    if TypeCategory.UNKNOWN in (left, right):
        return True
    return left == right


class TableRef(BaseModel):
    """Schema-qualified table identity. Hashable, usable as a dict key."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_name: str = DEFAULT_SCHEMA
    table: str

    @property
    def qualified(self) -> str:
        return f"{self.schema_name}.{self.table}"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.qualified

    @classmethod
    def parse(cls, name: str, default_schema: str = DEFAULT_SCHEMA) -> TableRef:
        """Parse ``"schema.table"`` or ``"table"`` into a ref.

        Identifiers are lower-cased, matching PostgreSQL's folding of unquoted
        identifiers. Quoted identifiers are stripped of their quotes and keep
        their case.
        """
        parts = [_unquote(p) for p in _split_qualified(name)]
        if len(parts) >= 2:
            return cls(schema_name=parts[-2], table=parts[-1])
        return cls(schema_name=default_schema, table=parts[-1])


class ColumnRef(BaseModel):
    """Schema-qualified column identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_name: str = DEFAULT_SCHEMA
    table: str
    column: str

    @property
    def table_ref(self) -> TableRef:
        return TableRef(schema_name=self.schema_name, table=self.table)

    @property
    def qualified(self) -> str:
        return f"{self.schema_name}.{self.table}.{self.column}"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.qualified


class ColumnInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: ColumnRef
    data_type: str
    is_nullable: bool
    ordinal: int
    comment: str | None = None
    default: str | None = None
    is_primary_key: bool = False

    #: ``pg_stats.n_distinct``. Positive is an absolute count of distinct
    #: values; negative is a ratio of the row count. ``None`` means the table
    #: has never been ANALYZEd.
    n_distinct: float | None = None
    #: Valeurs d'exemple, **jamais échantillonnées dans les données**.
    #:
    #: Le système d'origine les tirait de `pg_stats`. Ici, les vues portent sur
    #: des tables multi-locataires : une valeur d'exemple issue des statistiques
    #: du planificateur serait un nom de client, une référence d'expédition ou
    #: un code de parcelle appartenant à **une autre organisation**, et elle
    #: apparaîtrait à la fois dans l'index de schéma et dans le SQL généré.
    #:
    #: Elles viennent donc d'un registre d'énumérations déclaré en Python
    #: (`app/analytics/enums.py`) : des valeurs stockées, qui n'appartiennent à
    #: personne. C'est la ligne 14 du registre d'honnêteté, refermée par
    #: construction plutôt que par une règle de filtrage qu'il faudrait tenir.
    sample_values: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return self.ref.column

    @property
    def category(self) -> TypeCategory:
        return categorize_type(self.data_type)

    @property
    def is_low_cardinality(self) -> bool:
        return self.n_distinct is not None and 0 < self.n_distinct <= 50


class ForeignKey(BaseModel):
    """A declared foreign-key constraint, possibly composite."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    constraint_name: str
    source: TableRef
    source_columns: tuple[str, ...]
    target: TableRef
    target_columns: tuple[str, ...]

    def pairs(self) -> tuple[tuple[ColumnRef, ColumnRef], ...]:
        """(source column, target column) pairs, positionally matched."""
        return tuple(
            (
                ColumnRef(
                    schema_name=self.source.schema_name, table=self.source.table, column=src
                ),
                ColumnRef(
                    schema_name=self.target.schema_name, table=self.target.table, column=tgt
                ),
            )
            for src, tgt in zip(self.source_columns, self.target_columns, strict=False)
        )

    def matches(self, left: ColumnRef, right: ColumnRef) -> bool:
        """Whether this FK backs an equality between ``left`` and ``right``.

        Direction-insensitive: a join predicate may be written either way
        around.
        """
        for src, tgt in self.pairs():
            if (left == src and right == tgt) or (left == tgt and right == src):
                return True
        return False


class TableKind(StrEnum):
    TABLE = "table"
    VIEW = "view"
    MATERIALIZED_VIEW = "materialized_view"
    FOREIGN_TABLE = "foreign_table"


class DescriptionSource(StrEnum):
    """Where a table's description came from.

    Recorded per table and surfaced in the index so an operator can tell an
    authored ``COMMENT ON`` apart from a model's guess.
    """

    COMMENT = "comment"
    LLM = "llm"
    TEMPLATE = "template"


class TableInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: TableRef
    kind: TableKind = TableKind.TABLE
    comment: str | None = None
    description: str | None = None
    description_source: DescriptionSource = DescriptionSource.TEMPLATE
    columns: tuple[ColumnInfo, ...] = ()
    primary_key: tuple[str, ...] = ()
    foreign_keys: tuple[ForeignKey, ...] = ()
    approx_row_count: int | None = None

    _columns_by_name: dict[str, ColumnInfo] = PrivateAttr(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        object.__setattr__(
            self, "_columns_by_name", {c.ref.column.lower(): c for c in self.columns}
        )

    def column(self, name: str) -> ColumnInfo | None:
        return self._columns_by_name.get(_unquote(name).lower())

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.ref.column for c in self.columns)

    @property
    def is_foreign(self) -> bool:
        """Foreign tables reach the network on read; retrieval excludes them."""
        return self.kind is TableKind.FOREIGN_TABLE


class CatalogSnapshot(BaseModel):
    """Point-in-time view of the accessible schema.

    Staleness is a real failure mode: a table dropped after the snapshot was
    taken passes validation and fails at execution. :attr:`fetched_at` exists so
    that failure is diagnosable, and the execution error classifier routes a
    ``missing_object`` error to repair, which re-reads the catalog.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    fetched_at: datetime
    tables: tuple[TableInfo, ...] = ()
    search_path: tuple[str, ...] = (DEFAULT_SCHEMA,)

    _by_ref: dict[TableRef, TableInfo] = PrivateAttr(default_factory=dict)
    _by_bare_name: dict[str, list[TableInfo]] = PrivateAttr(default_factory=dict)
    _fk_adjacency: dict[TableRef, list[ForeignKey]] = PrivateAttr(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        by_ref: dict[TableRef, TableInfo] = {}
        by_bare: dict[str, list[TableInfo]] = {}
        adjacency: dict[TableRef, list[ForeignKey]] = {}

        for table in self.tables:
            by_ref[table.ref] = table
            by_bare.setdefault(table.ref.table.lower(), []).append(table)
            adjacency.setdefault(table.ref, [])

        for table in self.tables:
            for fk in table.foreign_keys:
                # Undirected: expansion must traverse a FK from either end.
                adjacency.setdefault(fk.source, []).append(fk)
                adjacency.setdefault(fk.target, []).append(fk)

        object.__setattr__(self, "_by_ref", by_ref)
        object.__setattr__(self, "_by_bare_name", by_bare)
        object.__setattr__(self, "_fk_adjacency", adjacency)

    # -- lookup ----------------------------------------------------------

    def get(self, ref: TableRef) -> TableInfo | None:
        return self._by_ref.get(ref)

    def resolve_table(
        self, name: str, search_path: tuple[str, ...] | None = None
    ) -> TableInfo | None:
        """Resolve a possibly-unqualified table name.

        An unqualified name is resolved against ``search_path`` in order, which
        is how PostgreSQL resolves it. Returning the first match rather than
        the only match matters when the same table name exists in two schemas.
        """
        parts = [_unquote(p) for p in _split_qualified(name)]
        if len(parts) >= 2:
            return self._by_ref.get(TableRef(schema_name=parts[-2], table=parts[-1]))

        bare = parts[-1].lower()
        candidates = self._by_bare_name.get(bare, [])
        if not candidates:
            return None
        for schema in search_path or self.search_path:
            for candidate in candidates:
                if candidate.ref.schema_name == schema:
                    return candidate
        return None

    def resolve_column(self, table: TableRef, name: str) -> ColumnInfo | None:
        info = self._by_ref.get(table)
        return info.column(name) if info else None

    def tables_containing_column(self, name: str) -> tuple[TableRef, ...]:
        """Every table with a column of this name -- powers 'did you mean'."""
        lowered = _unquote(name).lower()
        return tuple(t.ref for t in self.tables if t.column(lowered) is not None)

    def all_column_names(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for table in self.tables:
            for column in table.columns:
                seen.setdefault(column.ref.column, None)
        return tuple(seen)

    # -- foreign-key graph ------------------------------------------------

    def fk_edges(self, ref: TableRef) -> tuple[ForeignKey, ...]:
        """All FKs incident to ``ref``, in either direction."""
        return tuple(self._fk_adjacency.get(ref, ()))

    def neighbors(self, ref: TableRef) -> tuple[TableRef, ...]:
        out: dict[TableRef, None] = {}
        for fk in self.fk_edges(ref):
            other = fk.target if fk.source == ref else fk.source
            if other != ref and other in self._by_ref:
                out.setdefault(other, None)
        return tuple(out)

    def foreign_key_between(
        self, left: ColumnRef, right: ColumnRef
    ) -> ForeignKey | None:
        """The FK backing an equality between two columns, if one exists."""
        for ref in (left.table_ref, right.table_ref):
            for fk in self.fk_edges(ref):
                if fk.matches(left, right):
                    return fk
        return None

    @property
    def table_count(self) -> int:
        return len(self.tables)


def _split_qualified(name: str) -> list[str]:
    """Split a dotted identifier, honouring double-quoted parts."""
    parts: list[str] = []
    current: list[str] = []
    in_quotes = False
    for char in name:
        if char == '"':
            in_quotes = not in_quotes
            current.append(char)
        elif char == "." and not in_quotes:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return [p for p in parts if p]


def _unquote(identifier: str) -> str:
    """Strip double quotes; fold unquoted identifiers to lower case."""
    stripped = identifier.strip()
    if len(stripped) >= 2 and stripped.startswith('"') and stripped.endswith('"'):
        return stripped[1:-1].replace('""', '"')
    return stripped.lower()


__all__ = [
    "DEFAULT_SCHEMA",
    "CatalogSnapshot",
    "ColumnInfo",
    "ColumnRef",
    "DescriptionSource",
    "ForeignKey",
    "TableInfo",
    "TableKind",
    "TableRef",
    "TypeCategory",
    "categorize_type",
    "types_compatible",
]
