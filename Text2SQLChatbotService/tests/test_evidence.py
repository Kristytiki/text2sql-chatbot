"""Evidence extraction — pairs semantic_query toolUse with its toolResult.

Regression guard for the per-turn slicing fix: only SQL from the matched tool,
only from the messages passed in (not the whole history).
"""
import json

from text2sqlchatbotservice.api.routers.chat import _extract_evidence


def _tool_use(tool_use_id, name):
    return {"role": "assistant", "content": [{"toolUse": {"toolUseId": tool_use_id, "name": name}}]}


def _tool_result(tool_use_id, payload):
    return {
        "role": "user",
        "content": [{"toolResult": {"toolUseId": tool_use_id, "content": [{"text": json.dumps(payload)}]}}],
    }


SQL_PAYLOAD = {
    "sql": "SELECT 1 FROM CENSUS_VIEWS.V_TIME_SPINE LIMIT 1",
    "columns": ["x"],
    "rows": [[1]],
    "row_count": 1,
    "truncated": False,
}


def test_extracts_matched_semantic_query():
    messages = [_tool_use("u1", "semantic_query"), _tool_result("u1", SQL_PAYLOAD)]
    ev = _extract_evidence(messages)
    assert len(ev) == 1
    assert ev[0].sql == SQL_PAYLOAD["sql"]
    assert ev[0].row_count == 1


def test_ignores_non_sql_tool():
    # A toolResult whose JSON happens to lack `sql`, from a different tool.
    messages = [
        _tool_use("u1", "list_metrics"),
        _tool_result("u1", {"results": ["a", "b"]}),
    ]
    assert _extract_evidence(messages) == []


def test_ignores_result_without_matching_tool_use():
    # toolResult present but no semantic_query toolUse with that id → skipped.
    messages = [_tool_result("orphan", SQL_PAYLOAD)]
    assert _extract_evidence(messages) == []


def test_empty_messages():
    assert _extract_evidence([]) == []
