"""Generate the full census semantic catalog from Snowflake metadata.

Outputs:
  1. snowflake_setup_full.sql  — drops/recreates CENSUS_DB.CENSUS_VIEWS:
       * V_CBG_GEOGRAPHY      (cbg → state name/fips, county name/fips, lat/lon)
       * V_CBG_<topic>        for each of the ~29 census topics, year-unioned
       * V_TIME_SPINE
  2. catalog/project_configuration.yaml
  3. catalog/semantic_models/<topic>.yaml   per topic
  4. catalog/semantic_models/_geography.yaml
  5. catalog/metrics.yaml                    (one simple metric per measure)

Wide-format aware: each ACS column becomes its own measure (e.g.
"b02001e2__white_alone"). Group-by dimensions are only year/state/county.

Skips rows whose FIELD_LEVEL chain mentions "Median", "Mean", "Aggregate",
or "Average" — those are non-additive and would silently return wrong
results if SUM-aggregated across CBGs. The metric inventory tool can still
list them by code.
"""
from __future__ import annotations

import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import yaml
from dotenv import load_dotenv
import snowflake.connector

ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = ROOT / "src" / "text2sqlchatbotservice" / "semantic" / "catalog"
MODELS_DIR = CATALOG_DIR / "semantic_models"
SQL_OUT = ROOT / "scripts" / "snowflake_setup_full.sql"
SHARE_DB = "US_OPEN_CENSUS_DATA__NEIGHBORHOOD_INSIGHTS__FREE_DATASET"
SHARE_SCHEMA = "PUBLIC"
TARGET_DB = "CENSUS_DB"
TARGET_SCHEMA = "CENSUS_VIEWS"
YEARS = (2019, 2020)

NON_ADDITIVE_KEYWORDS = ("median", "mean", "aggregate", "average")
NON_ADDITIVE_RE = re.compile(r"\b(" + "|".join(NON_ADDITIVE_KEYWORDS) + r")\b", re.I)


def _safe_text(s: str) -> str:
    """Strip characters that MetricFlow's string.Template parser chokes on."""
    return s.replace("$", "USD ")


def _slug(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")[:60]


def _connect():
    load_dotenv(ROOT.parent / "Text2SQLAgent" / ".env")
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ["SNOWFLAKE_PASSWORD"],
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
        database=SHARE_DB,
        schema=SHARE_SCHEMA,
        role="ACCOUNTADMIN",
    )


def _scan(cur):
    """Return:
        topics: ordered list of CBG topic suffixes covered by metadata.
        cbg_columns: {topic: ordered [col_name]} actually present in the
                     2019 AND 2020 CBG tables (intersection only).
        meta_by_col: {col_id: {table_id, table_title, table_topics, table_universe,
                               levels: [...], non_additive: bool}}
    """
    # 1. Find all CBG topic suffixes that exist for both years.
    cur.execute(f'SHOW TABLES IN SCHEMA "{SHARE_DB}"."{SHARE_SCHEMA}"')
    cols = [c.name for c in cur.description]
    name_idx = cols.index("name")
    all_tbls = [r[name_idx] for r in cur.fetchall()]
    by_year: dict[int, set[str]] = {y: set() for y in YEARS}
    for t in all_tbls:
        parts = t.split("_", 2)
        if len(parts) == 3 and parts[1] == "CBG" and parts[0].isdigit():
            yr = int(parts[0])
            if yr in by_year:
                by_year[yr].add(parts[2])
    common_topics = sorted(set.intersection(*by_year.values()))
    # Keep only ACS B/C tables — drop GEO + PAT (SafeGraph extras, not census counts).
    topics = [t for t in common_topics if t.startswith(("B", "C"))]

    # 2. For each topic, intersect column sets across both years.
    cbg_columns: dict[str, list[str]] = {}
    for topic in topics:
        per_year: list[set[str]] = []
        ordered: list[str] = []
        for yr in YEARS:
            cur.execute(f'SELECT * FROM "{SHARE_DB}"."{SHARE_SCHEMA}"."{yr}_CBG_{topic}" LIMIT 0')
            yr_cols = [c.name for c in cur.description]
            per_year.append(set(yr_cols))
            if yr == YEARS[-1]:
                ordered = yr_cols
        common_cols = set.intersection(*per_year)
        cbg_columns[topic] = [c for c in ordered if c in common_cols]

    # 3. Pull field metadata (use 2020; 2019 is a strict subset).
    cur.execute(
        f'SELECT TABLE_ID, TABLE_NUMBER, TABLE_TITLE, TABLE_TOPICS, TABLE_UNIVERSE, '
        f'FIELD_LEVEL_1, FIELD_LEVEL_2, FIELD_LEVEL_3, FIELD_LEVEL_4, FIELD_LEVEL_5, '
        f'FIELD_LEVEL_6, FIELD_LEVEL_7, FIELD_LEVEL_8 '
        f'FROM "{SHARE_DB}"."{SHARE_SCHEMA}"."2020_METADATA_CBG_FIELD_DESCRIPTIONS"'
    )
    meta_by_col: dict[str, dict] = {}
    for row in cur.fetchall():
        col_id, table_number, title, topics_, universe, *levels = row
        levels = [lv for lv in levels if lv]
        full_label = " — ".join(levels) if levels else (title or col_id)
        non_additive = bool(NON_ADDITIVE_RE.search(full_label) or NON_ADDITIVE_RE.search(universe or ""))
        meta_by_col[col_id] = {
            "table_id": col_id,
            "table_number": table_number,
            "title": title,
            "topics": topics_,
            "universe": universe,
            "levels": levels,
            "full_label": full_label,
            "non_additive": non_additive,
        }
    return topics, cbg_columns, meta_by_col


