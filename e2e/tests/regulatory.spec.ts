import { expect, test } from '@playwright/test'
import { runJob } from './backend'
import { createUser, signIn } from './identity'


test('regulatory clocks, materiality decision, and notification log', async ({ page, request }) => {
  const admin = await createUser(request, ['casemgr-detect-respond-admin'])
  await signIn(page, admin)
  await page.getByText('New case').click()
  await page.getByLabel('Type').selectOption('data_exposure')
  const title = `Customer data exposure ${Date.now()}`
  await page.getByLabel('Title').fill(title)
  await page.getByRole('button', { name: 'Create case' }).click()
  await expect(page.getByRole('heading', { name: title })).toBeVisible()
  const number = page.url().split('/').at(-1)

  await page.getByLabel('PII').check()
  await page.getByLabel('Data regions').fill('EU')
  await page.getByRole('button', { name: 'Save case' }).click()
  const clocks = page.getByRole('table', { name: 'Notification clocks' })
  const gdpr = clocks.getByRole('row', { name: /GDPR Art. 33/ })
  await expect(gdpr).toContainText('pending')

  await page.getByLabel('Decided by').fill('Disclosure committee')
  await page.getByLabel('Decision time').fill('2026-10-14T10:00')
  await page.getByLabel('Rationale').fill('Customer records of many accounts were exposed.')
  await page.getByRole('button', { name: 'Record decision' }).click()
  await expect(page.getByText(/decided by Disclosure committee/)).toBeVisible()
  await expect(clocks.getByRole('row', { name: /SEC Form 8-K/ })).toContainText('breached')

  await page.getByLabel('Recipient', { exact: true }).fill('Irish DPC')
  await page.getByLabel('Sent at').fill('2026-10-31T09:00')
  await page.getByLabel('Notification summary').fill('Initial Art. 33 report, reference DPC-1')
  await page.getByLabel('Meets clock').selectOption({ label: 'GDPR Art. 33 (supervisory authority)' })
  await page.getByRole('button', { name: 'Log notification' }).click()
  await expect(gdpr).toContainText('met')
  await expect(page.getByText('Irish DPC (regulator)')).toBeVisible()

  await page.getByRole('link', { name: 'Dashboard', exact: true }).click()
  const deadlines = page.locator('section', { has: page.getByRole('heading', { name: 'Notification deadlines' }) })
  await expect(deadlines.getByRole('row', { name: new RegExp(`#${number} SEC Form 8-K`) })).toBeVisible()
  await expect(deadlines.getByRole('row', { name: new RegExp(`#${number} GDPR`) })).toHaveCount(0)

  const output = runJob('clocks')
  expect(output).not.toContain('failed')
})
