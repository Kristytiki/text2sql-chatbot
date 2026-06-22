"""SQL validator — the last gate before Snowflake.

This is the hardest safety boundary: even a jailbroken LLM cannot get DDL/DML
past it. These tests pin that contract.
"""
import pytest

from text2sqlchatbotservice.guardrails.sql_validator import (
    SqlValidationError,
    validate_select_sql,
)

ALLOWED = dict(allowed_databases={"CENSUS_DB"}, allowed_schemas={"CENSUS_VIEWS"})


def test_plain_select_passes_and_gets_limit():
    out = validate_select_sql(
        "SELECT * FROM CENSUS_VIEWS.V_TIME_SPINE", **ALLOWED, max_limit=100
    )
    assert "LIMIT 100" in out.upper()


def test_existing_limit_under_cap_preserved():
    out = validate_select_sql(
        "SELECT * FROM CENSUS_VIEWS.V_TIME_SPINE LIMIT 5", **ALLOWED, max_limit=100
    )
    assert "LIMIT 5" in out.upper()


def test_limit_over_cap_rejected():
    with pytest.raises(SqlValidationError, match="exceeds max"):
        validate_select_sql(
            "SELECT * FROM CENSUS_VIEWS.V_TIME_SPINE LIMIT 999999",
            **ALLOWED,
            max_limit=100,
        )


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE CENSUS_VIEWS.V_TIME_SPINE",
        "DELETE FROM CENSUS_VIEWS.V_TIME_SPINE",
        "INSERT INTO CENSUS_VIEWS.V_TIME_SPINE VALUES (1)",
        "UPDATE CENSUS_VIEWS.V_TIME_SPINE SET x = 1",
        "CREATE TABLE CENSUS_VIEWS.evil (i INT)",
        "ALTER TABLE CENSUS_VIEWS.V_TIME_SPINE ADD COLUMN x INT",
        "TRUNCATE TABLE CENSUS_VIEWS.V_TIME_SPINE",
    ],
)
def test_ddl_dml_rejected(sql):
    with pytest.raises(SqlValidationError):
        validate_select_sql(sql, **ALLOWED)


def test_stacked_statements_rejected():
    # The classic injection: a benign SELECT followed by a destructive one.
    with pytest.raises(SqlValidationError, match="exactly 1 statement"):
        validate_select_sql(
            "SELECT 1 FROM CENSUS_VIEWS.V_TIME_SPINE; DROP TABLE CENSUS_VIEWS.V_TIME_SPINE",
            **ALLOWED,
        )


def test_subquery_ddl_rejected():
    # DDL hidden below the top level must still be caught (find(), not just isinstance).
    with pytest.raises(SqlValidationError):
        validate_select_sql(
            "SELECT * FROM (DELETE FROM CENSUS_VIEWS.V_TIME_SPINE RETURNING *)", **ALLOWED
        )


def test_schema_allowlist_enforced():
    with pytest.raises(SqlValidationError, match="schema not in allowlist"):
        validate_select_sql("SELECT * FROM SECRET_SCHEMA.tbl", **ALLOWED)


def test_database_allowlist_enforced():
    with pytest.raises(SqlValidationError, match="database not in allowlist"):
        validate_select_sql("SELECT * FROM OTHER_DB.CENSUS_VIEWS.tbl", **ALLOWED)


def test_bare_table_rejected_when_schema_allowlist_set():
    # No schema qualifier → can't prove it isn't INFORMATION_SCHEMA etc.
    with pytest.raises(SqlValidationError, match="must be qualified"):
        validate_select_sql("SELECT * FROM v_time_spine", **ALLOWED)


def test_empty_sql_rejected():
    with pytest.raises(SqlValidationError, match="empty"):
        validate_select_sql("   ", **ALLOWED)


def test_cte_passes():
    sql = (
        "WITH t AS (SELECT state_name FROM CENSUS_VIEWS.V_TIME_SPINE) "
        "SELECT * FROM t"
    )
    out = validate_select_sql(sql, **ALLOWED, max_limit=50)
    assert "LIMIT 50" in out.upper()
