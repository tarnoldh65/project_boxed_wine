-- Runtime role for the application. It owns no tables, so it cannot bypass row-level security.
CREATE ROLE casemgr_app LOGIN PASSWORD 'casemgr_app';
