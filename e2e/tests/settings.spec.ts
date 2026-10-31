import { expect, test } from '@playwright/test'
import { createUser, signIn } from './identity'

test('tenant admin edits lifecycle states and the change persists', async ({ page, request }) => {
  // insider-threat keeps the change away from the tenant other tests use.
  const user = await createUser(request, ['casemgr-insider-threat-admin'])
  await signIn(page, user)
  await page.getByRole('link', { name: 'Settings' }).click()

  const states = page.locator('form', { has: page.getByRole('heading', { name: 'Lifecycle states' }) })
  const name = `Triage ${Date.now()}`
  await states.getByLabel('State 2 name').fill(name)
  await states.getByRole('button', { name: 'Save' }).click()
  await expect(states.getByRole('status')).toHaveText('Saved')

  // An invalid change is rejected with the server's explanation.
  await states.getByLabel('State 8 terminal').uncheck()
  await states.getByRole('button', { name: 'Save' }).click()
  await expect(states.getByRole('alert')).toContainText('at least one state must be terminal')

  await page.reload()
  await page.getByRole('link', { name: 'Settings' }).click()
  await expect(page.getByLabel('State 2 name')).toHaveValue(name)
  await expect(page.getByLabel('State 8 terminal')).toBeChecked()
})

test('analysts do not see settings', async ({ page, request }) => {
  const user = await createUser(request, ['casemgr-insider-threat-analyst'])
  await signIn(page, user)
  await expect(page.getByRole('heading', { name: 'Cases' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Settings' })).toBeHidden()
})
