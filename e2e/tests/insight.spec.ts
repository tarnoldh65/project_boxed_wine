import { expect, test, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { createUser, signIn } from './identity'

async function newCase(page: Page, title: string) {
  await page.getByText('New case').click()
  await page.getByLabel('Title').fill(title)
  await page.getByRole('button', { name: 'Create case' }).click()
  await expect(page.getByRole('heading', { name: title })).toBeVisible()
}

async function search(page: Page, tenant: string, terms: string) {
  await page.goto(`/t/${tenant}/search`)
  await page.getByLabel('Search terms').fill(terms)
  await page.getByRole('button', { name: 'Search', exact: true }).click()
}

test('restricted content never shows in search without the restricted grant', async ({ page, browser, request, baseURL }) => {
  const word = `kestrel${Date.now()}`
  const insider = await createUser(request, ['casemgr-insider-threat-ir_lead', 'casemgr-insider-threat-restricted_case'])
  await signIn(page, insider)
  await page.goto('/t/insider-threat')
  await newCase(page, `Data hoarding ${word}`)
  await page.getByLabel('New note').fill(`HR interview with the ${word} subject`)
  await page.getByRole('button', { name: 'Add note' }).click()
  await page.getByLabel('Restricted').check()
  await page.getByRole('button', { name: 'Save case' }).click()
  await expect(page.getByRole('status')).toHaveText('Saved')

  await search(page, 'insider-threat', word)
  await expect(page.getByRole('link', { name: `Data hoarding ${word}` })).toHaveCount(2)
  await page.getByLabel('Saved search name').fill('Hoarding')
  await page.getByRole('button', { name: 'Save search' }).click()
  await expect(page.getByRole('link', { name: 'Hoarding', exact: true })).toBeVisible()

  for (const [groups, tenant] of [
    [['casemgr-insider-threat-analyst'], 'insider-threat'],
    [['casemgr-detect-respond-admin', 'casemgr-detect-respond-restricted_case'], 'detect-respond'],
  ] as const) {
    const context = await browser.newContext({ baseURL, ignoreHTTPSErrors: true })
    const other = await context.newPage()
    await signIn(other, await createUser(request, [...groups]))
    await search(other, tenant, word)
    await expect(other.getByText('No matches.')).toBeVisible()
    await context.close()
  }
})

test('reports download and the dashboard shows metrics', async ({ page, request }) => {
  const admin = await createUser(request, ['casemgr-detect-respond-admin'])
  await signIn(page, admin)
  await newCase(page, `Report check ${Date.now()}`)

  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('link', { name: 'Technical report (PDF)' }).click(),
  ])
  expect(download.suggestedFilename()).toMatch(/^case-\d+-technical\.pdf$/)
  expect(readFileSync(await download.path()).subarray(0, 5).toString()).toBe('%PDF-')
  const [word] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('link', { name: 'Executive summary (DOCX)' }).click(),
  ])
  expect(word.suggestedFilename()).toMatch(/^case-\d+-executive\.docx$/)

  await page.getByRole('link', { name: 'Dashboard', exact: true }).click()
  await expect(page.getByRole('table', { name: 'Open cases by severity' })).toBeVisible()
  await expect(page.getByRole('table', { name: /Response times \(\d+ cases\)/ })).toContainText('Mean time to acknowledge')
})
