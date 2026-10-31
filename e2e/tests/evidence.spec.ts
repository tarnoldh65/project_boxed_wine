import { expect, test, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import { createUser, signIn } from './identity'

const COMPOSE = fileURLToPath(new URL('../../deploy/compose.yaml', import.meta.url))

function runJob(name: string): string {
  return execFileSync('docker', ['compose', '-f', COMPOSE, 'exec', '-T', 'backend', 'python', '-m', 'app.jobs', name], {
    encoding: 'utf8',
  })
}

async function newCase(page: Page, title: string) {
  await page.getByText('New case').click()
  await page.getByLabel('Title').fill(title)
  await page.getByRole('button', { name: 'Create case' }).click()
  await expect(page.getByRole('heading', { name: title })).toBeVisible()
}

test('evidence uploads straight to storage, is hashed, verified, and held', async ({ page, request }) => {
  const admin = await createUser(request, ['casemgr-detect-respond-admin'])
  await signIn(page, admin)
  await newCase(page, `Exfiltration ${Date.now()}`)

  // Several MiB of varied bytes, uploaded by the browser through presigned URLs.
  const content = Buffer.alloc(3 * 1024 * 1024)
  for (let i = 0; i < content.length; i++) content[i] = (i * 31) % 251
  await page.locator('summary', { hasText: 'Add evidence' }).click()
  await page.getByLabel('File', { exact: true }).setInputFiles({ name: 'capture.pcap', mimeType: 'application/octet-stream', buffer: content })
  await page.getByLabel('Collected from').fill('fw-01')
  await page.getByRole('button', { name: 'Add evidence' }).click()
  await expect(page.locator('.task-head', { hasText: 'capture.pcap' })).toContainText('stored')

  expect(runJob('evidence_hash')).toContain('hashed evidence')
  await page.reload()
  const row = page.locator('.task-head', { hasText: 'capture.pcap' })
  const sha256 = createHash('sha256').update(content).digest('hex')
  await expect(row.locator('code')).toHaveAttribute('title', sha256)

  const download = page.waitForEvent('download')
  await row.getByRole('link', { name: 'Download capture.pcap' }).click()
  const file = await (await download).path()
  const { readFileSync } = await import('node:fs')
  expect(createHash('sha256').update(readFileSync(file)).digest('hex')).toBe(sha256)

  await row.getByRole('button', { name: 'Custody of capture.pcap' }).click()
  await expect(page.getByText('Chain of custody verified intact')).toBeVisible()
  await expect(page.getByRole('cell', { name: 'verified', exact: true })).toBeVisible()
  await expect(page.getByRole('cell', { name: /collected_from: fw-01/ })).toBeVisible()

  await page.getByLabel('Legal hold reason').fill('Litigation expected')
  await page.getByRole('button', { name: 'Place legal hold' }).click()
  await expect(page.getByText('Legal hold: evidence on this case cannot be deleted.')).toBeVisible()
  await expect(row.getByRole('button', { name: 'Delete capture.pcap' })).toBeDisabled()
})

test('malware samples are only downloadable with malware access', async ({ page, request }) => {
  const admin = await createUser(request, ['casemgr-detect-respond-admin'])
  await signIn(page, admin)
  await newCase(page, `Dropper ${Date.now()}`)
  const caseUrl = page.url()

  await page.locator('summary', { hasText: 'Add evidence' }).click()
  await page.getByLabel('Malware sample (stored encrypted)').check()
  await page.getByLabel('File', { exact: true }).setInputFiles({ name: 'invoice.exe', mimeType: 'application/octet-stream', buffer: Buffer.from('MZ fake sample') })
  await page.getByRole('button', { name: 'Add evidence' }).click()
  const row = page.locator('.task-head', { hasText: 'invoice.exe' })
  await expect(row).toContainText('hashed')
  await expect(row.getByRole('link', { name: 'Download invoice.exe' })).toBeHidden()

  const handler = await createUser(request, [
    'casemgr-detect-respond-analyst',
    'casemgr-detect-respond-malware:read',
  ])
  await page.getByRole('button', { name: 'Sign out' }).click()
  // Let the IdP logout finish before the next sign-in.
  await expect(page.getByRole('link', { name: 'Sign in' })).toBeVisible()
  await signIn(page, handler)
  await page.goto(caseUrl)
  const download = page.waitForEvent('download')
  await page.getByRole('link', { name: 'Download invoice.exe' }).click()
  expect((await download).suggestedFilename()).toBe('invoice.exe.zip')
})
