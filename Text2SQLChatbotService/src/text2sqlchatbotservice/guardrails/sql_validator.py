"""Token-level SQL validation — sqlglot AST walk.

Defense at the data boundary: even if Bedrock Guardrails passes, even if the
LLM is jailbroken, this validator is the last gate before Snowflake sees a
query. Bedrock Guardrails operates on natural language and cannot tell
DROP TABLE from SELECT — this is what does.
"""
from __future__ import annotations

import sqlglot
from sqlglot import expressions as exp


class SqlValidationError(ValueError):
    """Raised when SQL fails the static safety check."""


_FORBIDDEN_TOP_LEVEL = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.AlterColumn,
    exp.TruncateTable,
    exp.Merge,
)


def validate_select_sql(
    sql: str,
    *,
    allowed_databases: set[str] | None = None,
    allowed_schemas: set[str] | None = None,
    max_limit: int = 10_000,
    enforce_limit: bool = True,
) -> str:
    """Parse SQL, ensure it is a single read-only SELECT against allowed objects.

    Returns the (possibly LIMIT-injected) SQL string. Raises SqlValidationError.
    """
    if not sql or not sql.strip():
        raise SqlValidationError("empty SQL")

    try:
        statements = sqlglot.parse(sql, read="snowflake")
    except Exception as exc:
        raise SqlValidationError(f"unparseable SQL: {exc}") from exc

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise SqlValidationError(f"expected exactly 1 statement, got {len(statements)}")

    stmt = statements[0]

    for forbidden in _FORBIDDEN_TOP_LEVEL:
        if isinstance(stmt, forbidden) or stmt.find(forbidden):
            raise SqlValidationError(f"DDL/DML not allowed: {forbidden.__name__}")

    if not isinstance(stmt, (exp.Select, exp.Union, exp.With)):
        raise SqlValidationError(f"top-level statement must be SELECT, got {type(stmt).__name__}")

    # Collect CTE aliases declared by any WITH at any depth. Tables with names
    # matching these aliases are CTE references, not real schema objects, and
    # must be skipped by the schema/database allowlist check.
    cte_aliases: set[str] = set()
    for cte in stmt.find_all(exp.CTE):
        alias = cte.alias
        if alias:
            cte_aliases.add(alias.upper())

    norm_dbs = {d.upper() for d in (allowed_databases or set())}
    norm_schemas = {s.upper() for s in (allowed_schemas or set())}

    for tbl in stmt.find_all(exp.Table):
        if tbl.name and tbl.name.upper() in cte_aliases and not tbl.args.get("db"):
            continue  # CTE reference — already validated where the CTE body parses.

        # sqlglot: catalog = database, db = schema. For 2-part names
        # `schema.table` only `db` is set; for 3-part `db.schema.table`
        # both are set.
        catalog = tbl.args.get("catalog")
        schema = tbl.args.get("db")
        catalog_name = catalog.name if catalog else None
        schema_name = schema.name if schema else None

        # Database allowlist: if the table is explicitly database-qualified
        # (3-part name) it must match. If it is unqualified (2-part) we cannot
        # check here — but the schema allowlist below closes the gap because
        # MetricFlow always emits 2-part names referencing schemas we own.
        if norm_dbs and catalog_name and catalog_name.upper() not in norm_dbs:
            raise SqlValidationError(f"database not in allowlist: {catalog_name}")

        # Schema allowlist: every real table reference must be qualified with
        # an allowed schema. Bare table names (no schema) are rejected —
        # without a schema we can't tell if they belong to a sensitive schema
        # the connection's default DB exposes (e.g. INFORMATION_SCHEMA).
        if norm_schemas:
            if schema_name is None:
                raise SqlValidationError(
                    f"table {tbl.sql()!r} must be qualified with schema "
                    f"(allowed: {sorted(norm_schemas)})"
                )
            if schema_name.upper() not in norm_schemas:
                raise SqlValidationError(f"schema not in allowlist: {schema_name}")

    if enforce_limit:
        # CTE / UNION top-level statements also need a LIMIT cap. We attach to
        # whichever node carries the .limit() builder for this dialect.
        target = stmt
        if isinstance(stmt, exp.With):
            inner = stmt.this
            if isinstance(inner, (exp.Select, exp.Union)):
                target = inner
        if isinstance(target, (exp.Select, exp.Union)):
            existing_limit = target.args.get("limit")
            if existing_limit is None:
                # Wrap the whole statement (including any preceding WITH) in a
                # LIMIT-bearing copy. .limit() returns a new node — preserves
                # immutability of the caller's input.
                stmt = stmt.limit(max_limit)
            else:
                try:
                    lim_val = int(existing_limit.expression.name)
                    if lim_val > max_limit:
                        raise SqlValidationError(f"LIMIT {lim_val} exceeds max {max_limit}")
                except (AttributeError, ValueError):
                    pass

    return stmt.sql(dialect="snowflake")
