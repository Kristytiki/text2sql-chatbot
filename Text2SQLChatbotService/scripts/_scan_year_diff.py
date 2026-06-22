"""Compare 2019 vs 2020 column metadata to figure out the safe column set."""
import os
from pathlib import Path
from collections import defaultdict

from dotenv import load_dotenv
import snowflake.connector

load_dotenv(Path(__file__).resolve().parents[2] / "Text2SQLAgent" / ".env")

conn = snowflake.connector.connect(
    account=os.environ["SNOWFLAKE_ACCOUNT"],
    user=os.environ["SNOWFLAKE_USER"],
    password=os.environ["SNOWFLAKE_PASSWORD"],
    warehouse=os.getenv("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
    database="US_OPEN_CENSUS_DATA__NEIGHBORHOOD_INSIGHTS__FREE_DATASET",
    schema="PUBLIC",
    role="ACCOUNTADMIN",
)

with conn.cursor() as cur:
    out = {}
    for year in (2019, 2020):
        cur.execute(
            f'SELECT TABLE_ID, TABLE_NUMBER, TABLE_TITLE, TABLE_TOPICS, TABLE_UNIVERSE, '
            f'FIELD_LEVEL_1, FIELD_LEVEL_2, FIELD_LEVEL_3, FIELD_LEVEL_4, FIELD_LEVEL_5, '
            f'FIELD_LEVEL_6, FIELD_LEVEL_7, FIELD_LEVEL_8 '
            f'FROM "US_OPEN_CENSUS_DATA__NEIGHBORHOOD_INSIGHTS__FREE_DATASET"."PUBLIC"."{year}_METADATA_CBG_FIELD_DESCRIPTIONS"'
        )
        rows = cur.fetchall()
        out[year] = {r[0]: r for r in rows}
        print(f"{year}: {len(rows)} field rows, {len({r[1] for r in rows})} unique TABLE_NUMBERs")

    common = set(out[2019]) & set(out[2020])
    only_19 = set(out[2019]) - set(out[2020])
    only_20 = set(out[2020]) - set(out[2019])
    print(f"\ncolumn ids — common: {len(common)}, only 2019: {len(only_19)}, only 2020: {len(only_20)}")
    print(f"sample only-2019: {list(only_19)[:5]}")
    print(f"sample only-2020: {list(only_20)[:5]}")

    # B-tables exposed in CBG_<topic> tables — which TABLE_NUMBERs actually back data?
    cur.execute(
        'SHOW TABLES IN SCHEMA "US_OPEN_CENSUS_DATA__NEIGHBORHOOD_INSIGHTS__FREE_DATASET"."PUBLIC"'
    )
    cols = [c.name for c in cur.description]
    name_idx = cols.index("name")
    tbls = [r[name_idx] for r in cur.fetchall()]
    cbg_topics = sorted({t.split("_", 2)[2] for t in tbls if t.startswith(("2019_CBG_", "2020_CBG_")) and len(t.split("_", 2)) == 3})
    print(f"\nCBG topic suffixes ({len(cbg_topics)}): {cbg_topics[:20]}{'...' if len(cbg_topics) > 20 else ''}")

    # For each TABLE_NUMBER in metadata: which CBG_<topic> table holds it?
    # ACS table numbers like B01001 → topic "B01"; B23025 → "B23".
    by_topic = defaultdict(set)
    for col_id, r in out[2020].items():
        tn = r[1] or ""
        if tn:
            by_topic[tn[:3]].add(tn)
    print(f"\ntable_number prefixes covered ({len(by_topic)}):")
    for prefix, tns in sorted(by_topic.items())[:10]:
        print(f"  {prefix}: {len(tns)} ACS tables, e.g. {sorted(tns)[:5]}")

    # Sample column count per CBG topic table.
    cur.execute(f'SELECT * FROM "US_OPEN_CENSUS_DATA__NEIGHBORHOOD_INSIGHTS__FREE_DATASET"."PUBLIC"."2020_CBG_B19" LIMIT 0')
    print(f"\n2020_CBG_B19 has {len(cur.description)} columns")
    print(f"first 10: {[c.name for c in cur.description[:10]]}")

    # FIPS state codes preview
    cur.execute('SELECT DISTINCT STATE, STATE_FIPS FROM "US_OPEN_CENSUS_DATA__NEIGHBORHOOD_INSIGHTS__FREE_DATASET"."PUBLIC"."2020_METADATA_CBG_FIPS_CODES" ORDER BY STATE_FIPS LIMIT 5')
    print(f"\nFIPS state sample: {cur.fetchall()}")
conn.close()
