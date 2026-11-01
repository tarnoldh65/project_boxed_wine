-- Runtime role for the application. It owns no tables, so it cannot bypass row-level security.
CREATE ROLE casemgr_app LOGIN PASSWORD 'casemgr_app';
-- Owns the search_hits function (migration 0011): it may read the searched tables past
-- row-level security, so the function can use their full-text indexes.
CREATE ROLE casemgr_search NOLOGIN BYPASSRLS;
GRANT casemgr_search TO casemgr;
