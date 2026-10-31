import { expect, test } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { createUser, signIn } from './identity'

const COMPOSE = fileURLToPath(new URL('../../deploy/compose.yaml', import.meta.url))
const MAILPIT = 'http://localhost:8025/api/v1'

function runJob(name: string): string {
  return execFileSync('docker', ['compose', '-f', COMPOSE, 'exec', '-T', 'backend', 'python', '-m', 'app.jobs', name], {
    encoding: 'utf8',
  })
}

test('playbook tasks gate closing a case', async ({ page, request }) => {
  const user = await createUser(request, ['casemgr-detect-respond-analyst'])
  await signIn(page, user)
  const title = `Flood ${Date.now()}`

  await page.getByText('New case').click()
  await page.getByLabel('Type').selectOption({ label: 'DDoS' })
  await page.getByLabel('Title').fill(title)
  await page.getByRole('button', { name: 'Create case' }).click()
  await expect(page.getByRole('heading', { name: title })).toBeVisible()
  await expect(page.locator('strong', { hasText: 'Engage the DDoS mitigation provider' })).toBeVisible()
  await expect(page.getByText('blocked')).toBeVisible()

  await page.getByLabel('State').selectOption({ label: 'Closed' })
  await page.getByLabel('Resolution').selectOption('true_positive')
  await page.getByRole('button', { name: 'Save case' }).click()
  await expect(page.getByRole('alert')).toContainText('finish or waive every task first')

  await page.getByLabel('Status of Engage the DDoS mitigation provider').selectOption('done')
  await expect(page.getByText('blocked')).toBeHidden()
  await page.getByRole('button', { name: 'Waive Monitor service availability until stable' }).click()
  await page.getByLabel('Reason to waive Monitor service availability until stable').fill('Provider confirmed')
  await page.getByRole('button', { name: 'Confirm waive' }).click()
  await expect(page.getByText('Waived: Provider confirmed')).toBeVisible()

  await page.getByRole('button', { name: 'Save case' }).click()
  await expect(page.getByRole('status')).toHaveText('Saved')
  await expect(page.getByRole('table', { name: 'SLA' })).toContainText('resolve met')
})

test('declaring a major incident emails the escalation contacts', async ({ page, request }) => {
  const admin = await createUser(request, ['casemgr-detect-respond-admin'])
  await signIn(page, admin)
  const run = Date.now()
  const contact = `ir-${run}@example.com`
  const rules = await page.request.put('/api/tenants/detect-respond/config/escalation-rules', {
    data: [{ name: `Major ${run}`, trigger: 'major_incident', notify_emails: [contact] }],
  })
  expect(rules.status()).toBe(200)

  const title = `Payroll outage ${run}`
  await page.getByText('New case').click()
  await page.getByLabel('Title').fill(title)
  await page.getByRole('button', { name: 'Create case' }).click()
  await expect(page.getByRole('heading', { name: title })).toBeVisible()
  const number = page.url().split('/').at(-1)

  await page.getByLabel('Major incident reason').fill('Payroll cannot run')
  await page.getByRole('button', { name: 'Declare major incident' }).click()
  await expect(page.getByText(/Major incident declared .*: Payroll cannot run/)).toBeVisible()

  expect(runJob('sla')).toContain('notifications sent')
  const search = await request.get(`${MAILPIT}/search`, { params: { query: `to:${contact}` } })
  const { messages } = await search.json()
  expect(messages).toHaveLength(1)
  expect(messages[0].Subject).toBe(`[detect-respond] Case #${number} (high): major incident`)
  const message = await (await request.get(`${MAILPIT}/message/${messages[0].ID}`)).json()
  expect(message.Text).toContain(`/t/detect-respond/cases/${number}`)
  expect(message.Text).not.toContain(title)

  // Running the job again sends nothing more.
  runJob('sla')
  const again = await request.get(`${MAILPIT}/search`, { params: { query: `to:${contact}` } })
  expect((await again.json()).messages).toHaveLength(1)
})
