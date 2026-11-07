import { expect, test, type Locator, type Page } from '@playwright/test'
import { createUser, popOut, queueFrame, signIn } from './identity'

async function box(locator: Locator) {
  const found = await locator.boundingBox()
  return found!
}

async function drag(page: Page, handle: Locator, dx: number, dy: number) {
  const { x, y, width, height } = await box(handle)
  const [startX, startY] = [x + width / 2, y + height / 2]
  await page.mouse.move(startX, startY)
  await page.mouse.down()
  await page.mouse.move(startX + dx / 2, startY + dy / 2, { steps: 5 })
  await page.mouse.move(startX + dx, startY + dy, { steps: 5 })
  await page.mouse.up()
}

/** A frame's grid item once its move animation has finished. */
async function settledBox(frame: Locator) {
  let last: Awaited<ReturnType<typeof box>> | null = null
  await expect(async () => {
    const previous = last
    last = await box(frame)
    expect(last).toEqual(previous)
  }).toPass({ intervals: [300] })
  return last!
}

test('frames drag and resize with the mouse, and come back after a reload', async ({ page, request }) => {
  const violations: string[] = []
  page.on('console', (message) => {
    if (message.text().includes('Content Security Policy')) violations.push(message.text())
  })
  await page.setViewportSize({ width: 1400, height: 1000 })
  await signIn(page, await createUser(request, ['casemgr-detect-respond-analyst']))
  const overview = page.locator('#frame-overview')
  const deadlines = page.locator('#frame-deadlines')
  await expect(overview).toBeVisible()
  const before = await settledBox(overview)

  // Dragging Overview below Notification deadlines swaps the two.
  const deadlinesBefore = await settledBox(deadlines)
  await drag(page, overview.locator('.frame-handle'), 0, deadlinesBefore.y - before.y + deadlinesBefore.height)
  const moved = await settledBox(overview)
  expect(moved.y).toBeGreaterThan(before.y)
  expect((await settledBox(deadlines)).y).toBeLessThan(moved.y)

  const item = page.locator('.react-grid-item', { has: deadlines })
  const sized = await settledBox(deadlines)
  await drag(page, item.locator('.react-resizable-handle-se'), -100, 96)
  const resized = await settledBox(deadlines)
  expect(resized.height).toBeGreaterThan(sized.height)
  expect(resized.width).toBeLessThan(sized.width)

  await page.reload()
  await expect(overview).toBeVisible()
  expect((await settledBox(deadlines)).height).toBe(resized.height)
  expect((await settledBox(deadlines)).width).toBe(resized.width)
  expect((await settledBox(overview)).y).toBeGreaterThan(before.y)
  expect(violations).toEqual([])
})

test('the arrange menu moves a frame without a mouse', async ({ page, request }) => {
  await page.setViewportSize({ width: 1400, height: 1000 })
  await signIn(page, await createUser(request, ['casemgr-detect-respond-analyst']))
  const deadlines = page.locator('#frame-deadlines')
  const before = await settledBox(deadlines)

  await page.getByRole('button', { name: 'Arrange Notification deadlines' }).focus()
  await page.keyboard.press('Enter')
  await page.getByRole('button', { name: 'Move up' }).focus()
  await page.keyboard.press('Enter')
  await page.keyboard.press('Escape')
  expect((await settledBox(deadlines)).y).toBeLessThan(before.y)
  await expect(page.getByRole('button', { name: 'Arrange Notification deadlines' })).toBeFocused()
})

test('frames follow the window, and stack without sideways scrolling on a phone', async ({ page, request }) => {
  await page.setViewportSize({ width: 1920, height: 1000 })
  await signIn(page, await createUser(request, ['casemgr-detect-respond-analyst']))
  const queue = queueFrame(page)
  const wide = await settledBox(queue)

  await page.setViewportSize({ width: 1280, height: 800 })
  await expect.poll(async () => (await box(queue)).width).toBeLessThan(wide.width)

  await page.setViewportSize({ width: 360, height: 740 })
  await expect.poll(async () => (await box(queue)).width).toBeLessThan(360)
  const stacked = await settledBox(queue)
  const overview = await settledBox(page.locator('#frame-overview'))
  expect(overview.x).toBe(stacked.x)
  expect(overview.y).toBeGreaterThan(stacked.y)
  expect(overview.width).toBe(stacked.width)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)

  // Dragging is off, so a swipe on the title bar scrolls instead of moving the frame.
  await drag(page, page.locator('#frame-overview .frame-handle'), 0, -200)
  expect((await settledBox(page.locator('#frame-overview'))).x).toBe(overview.x)
  await expect(page.locator('.react-resizable-handle:visible')).toHaveCount(0)

  // Passing through other sizes leaves the wide layout as it was.
  await page.setViewportSize({ width: 1920, height: 1000 })
  await expect.poll(async () => (await box(queue)).width).toBe(wide.width)
})

