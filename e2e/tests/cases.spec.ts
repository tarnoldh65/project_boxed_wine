import { expect, test, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { createUser, signIn } from './identity'

const COMPOSE = fileURLToPath(new URL('../../deploy/compose.yaml', import.meta.url))
const TENANT = '/api/tenants/detect-respond'

/** An alert in the case page's alert list (titles can also appear as the case title). */
function alertRow(page: Page, title: string) {
  return page.locator('summary', { hasText: title })
}

function compose(...args: string[]): string {
  return execFileSync('docker', ['compose', '-f', COMPOSE, ...args], { encoding: 'utf8' })
}

/** An alerts:write token, created through the API with the signed-in admin's session. */
async function intakeToken(page: Page): Promise<Record<string, string>> {
  const response = await page.request.post(`${TENANT}/tokens`, {
    data: { name: 'e2e-intake', scopes: ['alerts:write'], expires_in_days: 1 },
  })
  expect(response.status()).toBe(201)
  return { Authorization: `Bearer ${(await response.json()).token}` }
}

test('analyst opens a case from a template, edits it, and links an asset', async ({
  page,
  request,
}) => {
  const user = await createUser(request, ['casemgr-detect-respond-analyst'])
  await signIn(page, user)
  const title = `Wire fraud ${Date.now()}`

  await page.getByText('New case').click()
  await page.getByLabel('Type').selectOption({ label: 'Business email compromise' })
  await page.getByLabel('Title').fill(title)
  await page.getByRole('button', { name: 'Create case' }).click()

  await expect(page.getByRole('heading', { name: title })).toBeVisible()
  await expect(page.getByText(/P2 high \| New/)).toBeVisible()
  await page.getByLabel('State').selectOption({ label: 'Triage' })
  await page.getByLabel('Data regions').fill('EU')
  await page.getByLabel('PII').check()
  await page.getByRole('button', { name: 'Save case' }).click()
  await expect(page.getByRole('status')).toHaveText('Saved')

  await page.getByLabel('Asset name').fill('cfo-laptop')
  await page.getByRole('button', { name: 'Link asset' }).click()
  await expect(page.getByText('host: cfo-laptop')).toBeVisible()

  await page.reload()
  await expect(page.getByLabel('State')).toHaveValue('triage')
  await expect(page.getByLabel('Data regions')).toHaveValue('EU')
  await expect(page.getByLabel('PII')).toBeChecked()
})

test('API and webhook alerts open cases and group by key', async ({ page, request }) => {
  const admin = await createUser(request, ['casemgr-detect-respond-admin'])
  await signIn(page, admin)
  const headers = await intakeToken(page)
  const run = Date.now()
  const alert = (title: string) => ({
    template: 'phishing',
    title,
    source: 'mail-gateway',
    group_key: `campaign-${run}`,
    payload: { recipient: 'jdoe@example.com' },
  })

  const first = await (await request.post(`${TENANT}/alerts`, { headers, data: alert(`Lure A ${run}`) })).json()
  const second = await (await request.post(`${TENANT}/alerts`, { headers, data: alert(`Lure B ${run}`) })).json()
  expect(first.new_case).toBe(true)
  expect(second).toMatchObject({ case_number: first.case_number, new_case: false })

  const hook = await request.post(`${TENANT}/webhooks/splunk`, {
    headers,
    params: { template: 'ransomware', title_field: 'rule', id_field: 'sid' },
    data: { rule: `Encryption burst ${run}`, sid: `sid-${run}` },
  })
  expect(hook.status()).toBe(201)

  await page.goto(`/t/detect-respond/cases/${first.case_number}`)
  await expect(alertRow(page, `Lure A ${run}`)).toBeVisible()
  await expect(alertRow(page, `Lure B ${run}`)).toBeVisible()
  await page.goto('/t/detect-respond')
  await page.getByRole('link', { name: `Encryption burst ${run}` }).click()
  await expect(page.getByText('P1 critical | New | source splunk', { exact: false })).toBeVisible()
})

test('email to the intake mailbox becomes a case', async ({ page, request }) => {
  const subject = `Invoice overdue ${Date.now()}`
  const send = [
    'import smtplib',
    'from email.message import EmailMessage',
    'm = EmailMessage()',
    "m['From'] = 'billing@evil.example'",
    "m['To'] = 'intake@example.test'",
    `m['Subject'] = ${JSON.stringify(subject)}`,
    "m.set_content('Pay now')",
    "smtplib.SMTP('greenmail', 3025).send_message(m)",
  ].join('\n')
  compose('exec', '-T', 'backend', 'python', '-c', send)
  expect(compose('exec', '-T', 'backend', 'python', '-m', 'app.jobs', 'email_intake')).toContain(
    '-> case',
  )

  const user = await createUser(request, ['casemgr-detect-respond-analyst'])
  await signIn(page, user)
  await page.getByRole('link', { name: subject }).click()
  await expect(page.getByText(/source email/)).toBeVisible()
  await expect(page.getByText(/billing@evil.example/)).toBeAttached()
})

test('analyst splits an alert off and merges it into another case', async ({ page, request }) => {
  const admin = await createUser(request, ['casemgr-detect-respond-admin'])
  await signIn(page, admin)
  const headers = await intakeToken(page)
  const run = Date.now()
  const post = async (title: string, group: string) =>
    (
      await (
        await request.post(`${TENANT}/alerts`, {
          headers,
          data: { template: 'phishing', title, source: 'edr', group_key: group },
        })
      ).json()
    ).case_number as number
  const source = await post(`Alert one ${run}`, `a-${run}`)
  await post(`Alert two ${run}`, `a-${run}`)
  const target = await post(`Other alert ${run}`, `b-${run}`)

  await page.goto(`/t/detect-respond/cases/${source}`)
  await page.getByLabel(`Select alert Alert two ${run}`).check()
  await page.getByLabel('New case title').fill(`Split ${run}`)
  await page.getByRole('button', { name: 'Split selected into new case' }).click()
  await expect(page.getByRole('heading', { name: `Split ${run}` })).toBeVisible()
  await expect(alertRow(page, `Alert two ${run}`)).toBeVisible()

  await page.getByLabel('Merge into case').fill(String(target))
  await page.getByRole('button', { name: 'Merge into case' }).click()
  await expect(page.getByRole('heading', { name: `#${target} Other alert ${run}` })).toBeVisible()
  await expect(alertRow(page, `Alert two ${run}`)).toBeVisible()

  await page.goto(`/t/detect-respond/cases/${source}`)
  await expect(alertRow(page, `Alert one ${run}`)).toBeVisible()
  await expect(alertRow(page, `Alert two ${run}`)).toBeHidden()
})
