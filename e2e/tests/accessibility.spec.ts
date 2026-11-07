import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { caseFrame, createUser, openCaseTab, popOut, queueFrame, signIn } from './identity'

const CASE_FRAMES = ['Details', 'Notes', 'Comments', 'Observables', 'Evidence', 'Assets', 'Alerts', 'Regulatory', 'Post-incident review', 'Reports']

async function serious(page: Page) {
  const { violations } = await new AxeBuilder({ page }).analyze()
  return violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).slice(0, 4).join(', ')}`)
}

for (const theme of ['Light', 'Dark']) {
  test(`no serious accessibility issues in the ${theme.toLowerCase()} theme`, async ({ page, request }) => {
    test.setTimeout(90_000)
    // A frame fading in would be measured at part opacity.
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.setViewportSize({ width: 1440, height: 900 })
    await signIn(page, await createUser(request, ['casemgr-detect-respond-admin']))
    await page.getByRole('radio', { name: theme }).check({ force: true })
    expect(await serious(page)).toEqual([])

    await queueFrame(page).getByText('New case').click()
    await queueFrame(page).getByLabel('Title').fill(`Accessibility ${Date.now()}`)
    await queueFrame(page).getByRole('button', { name: 'Create case' }).click()
    await expect(caseFrame(page)).toBeVisible()
    for (const name of CASE_FRAMES) await openCaseTab(page, name)
    await popOut(page, 'Notes')
    await page.getByRole('button', { name: 'Frames' }).click()
    await page.getByRole('checkbox', { name: 'Metrics' }).check()
    await page.getByRole('checkbox', { name: 'Search' }).check()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('region', { name: 'Search' })).toBeVisible()
    expect(await serious(page)).toEqual([])

    await caseFrame(page).getByRole('button', { name: 'Change state' }).click()
    expect(await serious(page)).toEqual([])
    await page.keyboard.press('Escape')

    await page.getByRole('link', { name: 'Settings' }).click()
    await expect(page.getByRole('heading', { name: 'API tokens' })).toBeVisible()
    expect(await serious(page)).toEqual([])
  })
}

test('the keyboard reaches frames in the order they appear', async ({ page, request }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await signIn(page, await createUser(request, ['casemgr-detect-respond-analyst']))
  const queue = page.getByRole('button', { name: 'Arrange Case queue' })
  await expect(queue).toBeVisible()
  const order = await page.locator('.frame').evaluateAll((frames) => frames.map((f) => f.id))
  expect(order).toEqual(['frame-queue', 'frame-overview', 'frame-deadlines'])

  // Keyboard moves keep focus on the menu, so they can repeat.
  await page.getByRole('button', { name: 'Arrange Notification deadlines' }).click()
  const up = page.getByRole('button', { name: 'Move up' })
  await up.focus()
  await page.keyboard.press('Enter')
  await expect(up).toBeFocused()
  await expect
    .poll(() => page.locator('.frame').evaluateAll((frames) => frames.map((f) => f.id)))
    .toEqual(['frame-queue', 'frame-deadlines', 'frame-overview'])
})
