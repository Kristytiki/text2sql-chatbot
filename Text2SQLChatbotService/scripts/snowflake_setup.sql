-- One-time setup. Creates simple-named views over the marketplace tables in
-- the user's own writable schema. MetricFlow doesn't handle quoted
-- digit-prefixed table names like "2020_CBG_B01" cleanly, so we wrap.
--
-- Run this once in the Snowflake worksheet:
--   1. Open https://app.snowflake.com → Worksheet
--   2. Paste this entire file → Run all (cmd-shift-return)
--
-- Idempotent: CREATE OR REPLACE everywhere.

USE ROLE ACCOUNTADMIN;
USE WAREHOUSE COMPUTE_WH;
CREATE DATABASE IF NOT EXISTS CENSUS_DB;
USE DATABASE CENSUS_DB;
CREATE SCHEMA IF NOT EXISTS CENSUS_VIEWS;
USE SCHEMA CENSUS_VIEWS;

-- =============================================================================
-- TIME SPINE — required by MetricFlow project_configuration. We have only
-- snapshot data (2020 ACS 5-yr), so the spine is a single-row table.
-- =============================================================================
CREATE OR REPLACE VIEW V_TIME_SPINE AS
SELECT TO_TIMESTAMP('2020-01-01') AS SNAPSHOT_YEAR;

-- =============================================================================
-- POPULATION (B01001) — sex by age. We surface total + male + female totals.
-- Universe: total population.
-- =============================================================================
CREATE OR REPLACE VIEW V_CBG_POPULATION_2020 AS
SELECT
  CENSUS_BLOCK_GROUP,
  SUBSTR(CENSUS_BLOCK_GROUP, 1, 2) AS STATE_FIPS,
  SUBSTR(CENSUS_BLOCK_GROUP, 1, 5) AS COUNTY_FIPS,
  "B01001e1"  AS TOTAL_POPULATION,
  "B01001e2"  AS MALE_POPULATION,
  "B01001e26" AS FEMALE_POPULATION
FROM US_OPEN_CENSUS_DATA__NEIGHBORHOOD_INSIGHTS__FREE_DATASET.PUBLIC."2020_CBG_B01";

-- =============================================================================
-- RACE (B02001) — race counts. Universe: total population.
-- =============================================================================
CREATE OR REPLACE VIEW V_CBG_RACE_2020 AS
SELECT
  CENSUS_BLOCK_GROUP,
  SUBSTR(CENSUS_BLOCK_GROUP, 1, 2) AS STATE_FIPS,
  SUBSTR(CENSUS_BLOCK_GROUP, 1, 5) AS COUNTY_FIPS,
  "B02001e1"  AS RACE_UNIVERSE,
  "B02001e2"  AS WHITE_ALONE,
  "B02001e3"  AS BLACK_ALONE,
  "B02001e4"  AS NATIVE_ALONE,
  "B02001e5"  AS ASIAN_ALONE,
  "B02001e6"  AS PACIFIC_ALONE,
  "B02001e7"  AS OTHER_RACE_ALONE,
  "B02001e8"  AS TWO_OR_MORE_RACES
FROM US_OPEN_CENSUS_DATA__NEIGHBORHOOD_INSIGHTS__FREE_DATASET.PUBLIC."2020_CBG_B02";

-- =============================================================================
-- HISPANIC ORIGIN (B03002) — Hispanic/Latino by race. Universe: total population.
-- B03002e1 = total, e12 = Hispanic/Latino, e2 = Not Hispanic/Latino.
-- =============================================================================
CREATE OR REPLACE VIEW V_CBG_HISPANIC_2020 AS
SELECT
  CENSUS_BLOCK_GROUP,
  SUBSTR(CENSUS_BLOCK_GROUP, 1, 2) AS STATE_FIPS,
  SUBSTR(CENSUS_BLOCK_GROUP, 1, 5) AS COUNTY_FIPS,
  "B03002e1"  AS HISPANIC_UNIVERSE,
  "B03002e2"  AS NOT_HISPANIC,
  "B03002e12" AS HISPANIC_OR_LATINO
FROM US_OPEN_CENSUS_DATA__NEIGHBORHOOD_INSIGHTS__FREE_DATASET.PUBLIC."2020_CBG_B03";

