import { expect, test } from '@playwright/test'
import { createUser, signIn } from './identity'

const TENANT = '/api/tenants/detect-respond'

test('observables are extracted, linked, and correlated across cases', async ({ page, request }) => {
  const admin = await createUser(request, ['casemgr-detect-respond-admin'])
  await signIn(page, admin)
  const run = Date.now()
  const ip = `198.51.100.${run % 250}`
  const domain = `login-${run}.evil.example`

  const token = await page.request.post(`${TENANT}/tokens`, {
    data: { name: 'e2e-observables', scopes: ['alerts:write'], expires_in_days: 1 },
  })
  const headers = { Authorization: `Bearer ${(await token.json()).token}` }
  const alert = await request.post(`${TENANT}/alerts`, {
    headers,
    data: {
      template: 'phishing',
      title: `Credential lure ${run}`,
      source: 'mail-gateway',
      payload: { body: `Reset at hxxps://${domain.replaceAll('.', '[.]')}/reset from ${ip}` },
    },
  })
  const first = (await alert.json()).case_number

  await page.goto(`/t/detect-respond/cases/${first}`)
  await page.getByText('Add observables').click()
  await page.getByRole('button', { name: 'Extract from alerts' }).click()
  await expect(
    page.getByRole('checkbox', { name: `url hxxps://${domain.replaceAll('.', '[.]')}/reset` }),
  ).toBeChecked()
  await page.getByRole('button', { name: 'Link selected' }).click()
  const linkedIp = page.getByRole('link', { name: ip.replaceAll('.', '[.]') })
  await expect(linkedIp).toBeVisible()

  await page.goto('/t/detect-respond')
  await page.getByText('New case').click()
  await page.getByLabel('Title').fill(`Follow-up ${run}`)
  await page.getByRole('button', { name: 'Create case' }).click()
  await expect(page.getByRole('heading', { name: `Follow-up ${run}` })).toBeVisible()
  await page.getByText('Add observables').click()
  await page.getByLabel(/Paste text/).fill(`Firewall blocked ${ip.replaceAll('.', '[.]')} at 10:02`)
  await page.getByRole('button', { name: 'Extract from text' }).click()
  await page.getByRole('button', { name: 'Link selected' }).click()
  await expect(page.getByText('seen in 1 other case')).toBeVisible()

  await page.getByRole('link', { name: ip.replaceAll('.', '[.]') }).click()
  await expect(page.getByRole('heading', { name: `ipv4: ${ip.replaceAll('.', '[.]')}` })).toBeVisible()
  await expect(page.getByRole('link', { name: `Credential lure ${run}` })).toBeVisible()
  await expect(page.getByRole('link', { name: `Follow-up ${run}` })).toBeVisible()
})
