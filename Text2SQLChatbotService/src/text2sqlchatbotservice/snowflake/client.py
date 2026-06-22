"""Snowflake client — small connection pool, read-only execution.

Snowflake's Python connector is NOT cursor-isolated on a shared connection —
two cursors created from the same SnowflakeConnection can interleave their
result fetches. We use a tiny per-process pool so each query has its own
connection while bounded warehouse cost (idle conns auto-suspend after 60s
on Snowflake's side anyway).
"""
from __future__ import annotations

import logging
import queue
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

import snowflake.connector
from snowflake.connector.connection import SnowflakeConnection

from text2sqlchatbotservice.config import Settings

logger = logging.getLogger(__name__)

DEFAULT_POOL_SIZE = 4


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    row_count: int = field(init=False)

    def __post_init__(self) -> None:
        self.row_count = len(self.rows)

    def to_records(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, r)) for r in self.rows]


class SnowflakeClient:
    """Thin pool. Caller is responsible for SQL validation upstream."""

    def __init__(self, settings: Settings, pool_size: int = DEFAULT_POOL_SIZE) -> None:
        self._settings = settings
        # `LifoQueue` puts the most-recently-used connection back on top so a
        # warm conn is reused before a cold one — idle Snowflake connections
        # have higher first-query latency on warehouse resume.
        self._pool: queue.LifoQueue[SnowflakeConnection] = queue.LifoQueue(maxsize=pool_size)
        self._closed = False

    def _connect(self) -> SnowflakeConnection:
        s = self._settings
        return snowflake.connector.connect(
            account=s.snowflake_account,
            user=s.snowflake_user,
            password=s.snowflake_password,
            warehouse=s.snowflake_warehouse,
            database=s.snowflake_database,
            schema=s.snowflake_schema,
            role=s.snowflake_role or None,
            client_session_keep_alive=True,
        )

    @contextmanager
    def _checkout(self) -> Iterator[SnowflakeConnection]:
        if self._closed:
            raise RuntimeError("SnowflakeClient is closed")
        try:
            conn = self._pool.get_nowait()
            if conn.is_closed():
                conn = self._connect()
        except queue.Empty:
            logger.info("opening Snowflake connection (account=%s)", self._settings.snowflake_account)
            conn = self._connect()
        try:
            yield conn
        finally:
            if self._closed or conn.is_closed():
                try:
                    conn.close()
                except Exception:  # pragma: no cover — already-closed/network teardown
                    pass
            else:
                try:
                    self._pool.put_nowait(conn)
                except queue.Full:
                    # Pool already saturated; close this extra conn.
                    try:
                        conn.close()
                    except Exception:  # pragma: no cover
                        pass

    def execute(self, sql: str, max_rows: int | None = None) -> QueryResult:
        """Execute a pre-validated SELECT and return rows.

        `max_rows` caps the number of rows fetched (defense-in-depth — the SQL
        validator should already inject a LIMIT, but we cap here too).
        """
        with self._checkout() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                cap = max_rows if max_rows is not None else self._settings.max_row_limit
                rows = cur.fetchmany(cap) if cap else cur.fetchall()
                cols = [c.name for c in cur.description] if cur.description else []
                return QueryResult(columns=cols, rows=[tuple(r) for r in rows])

    def close(self) -> None:
        self._closed = True
        while True:
            try:
                conn = self._pool.get_nowait()
            except queue.Empty:
                break
            try:
                conn.close()
            except Exception:  # pragma: no cover
                pass