-- =============================================================================
-- HOUSEHOLDS (B11001) — household type. Universe: households.
-- =============================================================================
CREATE OR REPLACE VIEW V_CBG_HOUSEHOLDS_2020 AS
SELECT
  CENSUS_BLOCK_GROUP,
  SUBSTR(CENSUS_BLOCK_GROUP, 1, 2) AS STATE_FIPS,
  SUBSTR(CENSUS_BLOCK_GROUP, 1, 5) AS COUNTY_FIPS,
  "B11001e1" AS TOTAL_HOUSEHOLDS,
  "B11001e2" AS FAMILY_HOUSEHOLDS,
  "B11001e7" AS NONFAMILY_HOUSEHOLDS
FROM US_OPEN_CENSUS_DATA__NEIGHBORHOOD_INSIGHTS__FREE_DATASET.PUBLIC."2020_CBG_B11";

-- =============================================================================
-- HOUSING UNITS (B25001 / B25002) — total + occupancy status.
-- =============================================================================
CREATE OR REPLACE VIEW V_CBG_HOUSING_2020 AS
SELECT
  CENSUS_BLOCK_GROUP,
  SUBSTR(CENSUS_BLOCK_GROUP, 1, 2) AS STATE_FIPS,
  SUBSTR(CENSUS_BLOCK_GROUP, 1, 5) AS COUNTY_FIPS,
  "B25001e1" AS HOUSING_UNITS,
  "B25002e2" AS OCCUPIED_HOUSING_UNITS,
  "B25002e3" AS VACANT_HOUSING_UNITS
FROM US_OPEN_CENSUS_DATA__NEIGHBORHOOD_INSIGHTS__FREE_DATASET.PUBLIC."2020_CBG_B25";

-- =============================================================================
-- POVERTY (B17017) — household-level poverty status. The marketplace
-- 2020_CBG_B17 table does NOT contain B17001 (individual poverty); we use
-- B17017 (poverty by household type) instead. Universe = households.
-- =============================================================================
CREATE OR REPLACE VIEW V_CBG_POVERTY_2020 AS
SELECT
  CENSUS_BLOCK_GROUP,
  SUBSTR(CENSUS_BLOCK_GROUP, 1, 2) AS STATE_FIPS,
  SUBSTR(CENSUS_BLOCK_GROUP, 1, 5) AS COUNTY_FIPS,
  "B17017e1"  AS POVERTY_UNIVERSE_HOUSEHOLDS,
  "B17017e2"  AS HOUSEHOLDS_BELOW_POVERTY,
  "B17017e31" AS HOUSEHOLDS_AT_OR_ABOVE_POVERTY
FROM US_OPEN_CENSUS_DATA__NEIGHBORHOOD_INSIGHTS__FREE_DATASET.PUBLIC."2020_CBG_B17";

-- =============================================================================
-- EMPLOYMENT (B23025) — labor force status for population 16+.
-- Universe: population 16 years and over.
-- =============================================================================
CREATE OR REPLACE VIEW V_CBG_EMPLOYMENT_2020 AS
SELECT
  CENSUS_BLOCK_GROUP,
  SUBSTR(CENSUS_BLOCK_GROUP, 1, 2) AS STATE_FIPS,
  SUBSTR(CENSUS_BLOCK_GROUP, 1, 5) AS COUNTY_FIPS,
  "B23025e1" AS POPULATION_16_PLUS,
  "B23025e2" AS LABOR_FORCE,
  "B23025e3" AS CIVILIAN_LABOR_FORCE,
  "B23025e4" AS EMPLOYED,
  "B23025e5" AS UNEMPLOYED,
  "B23025e7" AS NOT_IN_LABOR_FORCE
FROM US_OPEN_CENSUS_DATA__NEIGHBORHOOD_INSIGHTS__FREE_DATASET.PUBLIC."2020_CBG_B23";

-- =============================================================================
-- VERIFY — quick sanity counts. Should each return ~242k rows.
-- =============================================================================
SELECT 'population' AS view_name, COUNT(*) AS row_count FROM V_CBG_POPULATION_2020
UNION ALL SELECT 'race',         COUNT(*) FROM V_CBG_RACE_2020
UNION ALL SELECT 'hispanic',     COUNT(*) FROM V_CBG_HISPANIC_2020
UNION ALL SELECT 'households',   COUNT(*) FROM V_CBG_HOUSEHOLDS_2020
UNION ALL SELECT 'housing',      COUNT(*) FROM V_CBG_HOUSING_2020
UNION ALL SELECT 'poverty',      COUNT(*) FROM V_CBG_POVERTY_2020
UNION ALL SELECT 'employment',   COUNT(*) FROM V_CBG_EMPLOYMENT_2020
UNION ALL SELECT 'time_spine',   COUNT(*) FROM V_TIME_SPINE;