def _emit_sql(topics: list[str], cbg_columns: dict[str, list[str]]) -> str:
    """Generate the full Snowflake setup SQL."""
    lines: list[str] = [
        "-- AUTO-GENERATED by scripts/generate_catalog.py — do NOT edit by hand.",
        "-- Wraps the SafeGraph US Open Census marketplace tables into:",
        "--   * V_CBG_<topic> per topic (UNION ALL of 2019 + 2020, intersected cols)",
        "--   * V_CBG_GEOGRAPHY (block-group → state/county names + lat/lon)",
        "--   * V_TIME_SPINE",
        "-- Why: marketplace tables have digit-prefixed names ('2020_CBG_B01') and",
        "-- mixed-case quoted columns ('B01001e1') that MetricFlow can't quote on",
        "-- its own. The views give us friendly identifiers + UNION ALL across years.",
        "",
        "USE ROLE ACCOUNTADMIN;",
        "USE WAREHOUSE COMPUTE_WH;",
        "CREATE DATABASE IF NOT EXISTS CENSUS_DB;",
        "USE DATABASE CENSUS_DB;",
        "CREATE SCHEMA IF NOT EXISTS CENSUS_VIEWS;",
        "USE SCHEMA CENSUS_VIEWS;",
        "",
        "-- =============================================================================",
        "-- TIME SPINE — required by MetricFlow project_configuration. Two snapshots.",
        "-- =============================================================================",
        "CREATE OR REPLACE VIEW V_TIME_SPINE AS",
        "SELECT TO_TIMESTAMP('2019-01-01') AS SNAPSHOT_YEAR",
        "UNION ALL SELECT TO_TIMESTAMP('2020-01-01');",
        "",
        "-- =============================================================================",
        "-- GEOGRAPHY — joinable hub: cbg → state/county FIPS + names + lat/lon.",
        "-- =============================================================================",
        "CREATE OR REPLACE VIEW V_CBG_GEOGRAPHY AS",
        "WITH fips AS (",
        f'  SELECT STATE_FIPS, COUNTY_FIPS, STATE AS STATE_CODE, COUNTY AS COUNTY_NAME',
        f'  FROM "{SHARE_DB}"."{SHARE_SCHEMA}"."2020_METADATA_CBG_FIPS_CODES"',
        "), geo AS (",
        f'  SELECT CENSUS_BLOCK_GROUP, LATITUDE, LONGITUDE, AMOUNT_LAND, AMOUNT_WATER',
        f'  FROM "{SHARE_DB}"."{SHARE_SCHEMA}"."2020_METADATA_CBG_GEOGRAPHIC_DATA"',
        "), state_names AS (",
        "  SELECT STATE_FIPS, STATE_CODE, MAX(",
        "    CASE STATE_CODE",
        "      WHEN 'AL' THEN 'Alabama' WHEN 'AK' THEN 'Alaska' WHEN 'AZ' THEN 'Arizona'",
        "      WHEN 'AR' THEN 'Arkansas' WHEN 'CA' THEN 'California' WHEN 'CO' THEN 'Colorado'",
        "      WHEN 'CT' THEN 'Connecticut' WHEN 'DE' THEN 'Delaware' WHEN 'DC' THEN 'District of Columbia'",
        "      WHEN 'FL' THEN 'Florida' WHEN 'GA' THEN 'Georgia' WHEN 'HI' THEN 'Hawaii'",
        "      WHEN 'ID' THEN 'Idaho' WHEN 'IL' THEN 'Illinois' WHEN 'IN' THEN 'Indiana'",
        "      WHEN 'IA' THEN 'Iowa' WHEN 'KS' THEN 'Kansas' WHEN 'KY' THEN 'Kentucky'",
        "      WHEN 'LA' THEN 'Louisiana' WHEN 'ME' THEN 'Maine' WHEN 'MD' THEN 'Maryland'",
        "      WHEN 'MA' THEN 'Massachusetts' WHEN 'MI' THEN 'Michigan' WHEN 'MN' THEN 'Minnesota'",
        "      WHEN 'MS' THEN 'Mississippi' WHEN 'MO' THEN 'Missouri' WHEN 'MT' THEN 'Montana'",
        "      WHEN 'NE' THEN 'Nebraska' WHEN 'NV' THEN 'Nevada' WHEN 'NH' THEN 'New Hampshire'",
        "      WHEN 'NJ' THEN 'New Jersey' WHEN 'NM' THEN 'New Mexico' WHEN 'NY' THEN 'New York'",
        "      WHEN 'NC' THEN 'North Carolina' WHEN 'ND' THEN 'North Dakota' WHEN 'OH' THEN 'Ohio'",
        "      WHEN 'OK' THEN 'Oklahoma' WHEN 'OR' THEN 'Oregon' WHEN 'PA' THEN 'Pennsylvania'",
        "      WHEN 'RI' THEN 'Rhode Island' WHEN 'SC' THEN 'South Carolina' WHEN 'SD' THEN 'South Dakota'",
        "      WHEN 'TN' THEN 'Tennessee' WHEN 'TX' THEN 'Texas' WHEN 'UT' THEN 'Utah'",
        "      WHEN 'VT' THEN 'Vermont' WHEN 'VA' THEN 'Virginia' WHEN 'WA' THEN 'Washington'",
        "      WHEN 'WV' THEN 'West Virginia' WHEN 'WI' THEN 'Wisconsin' WHEN 'WY' THEN 'Wyoming'",
        "      WHEN 'PR' THEN 'Puerto Rico'",
        "      ELSE STATE_CODE END",
        "  ) AS STATE_NAME",
        "  FROM fips GROUP BY STATE_FIPS, STATE_CODE",
        ")",
        "SELECT",
        "  geo.CENSUS_BLOCK_GROUP,",
        "  SUBSTR(geo.CENSUS_BLOCK_GROUP, 1, 2) AS STATE_FIPS,",
        "  SUBSTR(geo.CENSUS_BLOCK_GROUP, 1, 5) AS COUNTY_FIPS,",
        "  s.STATE_CODE,",
        "  s.STATE_NAME,",
        "  fips.COUNTY_NAME,",
        "  geo.LATITUDE, geo.LONGITUDE, geo.AMOUNT_LAND, geo.AMOUNT_WATER",
        "FROM geo",
        "LEFT JOIN fips",
        "  ON SUBSTR(geo.CENSUS_BLOCK_GROUP, 1, 2) = fips.STATE_FIPS",
        " AND SUBSTR(geo.CENSUS_BLOCK_GROUP, 3, 3) = fips.COUNTY_FIPS",
        "LEFT JOIN state_names s",
        "  ON SUBSTR(geo.CENSUS_BLOCK_GROUP, 1, 2) = s.STATE_FIPS;",
        "",
    ]
    for topic in topics:
        cols = cbg_columns.get(topic, [])
        if not cols or "CENSUS_BLOCK_GROUP" not in cols:
            continue
        data_cols = [c for c in cols if c != "CENSUS_BLOCK_GROUP"]
        select_cols = ",\n".join(f'    "{c}" AS "{c}"' for c in data_cols)
        lines += [
            "-- =============================================================================",
            f"-- TOPIC {topic} — {len(data_cols)} columns × 2 years.",
            "-- =============================================================================",
            f"CREATE OR REPLACE VIEW V_CBG_{topic} AS",
        ]
        union_parts = []
        for yr in YEARS:
            union_parts.append(
                f"SELECT {yr} AS YEAR,\n"
                f"    CENSUS_BLOCK_GROUP,\n"
                f"{select_cols}\n"
                f'  FROM "{SHARE_DB}"."{SHARE_SCHEMA}"."{yr}_CBG_{topic}"'
            )
        lines.append("  " + "\n  UNION ALL\n  ".join(union_parts) + ";")
        lines.append("")

    lines += [
        "-- =============================================================================",
        "-- VERIFY — quick row-count sanity for each generated view.",
        "-- =============================================================================",
        "SELECT 'V_TIME_SPINE' AS view_name, COUNT(*) AS row_count FROM V_TIME_SPINE",
        "UNION ALL SELECT 'V_CBG_GEOGRAPHY', COUNT(*) FROM V_CBG_GEOGRAPHY",
    ]
    for topic in topics:
        if cbg_columns.get(topic):
            lines.append(f"UNION ALL SELECT 'V_CBG_{topic}', COUNT(*) FROM V_CBG_{topic}")
    lines.append("ORDER BY view_name;")
    return "\n".join(lines) + "\n"