test('a phone stacks an open case without overlaps or sideways scrolling', async ({ page, request }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await signIn(page, await createUser(request, ['casemgr-detect-respond-analyst']))
  await queueFrame(page).getByText('New case').click()
  await queueFrame(page).getByLabel('Title').fill(`Phone ${Date.now()}`)
  await queueFrame(page).getByRole('button', { name: 'Create case' }).click()
  await popOut(page, 'Notes')
  await popOut(page, 'Details')
  await expect(page.getByRole('region', { name: /^Details #/ })).toBeVisible()

  await page.setViewportSize({ width: 360, height: 740 })
  await expect.poll(() => page.locator('.workspace.stacked').count()).toBe(1)
  await expect
    .poll(() =>
      page.locator('.frame').evaluateAll((all) => {
        const boxes = all.map((frame) => frame.getBoundingClientRect())
        return boxes.slice(1).filter((box, i) => box.top < boxes[i].bottom).length
      }),
    )
    .toBe(0)
  expect(await page.locator('.frame').count()).toBeGreaterThan(4)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

test('maximizing fills the window and restoring returns the frame', async ({ page, request }) => {
  await page.setViewportSize({ width: 1400, height: 900 })
  await signIn(page, await createUser(request, ['casemgr-detect-respond-analyst']))
  const queue = queueFrame(page)
  const before = await settledBox(queue)

  await page.getByRole('button', { name: 'Maximize Case queue' }).click()
  const full = await settledBox(queue)
  expect(full.width).toBeGreaterThan(1300)
  expect(full.height).toBeGreaterThan(800)

  await page.getByRole('button', { name: 'Restore Case queue' }).click()
  expect(await settledBox(queue)).toEqual(before)
})

test('selecting a dashboard figure filters the queue, and selecting it again clears it', async ({ page, request }) => {
  await signIn(page, await createUser(request, ['casemgr-detect-respond-analyst']))
  const queue = queueFrame(page)
  const tile = page.getByRole('button', { name: /^Open cases \d+$/ })

  await tile.click()
  await expect(tile).toHaveAttribute('aria-pressed', 'true')
  await expect(queue.getByText(/^\d+ cases?$/)).toBeVisible()
  await expect(queue.getByLabel('State')).toHaveValue('*open')
  await expect(queue.getByRole('cell', { name: 'Closed', exact: true })).toHaveCount(0)

  await tile.click()
  await expect(tile).toHaveAttribute('aria-pressed', 'false')
  await expect(queue.getByLabel('State')).toHaveValue('')
})

test('a popped-out section comes into view under the case, and goes back as a tab', async ({ page, request }) => {
  await page.setViewportSize({ width: 1280, height: 700 })
  await signIn(page, await createUser(request, ['casemgr-detect-respond-analyst']))
  await queueFrame(page).getByText('New case').click()
  await queueFrame(page).getByLabel('Title').fill(`Reveal ${Date.now()}`)
  await queueFrame(page).getByRole('button', { name: 'Create case' }).click()

  await popOut(page, 'Notes')
  const notes = page.getByRole('region', { name: /^Notes #/ })
  await expect(notes).toBeInViewport()
  // It docks right under the case.
  const caseBox = (await page.getByRole('region', { name: /^Case #/ }).boundingBox())!
  await expect.poll(async () => (await notes.boundingBox())!.x).toBe(caseBox.x)
  const notesBox = (await notes.boundingBox())!
  expect(notesBox.y).toBeGreaterThan(caseBox.y + caseBox.height)
  expect(notesBox.y).toBeLessThan(caseBox.y + caseBox.height + 40)

  await notes.getByRole('button', { name: /^Put Notes #\d+ back in the case$/ }).click()
  await expect(notes).toBeHidden()
  await expect(page.getByRole('tab', { name: /^Notes/ })).toHaveAttribute('aria-selected', 'true')
})

test('the page settles after the window crosses a breakpoint, and navigation still works', async ({ page, request }) => {
  await page.setViewportSize({ width: 1920, height: 1000 })
  await signIn(page, await createUser(request, ['casemgr-detect-respond-admin']))
  await expect(queueFrame(page)).toBeVisible()
  for (const width of [768, 360, 1920]) {
    await page.setViewportSize({ width, height: 900 })
    await page.waitForTimeout(600)
    const changes = await page.evaluate(
      () =>
        new Promise<number>((resolve) => {
          let count = 0
          const observer = new MutationObserver((list) => (count += list.length))
          observer.observe(document.body, { attributes: true, subtree: true, childList: true })
          setTimeout(() => {
            observer.disconnect()
            resolve(count)
          }, 500)
        }),
    )
    expect(changes).toBeLessThan(20)
  }
  await page.getByRole('link', { name: 'Settings' }).click()
  await expect(page.getByRole('heading', { name: 'API tokens' })).toBeVisible()
})
