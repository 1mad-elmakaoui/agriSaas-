"""Canonical AST fingerprinting for attempt de-duplication.

The brief requires the repair node to "not re-emit a previously attempted query
(hash and compare)". Hashing the SQL *string* does not achieve that: reindenting,
recasing a keyword, or renaming an output alias produces a different hash for
the same query -- and those are precisely the changes a model makes when told to
"try something different" without understanding the error.

Canonicalisation therefore:

1. parses to an AST, discarding all formatting and comments;
2. normalises unquoted identifiers to lower case, matching PostgreSQL's own
   folding (quoted identifiers keep their case, because there they are
   significant);
3. strips output aliases, so ``SELECT sum(x) AS total`` and ``SELECT sum(x) AS
   revenue`` collapse together -- renaming a label does not address a failing
   query.

Known limitation, stated rather than hidden: renaming a *table* alias
consistently throughout (``o`` to ``ord``) changes every column qualifier and
therefore the fingerprint, so it reads as a new attempt. Fully defeating that
needs column qualification against the schema, which would make the fingerprint
depend on catalog state. The duplicate-strike policy and the attempt cap bound
the cost of that gap to one wasted attempt.
"""

from __future__ import annotations

import hashlib
from contextlib import suppress

from sqlglot import exp
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers

DIALECT = "postgres"


def canonical_sql(expression: exp.Expression) -> str:
    """Formatting-independent rendering used as the hash input."""
    copy = expression.copy()

    # Identifier normalisation is an optimisation of the canonical form, not a
    # requirement of it: if sqlglot cannot fold identifiers for this tree, the
    # un-normalised rendering is still a valid fingerprint input. It is only
    # slightly less aggressive at collapsing near-duplicates.
    with suppress(Exception):
        copy = normalize_identifiers(copy, dialect=DIALECT)

    for alias in list(copy.find_all(exp.Alias)):
        parent = alias.parent
        if parent is None:
            continue
        # Only strip *output* aliases (select-list labels). A table alias is
        # load-bearing -- removing it would break the column qualifiers that
        # reference it.
        if isinstance(
            alias.this, exp.Column | exp.Func | exp.Binary | exp.Paren | exp.Case
        ) and (
            isinstance(parent, exp.Select)
            or (parent.args.get("expressions") and alias in parent.args["expressions"])
        ):
            alias.replace(alias.this)

    return copy.sql(dialect=DIALECT, comments=False, normalize=True)


def fingerprint(expression: exp.Expression) -> str:
    """SHA-256 of the canonical form. Stable across runs and processes."""
    return hashlib.sha256(canonical_sql(expression).encode("utf-8")).hexdigest()


__all__ = ["canonical_sql", "fingerprint"]
