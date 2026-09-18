"""sqlglot type-narrowing helpers.

sqlglot 30 separates its node hierarchy into abstract markers and concrete
nodes. ``Expr``, ``Condition``, ``Func`` and ``AggFunc`` are *not* subclasses of
``Expression``; every concrete node (``Select``, ``Column``, ``Sum``, ...)
multiply-inherits from ``Expression`` **and** from the marker it belongs to.

The practical consequence is that anything typed as a marker -- the ``parent``
link, the result of ``find_all(exp.AggFunc)``, ``Scope.expression`` -- does not
satisfy a signature written in terms of ``exp.Expression``, even though at
runtime it always is one, because a node cannot be instantiated from a marker
alone.

Rather than scatter ``cast`` calls (and their justification) across the
validator, the narrowing lives here once. The alternative -- writing every
internal signature against ``Expr`` -- does not work: ``Expr`` exposes neither
``args`` nor ``parent``, which the validator needs everywhere.
"""

from __future__ import annotations

from typing import cast

from sqlglot import exp


def as_expression(node: exp.Expr) -> exp.Expression:
    """Narrow a marker-typed node to ``Expression``.

    Safe by construction: the markers are abstract, so every instance reaching
    this function is a concrete node, and every concrete node inherits from
    ``Expression``.
    """
    return cast(exp.Expression, node)


def parent_of(node: exp.Expr) -> exp.Expression | None:
    """The parent node, narrowed to ``Expression``."""
    parent = node.parent
    return None if parent is None else as_expression(parent)


__all__ = ["as_expression", "parent_of"]
