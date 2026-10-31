import { expect, test } from '@playwright/test'
import { createUser, removeFromGroup, signIn } from './identity'

test('user signs in with MFA and sees only their tenants', async ({ page, request }) => {
  const user = await createUser(request, ['casemgr-detect-respond-analyst'])
  await signIn(page, user)

  await expect(page.getByRole('heading', { name: 'Detect and Respond' })).toBeVisible()
  await expect(page.getByRole('option')).toHaveText(['Detect and Respond'])
  await expect(page.getByRole('heading', { name: 'API tokens' })).toBeHidden()
})

test('tenant admin issues and revokes a scoped API token', async ({ page, request }) => {
  const user = await createUser(request, ['casemgr-detect-respond-admin'])
  await signIn(page, user)
  await page.getByRole('button', { name: 'Settings' }).click()

  await page.getByLabel('Name').fill('siem-ingest')
  await page.getByRole('button', { name: 'Create token' }).click()
  const token = await page.getByRole('status').locator('code').textContent()
  expect(token).toMatch(/^cmt_/)

  const tokens = '/api/tenants/detect-respond/tokens'
  const auth = { Authorization: `Bearer ${token}` }
  // The token authenticates but lacks tokens:manage, and is useless in another tenant.
  expect((await request.get(tokens, { headers: auth })).status()).toBe(403)
  expect(
    (await request.get('/api/tenants/insider-threat/tokens', { headers: auth })).status(),
  ).toBe(403)

  // Earlier runs leave tokens with the same name, so find this one by its prefix.
  const row = page.getByRole('row').filter({ hasText: token!.slice(0, 12) })
  await row.getByRole('button', { name: 'Revoke siem-ingest' }).click()
  await expect(row.getByRole('cell', { name: 'Revoked' })).toBeVisible()
  expect((await request.get(tokens, { headers: auth })).status()).toBe(401)
})

test('switching tenants and losing a group in the IdP', async ({ page, request }) => {
  const user = await createUser(request, [
    'casemgr-detect-respond-analyst',
    'casemgr-insider-threat-analyst',
  ])
  await signIn(page, user)

  await page.getByLabel('Tenant').selectOption({ label: 'Insider Threat' })
  await expect(page.getByRole('heading', { name: 'Insider Threat' })).toBeVisible()

  await removeFromGroup(request, user.username, 'casemgr-insider-threat-analyst')
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Detect and Respond' })).toBeVisible()
  await expect(page.getByRole('option')).toHaveText(['Detect and Respond'])
})

test('sign out ends both the app and IdP sessions', async ({ page, request }) => {
  const user = await createUser(request, ['casemgr-detect-respond-analyst'])
  await signIn(page, user)

  await page.getByRole('button', { name: 'Sign out' }).click()
  await page.getByRole('link', { name: 'Sign in' }).click()
  await expect(page.getByLabel('Username or email')).toBeVisible()
})
