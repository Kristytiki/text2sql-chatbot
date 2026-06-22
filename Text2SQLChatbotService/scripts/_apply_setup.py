"""Apply snowflake_setup_full.sql via the connector."""
import os, re, sys
from pathlib import Path
from dotenv import load_dotenv
import snowflake.connector

ROOT = Path(__file__).resolve().parents[1]
SQL_FILE = ROOT / "scripts" / "snowflake_setup_full.sql"
load_dotenv(ROOT.parent / "Text2SQLAgent" / ".env")

# Snowflake connector executes one statement at a time. Split on `;` followed
# by newline; preserve `--` comments inside statements.
text = SQL_FILE.read_text()
# Strip pure-comment lines (start with --) so we don't choke on blank statements.
text = re.sub(r"^\s*--[^\n]*\n", "", text, flags=re.M)
statements = [s.strip() for s in re.split(r";\s*\n", text) if s.strip()]
print(f"applying {len(statements)} statements", file=sys.stderr)

conn = snowflake.connector.connect(
    account=os.environ["SNOWFLAKE_ACCOUNT"],
    user=os.environ["SNOWFLAKE_USER"],
    password=os.environ["SNOWFLAKE_PASSWORD"],
    warehouse=os.getenv("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
    role="ACCOUNTADMIN",
)
last = None
with conn.cursor() as cur:
    for i, stmt in enumerate(statements, 1):
        try:
            cur.execute(stmt)
            if stmt.upper().startswith("SELECT"):
                last = cur.fetchall()
                cols = [c.name for c in cur.description]
                print(f"\n  ↳ result of statement {i}:")
                print(f"    {cols}")
                for r in last:
                    print(f"    {r}")
            elif i % 5 == 0:
                print(f"  [{i}/{len(statements)}] ok", file=sys.stderr)
        except Exception as exc:
            print(f"FAIL on statement #{i}:\n{stmt[:200]}\n→ {exc}", file=sys.stderr)
            raise
print("done", file=sys.stderr)
conn.close()
