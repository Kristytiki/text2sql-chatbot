-- Read-only role + user for the deployed Census chatbot.
--
-- Why: the app currently connects as ACCOUNTADMIN with a real password. Before
-- exposing the service publicly we drop it to a least-privilege principal that
-- can ONLY SELECT the published views — no writes, no DDL, no access to the
-- raw marketplace dataset (the views run owner's-rights, so SELECT on the view
-- is enough; the grantee never touches the underlying share).
--
-- Run as ACCOUNTADMIN (or SECURITYADMIN + SYSADMIN). Idempotent-ish: re-running
-- re-grants are harmless; CREATE USER will error if it already exists (expected).
--
-- ⚠️ Replace <SET_A_STRONG_PASSWORD> below with a freshly generated secret, e.g.
--     python -c "import secrets; print(secrets.token_urlsafe(24))"
--    Put the SAME value in the backend .env as SNOWFLAKE_PASSWORD. Do NOT commit it.

USE ROLE ACCOUNTADMIN;

-- 1. The role.
CREATE ROLE IF NOT EXISTS CENSUS_READONLY
  COMMENT = 'Read-only access to CENSUS_DB.CENSUS_VIEWS for the public chatbot';

-- 2. Compute. USAGE lets the role run queries on the warehouse; auto-resume
--    handles wake-up (no OPERATE/MODIFY granted, so it cannot resize or drop it).
GRANT USAGE ON WAREHOUSE COMPUTE_WH TO ROLE CENSUS_READONLY;

-- 3. Database + schema traversal (USAGE only — not CREATE/MODIFY).
GRANT USAGE ON DATABASE CENSUS_DB TO ROLE CENSUS_READONLY;
GRANT USAGE ON SCHEMA CENSUS_DB.CENSUS_VIEWS TO ROLE CENSUS_READONLY;

-- 4. Read the views — existing and any added later (covers both the 8-view
--    "lite" setup and the 31-view "full" setup without enumerating each one).
GRANT SELECT ON ALL VIEWS IN SCHEMA CENSUS_DB.CENSUS_VIEWS TO ROLE CENSUS_READONLY;
GRANT SELECT ON FUTURE VIEWS IN SCHEMA CENSUS_DB.CENSUS_VIEWS TO ROLE CENSUS_READONLY;

-- 5. A dedicated service user bound to the read-only role.
CREATE USER IF NOT EXISTS CENSUS_APP
  PASSWORD = '<SET_A_STRONG_PASSWORD>'
  DEFAULT_ROLE = CENSUS_READONLY
  DEFAULT_WAREHOUSE = COMPUTE_WH
  DEFAULT_NAMESPACE = CENSUS_DB.CENSUS_VIEWS
  MUST_CHANGE_PASSWORD = FALSE
  COMMENT = 'Service account for the deployed Census chatbot (read-only)';

GRANT ROLE CENSUS_READONLY TO USER CENSUS_APP;

-- 6. Verify (optional — run manually to confirm the grant set):
--   SHOW GRANTS TO ROLE CENSUS_READONLY;
--   -- then test as the new user/role:
--   USE ROLE CENSUS_READONLY; USE WAREHOUSE COMPUTE_WH;
--   SELECT COUNT(*) FROM CENSUS_DB.CENSUS_VIEWS.V_TIME_SPINE;   -- should work
--   CREATE TABLE CENSUS_DB.CENSUS_VIEWS.x(i INT);               -- should FAIL (no CREATE)
