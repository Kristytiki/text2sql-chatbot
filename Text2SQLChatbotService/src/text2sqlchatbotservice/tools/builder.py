"""Strands @tool factories.

Closures over SnowflakeClient + SemanticCompiler + CatalogIndex + SQL validator
so each session shares state without globals. The Agent sees only the
@tool-decorated callables.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from strands import tool

from text2sqlchatbotservice.config import Settings
from text2sqlchatbotservice.guardrails.sql_validator import (
    SqlValidationError,
    validate_select_sql,
)
from text2sqlchatbotservice.semantic import CatalogIndex, CompileError, SemanticCompiler
from text2sqlchatbotservice.snowflake import SnowflakeClient

logger = logging.getLogger(__name__)


def build_tools(
    *,
    settings: Settings,
    compiler: SemanticCompiler,
    catalog: CatalogIndex,
    sf_client: SnowflakeClient,
) -> list[Callable[..., Any]]:
    allowed_dbs = {settings.snowflake_database}
    allowed_schemas = {settings.snowflake_schema}
    max_limit = settings.max_row_limit

    @tool
    def list_metrics(query: str = "", limit: int = 20) -> str:
        """Search the metric catalog by keyword.

        Args:
            query: free-text keywords (e.g. "median household income",
                   "hispanic population", "unemployment").
            limit: max results to return (default 20, max 50).

        Returns a JSON object with a `results` array. Each result has
        `metric_name`, `description`, and `table_id` (ACS code like B19013e1).
        """
        results = catalog.search(query, limit=min(limit, 50))
        return json.dumps({
            "query": query,
            "total": len(catalog),
            "results": [
                {
                    "metric_name": r.metric_name,
                    "description": r.description,
                    "table_id": r.table_id,
                }
                for r in results
            ],
        })

    @tool
    def semantic_query(
        metrics: list[str],
        group_by: list[str] | None = None,
        where: list[str] | None = None,
        limit: int | None = None,
    ) -> str:
        """Run a metric query against the US Census semantic layer.

        Args:
            metrics: metric names from the catalog (e.g.
                ["b01003e1_estimate_total_population_total_population_total"]).
            group_by: dimension names. Common ones:
                ["census_block_group__state_name"],
                ["census_block_group__county_name"],
                ["metric_time__year"].
            where: filter clauses, MetricFlow template form, e.g.
                ["{{ Dimension('census_block_group__state_name') }} = 'California'"].
            limit: max rows; capped at the global max.

        Returns a JSON object with `sql`, `columns`, `rows`, `row_count`.
        """
        # `limit is not None` — falsy-zero would silently turn limit=0 into
        # max_limit (code-review finding #7).
        effective_limit = min(limit, max_limit) if limit is not None else max_limit
        try:
            compiled = compiler.compile(
                metric_names=metrics,
                group_by_names=group_by,
                where_constraints=where,
                limit=effective_limit,
            )
        except CompileError as exc:
            return json.dumps({"error": "compile_error", "detail": str(exc)})

        try:
            safe_sql = validate_select_sql(
                compiled.sql,
                allowed_databases=allowed_dbs,
                allowed_schemas=allowed_schemas,
                max_limit=max_limit,
            )
        except SqlValidationError as exc:
            logger.warning("rejected compiled SQL: %s", exc)
            return json.dumps({"error": "sql_validation_failed", "detail": str(exc)})

        try:
            result = sf_client.execute(safe_sql, max_rows=effective_limit)
        except Exception as exc:
            logger.exception("Snowflake execute failed")
            return json.dumps({"error": "snowflake_error", "detail": str(exc)})

        # Send only the rows we're showing the LLM; report counts that match
        # the rows shipped, not the underlying fetch size, so downstream
        # statistical claims aren't drawn from a size the LLM can't see
        # (code-review finding #4).
        SAMPLE = 200
        rows_sample = result.rows[:SAMPLE]
        return json.dumps(
            {
                "sql": safe_sql,
                "columns": result.columns,
                "rows": rows_sample,
                "rows_returned": len(rows_sample),
                "rows_total_in_result": result.row_count,
                "truncated": result.row_count > len(rows_sample),
            },
            default=str,
        )

    @tool
    def calculator(expression: str) -> str:
        """Evaluate a small arithmetic expression for ratios / per-capita.

        Whitelisted operators only: + - * / ( ) and floats.
        """
        import ast
        import operator as op

        ALLOWED = {
            ast.Add: op.add, ast.Sub: op.sub,
            ast.Mult: op.mul, ast.Div: op.truediv,
            ast.USub: op.neg, ast.UAdd: op.pos,
        }

        def _eval(node: ast.AST) -> float:
            if isinstance(node, ast.Expression):
                return _eval(node.body)
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                return float(node.value)
            if isinstance(node, ast.BinOp) and type(node.op) in ALLOWED:
                return ALLOWED[type(node.op)](_eval(node.left), _eval(node.right))
            if isinstance(node, ast.UnaryOp) and type(node.op) in ALLOWED:
                return ALLOWED[type(node.op)](_eval(node.operand))
            raise ValueError(f"disallowed expression: {ast.dump(node)}")

        try:
            tree = ast.parse(expression, mode="eval")
            value = _eval(tree)
        except Exception as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps({"result": value})

    return [list_metrics, semantic_query, calculator]