def _emit_geography_yaml() -> dict:
    return {
        "semantic_model": {
            "name": "geography",
            "description": "Geographic dimensions for every census block group.",
            "node_relation": {"schema_name": TARGET_SCHEMA, "alias": "V_CBG_GEOGRAPHY"},
            "defaults": {"agg_time_dimension": "snapshot_year"},
            "entities": [
                {"name": "census_block_group", "type": "primary", "expr": "CENSUS_BLOCK_GROUP"},
            ],
            "dimensions": [
                {"name": "state_code",  "type": "categorical", "expr": "STATE_CODE"},
                {"name": "state_name",  "type": "categorical", "expr": "STATE_NAME"},
                {"name": "state_fips",  "type": "categorical", "expr": "STATE_FIPS"},
                {"name": "county_name", "type": "categorical", "expr": "COUNTY_NAME"},
                {"name": "county_fips", "type": "categorical", "expr": "COUNTY_FIPS"},
                # Single-value time dim — geography is timeless. Required so MetricFlow
                # can join geography in queries that include the year time dimension.
                {
                    "name": "snapshot_year",
                    "type": "time",
                    "expr": "TO_TIMESTAMP('2020-01-01')",
                    "type_params": {"time_granularity": "year"},
                },
            ],
            "measures": [
                # A single dummy SUM(0) measure makes this a valid semantic_model
                # that can be referenced as a dimension source. Never used directly.
                {"name": "geography_universe", "agg": "sum", "expr": "0"},
            ],
        }
    }


