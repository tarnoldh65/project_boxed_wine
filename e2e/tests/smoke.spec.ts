import { expect, test } from '@playwright/test'

test('app offers sign in', async ({ page }) => {
  await page.goto('/')
  await expect(page).toHaveTitle('Case Manager')
  await expect(page.getByRole('link', { name: 'Sign in' })).toBeVisible()
})

test('backend is ready', async ({ request }) => {
  const response = await request.get('http://localhost:8000/readyz')
  expect(response.ok()).toBe(true)
})

test('API rejects unauthenticated requests', async ({ request }) => {
  expect((await request.get('/api/me')).status()).toBe(401)
})

test('identity provider requires MFA enrollment', async ({ page }) => {
  await page.goto('http://localhost:8180/realms/casemgr/account')
  await page.getByLabel('Username or email').fill('analyst')
  await page.getByLabel('Password', { exact: true }).fill('analyst')
  await page.getByRole('button', { name: 'Sign In' }).click()
  await expect(page.getByText('Mobile Authenticator Setup')).toBeVisible()
})
