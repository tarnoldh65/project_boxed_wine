import { expect, test } from '@playwright/test'

test('shell renders', async ({ page }) => {
  await page.goto('/')
  await expect(page).toHaveTitle('Case Manager')
  await expect(page.getByRole('heading', { name: 'Cases' })).toBeVisible()
})

test('backend is ready', async ({ request }) => {
  const response = await request.get('http://localhost:8000/readyz')
  expect(response.ok()).toBe(true)
})

test('identity provider requires MFA enrollment', async ({ page }) => {
  await page.goto('http://localhost:8180/realms/casemgr/account')
  await page.getByLabel('Username or email').fill('analyst')
  await page.getByLabel('Password', { exact: true }).fill('analyst')
  await page.getByRole('button', { name: 'Sign In' }).click()
  await expect(page.getByText('Mobile Authenticator Setup')).toBeVisible()
})