def _emit_topic_yaml(topic: str, cols: list[str], meta_by_col: dict) -> dict:
    measures = []
    for c in cols:
        if c == "CENSUS_BLOCK_GROUP":
            continue
        meta = meta_by_col.get(c)
        if not meta:
            continue
        if meta["non_additive"]:
            continue  # SUM-aggregating a median/mean across CBGs is wrong; skip.
        # Margin-of-error columns ('m' suffix) — keep them but tag the description
        # so search/UI can deprioritize. ACS provides a margin per estimate; the
        # margins are real source data and must be queryable per the spec.
        is_margin = bool(re.search(r"m\d+$", c))
        slug = _slug(meta["full_label"]) or _slug(c)
        # MetricFlow rejects double underscores and trailing underscores in
        # measure names. Use single `_` separator and trim.
        measure_name = re.sub(r"_+", "_", f"{c.lower()}_{slug}")[:64].rstrip("_")
        descr = _safe_text(meta["full_label"])[:200]
        if is_margin:
            descr = "[MARGIN OF ERROR] " + descr
        measures.append({
            "name": measure_name,
            "description": descr,
            "agg": "sum",
            "expr": f'"{c}"',
        })
    if not measures:
        return {}
    return {
        "semantic_model": {
            "name": f"cbg_{topic.lower()}",
            "description": f"Census ACS topic {topic} — auto-generated from field metadata.",
            "node_relation": {"schema_name": TARGET_SCHEMA, "alias": f"V_CBG_{topic}"},
            "defaults": {"agg_time_dimension": "snapshot_year"},
            "entities": [
                {"name": "census_block_group", "type": "primary", "expr": "CENSUS_BLOCK_GROUP"},
            ],
            "dimensions": [
                {
                    "name": "snapshot_year",
                    "type": "time",
                    "expr": "TO_TIMESTAMP(YEAR || '-01-01')",
                    "type_params": {"time_granularity": "year"},
                },
            ],
            "measures": measures,
        }
    }


