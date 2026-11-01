-- Seeds the detect-respond tenant with synthetic cases and observables for load testing,
-- plus a read-only API token for k6. Run as the schema owner:
--   psql -v cases=1000000 -v observables=100000000 -v token=<secret> -f loadtest/seed.sql
-- Each observable is linked to one case; titles and descriptions draw from a small
-- vocabulary so searches match realistic numbers of rows.
\set ON_ERROR_STOP on

BEGIN;

CREATE TEMP TABLE seed AS
SELECT t.id AS tenant_id, coalesce(max(c.number), 0) AS first
FROM tenants t LEFT JOIN cases c ON c.tenant_id = t.id
WHERE t.slug = 'detect-respond'
GROUP BY t.id;

CREATE TEMP TABLE words (n int, word text);
INSERT INTO words
SELECT row_number() OVER () - 1, w
FROM unnest(string_to_array(
  'phishing ransomware beacon exfiltration credential lateral movement payload invoice '
  'macro powershell mimikatz cobalt strike vpn firewall dns tunnel webshell persistence '
  'backdoor privilege escalation insider usb laptop mailbox forwarding wire transfer '
  'vendor ddos botnet scanner exploit vulnerability patch kerberos ticket token', ' ')) AS w;

INSERT INTO cases (tenant_id, number, title, description, state, category, impact, urgency,
                   severity, priority, source, tlp, pap, restricted, regulated_data,
                   data_regions, business_units, attack_techniques, custom, created_at,
                   updated_at, detected_at, closed_at, resolution)
SELECT s.tenant_id, s.first + i,
       initcap(w1.word) || ' ' || w2.word || ' on host ' || i,
       'Alert for ' || w1.word || ' and ' || w3.word || ' observed by the ' || w2.word || ' sensor.',
       (array['new', 'triage', 'analysis', 'containment', 'closed'])[1 + i % 5],
       (array['phishing', 'ransomware', 'bec', 'insider', 'ddos'])[1 + i % 5],
       (array['high', 'medium', 'low'])[1 + i % 3],
       (array['high', 'medium', 'low'])[1 + i / 3 % 3],
       (array['critical', 'high', 'medium', 'low'])[1 + i % 4],
       (array['P1', 'P2', 'P3', 'P4'])[1 + i % 4],
       'loadtest', 'amber', 'amber', i % 50 = 0, '{}', '{}', '{}', '{}', '{}',
       now() - make_interval(mins => i % 525600), now(), now() - make_interval(mins => i % 525600),
       CASE WHEN i % 5 = 4 THEN now() END,
       CASE WHEN i % 5 = 4 THEN 'true_positive' END
FROM seed s, generate_series(1, :cases) AS i
JOIN words w1 ON w1.n = i % 40
JOIN words w2 ON w2.n = i / 40 % 40
JOIN words w3 ON w3.n = i / 1600 % 40;

UPDATE case_numbers SET last = (SELECT first FROM seed) + :cases
WHERE tenant_id = (SELECT tenant_id FROM seed);

CREATE TEMP TABLE seeded AS
SELECT row_number() OVER (ORDER BY number) - 1 AS n, id
FROM cases WHERE source = 'loadtest' AND tenant_id = (SELECT tenant_id FROM seed);
CREATE INDEX ON seeded (n);

INSERT INTO observables (tenant_id, type, value)
SELECT (SELECT tenant_id FROM seed), 'domain', 'host' || i || '.loadtest.example'
FROM generate_series(1, :observables) AS i;

INSERT INTO case_observables (case_id, observable_id, tenant_id)
SELECT s.id, o.id, o.tenant_id
FROM (SELECT id, tenant_id, row_number() OVER () AS i FROM observables
      WHERE tenant_id = (SELECT tenant_id FROM seed) AND type = 'domain' AND value LIKE '%.loadtest.example') o
JOIN seeded s ON s.n = o.i % (SELECT count(*) FROM seeded);

INSERT INTO users (subject, username, display_name) VALUES ('loadtest', 'loadtest', 'Load test')
ON CONFLICT (subject) DO NOTHING;
INSERT INTO api_tokens (tenant_id, name, prefix, token_hash, scopes, created_by, expires_at)
SELECT (SELECT tenant_id FROM seed), 'load test', left(:'token', 8),
       encode(sha256(convert_to(:'token', 'UTF8')), 'hex'), '{cases:read}',
       (SELECT id FROM users WHERE subject = 'loadtest'), now() + interval '7 days';

COMMIT;
ANALYZE;
