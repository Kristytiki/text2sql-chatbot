"""MetricFlow wrapper.

Translates an LLM-emitted query dict into Snowflake SQL via
`MetricFlowEngine.explain()` (no execution — pure compile path).

The actual MetricFlow imports are deferred to load time so the rest of the
service can be scaffolded / unit-tested without the metricflow package
installed yet. Once `pip install metricflow` is run, set use_metricflow=True
in `SemanticCompiler` to activate.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class CompileError(ValueError):
    """Raised when a semantic query cannot be compiled."""


@dataclass
class CompiledQuery:
    sql: str
    metric_names: list[str]
    group_by_names: list[str] = field(default_factory=list)
    where_constraints: list[str] = field(default_factory=list)


@dataclass
class SemanticCompiler:
    """Wraps MetricFlow.

    Args:
        manifest_dir: directory of semantic-manifest YAML files (semantic_models, metrics).
    """

    manifest_dir: Path
    _engine: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.manifest_dir = Path(self.manifest_dir)

    def _load_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        try:
            from metricflow.engine.metricflow_engine import MetricFlowEngine  # type: ignore
            from metricflow_semantics.model.semantic_manifest_lookup import (  # type: ignore
                SemanticManifestLookup,
            )
            from metricflow_semantic_interfaces.parsing.dir_to_model import (  # type: ignore
                parse_directory_of_yaml_files_to_semantic_manifest,
            )
        except ImportError as exc:
            raise CompileError(
                "metricflow not installed. Run: pip install -e .[metricflow]"
            ) from exc

        result = parse_directory_of_yaml_files_to_semantic_manifest(str(self.manifest_dir))
        manifest = result.semantic_manifest
        lookup = SemanticManifestLookup(manifest)
        self._engine = MetricFlowEngine(semantic_manifest_lookup=lookup, sql_client=_NullSqlClient())
        return self._engine

    def compile(
        self,
        *,
        metric_names: list[str],
        group_by_names: list[str] | None = None,
        where_constraints: list[str] | None = None,
        time_constraint_start: str | None = None,
        time_constraint_end: str | None = None,
        limit: int | None = None,
    ) -> CompiledQuery:
        from metricflow.engine.metricflow_engine import MetricFlowQueryRequest  # type: ignore

        engine = self._load_engine()
        request = MetricFlowQueryRequest.create(
            metric_names=metric_names,
            group_by_names=group_by_names or [],
            where_constraints=where_constraints or [],
            time_constraint_start=time_constraint_start,
            time_constraint_end=time_constraint_end,
            limit=limit,
        )
        try:
            explain = engine.explain(request)
        except Exception as exc:
            raise CompileError(f"MetricFlow.explain failed: {exc}") from exc

        return CompiledQuery(
            sql=explain.sql_statement.sql,
            metric_names=list(metric_names),
            group_by_names=list(group_by_names or []),
            where_constraints=list(where_constraints or []),
        )


class _NullSqlClient:
    """Satisfies MetricFlowEngine's SqlClient protocol for explain-only use."""

    def __init__(self) -> None:
        from metricflow.protocols.sql_client import SqlEngine  # type: ignore
        from metricflow.sql.render.snowflake import SnowflakeSqlPlanRenderer  # type: ignore
        self._engine_type = SqlEngine.SNOWFLAKE
        self._renderer = SnowflakeSqlPlanRenderer()

    @property
    def sql_engine_type(self) -> Any:
        return self._engine_type

    @property
    def sql_plan_renderer(self) -> Any:
        return self._renderer

    def query(self, *_, **__):
        raise NotImplementedError("explain-only client")

    def execute(self, *_, **__):
        raise NotImplementedError("explain-only client")

    def dry_run(self, *_, **__):
        raise NotImplementedError("explain-only client")

    def close(self) -> None:
        pass
