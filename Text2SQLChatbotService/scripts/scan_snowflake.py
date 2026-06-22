"""Inventory the Census schema: list all tables, group by prefix, and dump
the column list + field-description metadata table (if present).

Usage:
    python scripts/scan_snowflake.py > scan.txt
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
import snowflake.connector

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
# Fallback: also load Text2SQLAgent/.env where the password lives during scaffold
load_dotenv(Path(__file__).resolve().parents[2] / "Text2SQLAgent" / ".env", override=False)

conn = snowflake.connector.connect(
    account=os.environ["SNOWFLAKE_ACCOUNT"],
    user=os.environ["SNOWFLAKE_USER"],
    password=os.environ["SNOWFLAKE_PASSWORD"],
    warehouse=os.getenv("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
    database=os.getenv("SNOWFLAKE_DATABASE"),
    schema=os.getenv("SNOWFLAKE_SCHEMA", "PUBLIC"),
    role=os.getenv("SNOWFLAKE_ROLE") or None,
)

DB = os.environ["SNOWFLAKE_DATABASE"]
SCH = os.getenv("SNOWFLAKE_SCHEMA", "PUBLIC")

with conn.cursor() as cur:
    print(f"=== {DB}.{SCH} ===")

    cur.execute(f'SHOW TABLES IN SCHEMA "{DB}"."{SCH}"')
    rows = cur.fetchall()
    cols = [c.name for c in cur.description]
    name_idx = cols.index("name")
    rowcount_idx = cols.index("rows") if "rows" in cols else None
    tables = [(r[name_idx], r[rowcount_idx] if rowcount_idx is not None else None) for r in rows]
    print(f"\n# {len(tables)} tables total\n")

    # Group by prefix (e.g. "2019_CBG_B01" -> ("2019", "CBG", "B01"))
    by_prefix: dict[tuple[str, str], list[tuple[str, int | None]]] = defaultdict(list)
    other: list[tuple[str, int | None]] = []
    for name, rc in tables:
        parts = name.split("_", 2)
        if len(parts) >= 3 and parts[0].isdigit() and parts[1] == "CBG":
            by_prefix[(parts[0], parts[2][:3])].append((name, rc))
        else:
            other.append((name, rc))

    print("# Year × topic groups (year, topic-prefix) -> count, sample table:")
    for (year, topic), grp in sorted(by_prefix.items()):
        sample = grp[0][0]
        print(f"  {year} {topic}: {len(grp)} tables, e.g. {sample} ({grp[0][1]} rows)")

    print(f"\n# Non-CBG tables ({len(other)}):")
    for n, rc in sorted(other):
        print(f"  {n} ({rc} rows)")

    # Look for a field-description / metadata table.
    metadata_candidates = [
        n for n, _ in tables
        if any(k in n.upper() for k in ("FIELD", "DESCRIP", "META", "DICT", "LOOKUP"))
    ]
    print(f"\n# Metadata-table candidates: {metadata_candidates}")
    for mt in metadata_candidates:
        print(f"\n## {mt}")
        cur.execute(f'SELECT * FROM "{DB}"."{SCH}"."{mt}" LIMIT 5')
        m_cols = [c.name for c in cur.description]
        print("  columns:", m_cols)
        for r in cur.fetchall():
            print("  ", r)

    # Sample one CBG table per topic prefix to see column families.
    print("\n# Column families per topic (first 30 columns of one sample table per topic):")
    seen_topics = set()
    for (year, topic), grp in sorted(by_prefix.items()):
        if topic in seen_topics:
            continue
        seen_topics.add(topic)
        sample = grp[0][0]
        cur.execute(f'SELECT * FROM "{DB}"."{SCH}"."{sample}" LIMIT 0')
        c = [d.name for d in cur.description]
        print(f"\n## topic {topic}  (sample {sample}, {len(c)} cols)")
        print("  ", c[:30], "..." if len(c) > 30 else "")

conn.close()
print("\nOK")
