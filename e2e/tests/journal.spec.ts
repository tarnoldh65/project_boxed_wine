import { expect, test } from '@playwright/test'
import { runJob } from './backend'
import { createUser, signIn } from './identity'

const MAILPIT = 'http://localhost:8025/api/v1'

test('timeline, notes with mentions, and comments', async ({ page, browser, request, baseURL }) => {
  // The mentioned teammate must have signed in once so the app knows them.
  const teammate = await createUser(request, ['casemgr-detect-respond-analyst'])
  const other = await browser.newContext({ baseURL, ignoreHTTPSErrors: true })
  await signIn(await other.newPage(), teammate)
  await other.close()

  const user = await createUser(request, ['casemgr-detect-respond-analyst'])
  await signIn(page, user)
  const title = `Intrusion ${Date.now()}`
  await page.getByText('New case').click()
  await page.getByLabel('Title').fill(title)
  await page.getByRole('button', { name: 'Create case' }).click()
  await expect(page.getByRole('heading', { name: title })).toBeVisible()
  const number = page.url().split('/').at(-1)

  for (const [when, source, what, milestone] of [
    ['2026-10-30T12:00', 'EDR', 'Beacon observed', 'detection'],
    ['2026-10-27T09:00', 'Proxy log', 'First phish click', 'first_compromise'],
  ]) {
    await page.getByLabel('Event time').fill(when)
    await page.getByLabel('Event source').fill(source)
    await page.getByLabel('Event description').fill(what)
    await page.getByLabel('Milestone').selectOption(milestone)
    await page.getByRole('button', { name: 'Add event' }).click()
    await expect(page.getByRole('button', { name: `Remove event ${what}` })).toBeVisible()
  }
  const rows = page.locator('section', { has: page.getByRole('heading', { name: 'Timeline' }) }).getByRole('row')
  await expect(rows.nth(1)).toContainText('First phish click')
  await expect(page.getByText('3.1 days')).toBeVisible()

  const note = `## Findings\n\nAsk @${teammate.username} to check **web-01**.\n\n<img src=x onerror="document.title='pwned'">`
  await page.getByLabel('New note').fill(note)
  await page.getByRole('button', { name: 'Add note' }).click()
  await expect(page.getByRole('heading', { name: 'Findings' })).toBeVisible()
  await expect(page.locator('.markdown img')).toHaveCount(0)
  expect(await page.title()).toBe('Case Manager')

  await page.getByLabel('New comment').fill('Isolating web-01 now')
  await page.getByRole('button', { name: 'Post comment' }).click()
  await expect(page.getByText('Isolating web-01 now')).toBeVisible()

  runJob('sla')
  const search = await request.get(`${MAILPIT}/search`, {
    params: { query: `to:${teammate.username}@example.test` },
  })
  const { messages } = await search.json()
  expect(messages).toHaveLength(1)
  expect(messages[0].Subject).toBe(`[detect-respond] Case #${number} (high): you were mentioned in a note`)
})

test('legal marks a case privileged', async ({ page, request }) => {
  const admin = await createUser(request, ['casemgr-detect-respond-admin'])
  await signIn(page, admin)
  await page.getByText('New case').click()
  await page.getByLabel('Title').fill(`Counsel review ${Date.now()}`)
  await page.getByRole('button', { name: 'Create case' }).click()

  await page.getByLabel('Privilege reason').fill('Outside counsel engaged')
  await page.getByRole('button', { name: 'Mark privileged' }).click()
  await expect(
    page.getByText('Attorney-Client Privileged / Prepared at Direction of Counsel'),
  ).toBeVisible()
  await page.reload()
  await expect(page.getByRole('button', { name: 'Remove privilege marking' })).toBeVisible()
})
