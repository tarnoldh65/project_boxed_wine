// Case view and search latency against a database seeded by seed.sql (NFR-02).
//   k6 run -e TOKEN=<secret> -e FIRST_CASE=<first seeded number> -e CASES=1000000 loadtest/k6.js
import http from 'k6/http'
import { check } from 'k6'

const TENANT = `${__ENV.BASE_URL || 'http://localhost:8000'}/api/tenants/detect-respond`
const PARAMS = { headers: { Authorization: `Bearer ${__ENV.TOKEN}` } }
const FIRST = Number(__ENV.FIRST_CASE || 1)
const CASES = Number(__ENV.CASES || 1000000)
const OBSERVABLES = Number(__ENV.OBSERVABLES || 10000000)
const WORDS = ['ransomware', 'kerberos', 'webshell', 'exfiltration', 'mailbox', 'botnet']

function pick(items) {
  return items[Math.floor(Math.random() * items.length)]
}

const scenario = (rate, exec) => ({
  executor: 'constant-arrival-rate',
  rate,
  timeUnit: '1s',
  duration: __ENV.DURATION || '5m',
  preAllocatedVUs: 2 * rate,
  exec,
})

export const options = {
  scenarios: { case_view: scenario(20, 'caseView'), search: scenario(5, 'search') },
  thresholds: {
    'http_req_duration{scenario:case_view}': ['p(95)<2000'],
    'http_req_duration{scenario:search}': ['p(95)<3000'],
    checks: ['rate>0.99'],
  },
}

export function caseView() {
  let i = Math.floor(Math.random() * CASES) + 1
  // Every 50th seeded case is restricted, so invisible to this token.
  if (i % 50 === 0) i -= 1
  const response = http.get(`${TENANT}/cases/${FIRST - 1 + i}`, { ...PARAMS, tags: { name: 'case' } })
  check(response, { 'case 200': (r) => r.status === 200 })
}

export function search() {
  const q = pick([
    pick(WORDS),
    `${pick(WORDS)} ${pick(WORDS)}`,
    `"host${Math.floor(Math.random() * OBSERVABLES) + 1}.loadtest.example"`,
  ])
  const response = http.get(`${TENANT}/search?q=${encodeURIComponent(q)}`, { ...PARAMS, tags: { name: 'search' } })
  check(response, { 'search 200': (r) => r.status === 200 })
}