def _emit_metrics(model_yamls: Iterable[dict]) -> list[dict]:
    out = []
    seen = set()
    for m in model_yamls:
        sm = m.get("semantic_model") if m else None
        if not sm:
            continue
        for measure in sm.get("measures", []):
            mname = measure["name"]
            if mname in seen or mname == "geography_universe":
                continue
            seen.add(mname)
            out.append({
                "metric": {
                    "name": mname,
                    "description": measure.get("description", mname),
                    "type": "simple",
                    "type_params": {"measure": {"name": mname}},
                }
            })
    return out


def main() -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            print("scanning Snowflake metadata…", file=sys.stderr)
            topics, cbg_columns, meta_by_col = _scan(cur)
    finally:
        conn.close()

    sql = _emit_sql(topics, cbg_columns)
    SQL_OUT.write_text(sql)
    print(f"wrote {SQL_OUT}  ({len(sql)} bytes)", file=sys.stderr)

    # Wipe + rewrite catalog dir.
    if MODELS_DIR.exists():
        for p in MODELS_DIR.iterdir():
            p.unlink()
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    project_cfg = {
        "project_configuration": {
            "time_spine_table_configurations": [
                {"location": f"{TARGET_DB}.{TARGET_SCHEMA}.V_TIME_SPINE",
                 "column_name": "SNAPSHOT_YEAR",
                 "grain": "year"}
            ]
        }
    }
    (CATALOG_DIR / "project_configuration.yaml").write_text(yaml.safe_dump(project_cfg, sort_keys=False))

    geo = _emit_geography_yaml()
    (MODELS_DIR / "_geography.yaml").write_text(yaml.safe_dump(geo, sort_keys=False))

    all_models = [geo]
    measure_count = 0
    skipped_topics = []
    for topic in topics:
        cols = cbg_columns.get(topic, [])
        if not cols:
            skipped_topics.append(topic)
            continue
        ymodel = _emit_topic_yaml(topic, cols, meta_by_col)
        if not ymodel:
            skipped_topics.append(topic)
            continue
        all_models.append(ymodel)
        measure_count += len(ymodel["semantic_model"]["measures"])
        (MODELS_DIR / f"{topic.lower()}.yaml").write_text(yaml.safe_dump(ymodel, sort_keys=False))

    metrics = _emit_metrics(all_models)
    metrics_yaml = "\n---\n".join(yaml.safe_dump(m, sort_keys=False) for m in metrics)
    (CATALOG_DIR / "metrics.yaml").write_text("---\n" + metrics_yaml)

    print(f"emitted {len(all_models)-1} topic semantic_models + 1 geography model", file=sys.stderr)
    print(f"  measures: {measure_count}", file=sys.stderr)
    print(f"  metrics : {len(metrics)}", file=sys.stderr)
    if skipped_topics:
        print(f"  skipped topics (no usable columns): {skipped_topics}", file=sys.stderr)


if __name__ == "__main__":
    main()
