import { expect, type APIRequestContext, type Page } from '@playwright/test'
import { randomUUID } from 'node:crypto'
import { Secret, TOTP } from 'otpauth'

const KEYCLOAK = 'http://localhost:8180'
const REALM_ADMIN = `${KEYCLOAK}/admin/realms/casemgr`

export interface TestUser {
  username: string
  password: string
  displayName: string
}

async function adminHeaders(request: APIRequestContext) {
  const response = await request.post(`${KEYCLOAK}/realms/master/protocol/openid-connect/token`, {
    form: { grant_type: 'password', client_id: 'admin-cli', username: 'admin', password: 'admin' },
  })
  const { access_token } = await response.json()
  return { Authorization: `Bearer ${access_token}` }
}

async function userId(request: APIRequestContext, username: string): Promise<string> {
  const response = await request.get(`${REALM_ADMIN}/users`, {
    headers: await adminHeaders(request),
    params: { username, exact: 'true' },
  })
  const [user] = await response.json()
  return user.id
}

/** Create a Keycloak user in the given casemgr groups. MFA is not yet enrolled. */
export async function createUser(request: APIRequestContext, groups: string[]): Promise<TestUser> {
  const username = `e2e-${randomUUID().slice(0, 8)}`
  const user = { username, password: `pw-${randomUUID()}`, displayName: `E2E ${username}` }
  const response = await request.post(`${REALM_ADMIN}/users`, {
    headers: await adminHeaders(request),
    data: {
      username,
      firstName: 'E2E',
      lastName: username,
      email: `${username}@example.test`,
      emailVerified: true,
      enabled: true,
      credentials: [{ type: 'password', value: user.password, temporary: false }],
      groups: groups.map((g) => `/${g}`),
    },
  })
  expect(response.status()).toBe(201)
  return user
}

export async function removeFromGroup(request: APIRequestContext, username: string, group: string) {
  const headers = await adminHeaders(request)
  const id = await userId(request, username)
  const groups: { id: string; name: string }[] = await (
    await request.get(`${REALM_ADMIN}/users/${id}/groups`, { headers })
  ).json()
  const target = groups.find((g) => g.name === group)!
  const response = await request.delete(`${REALM_ADMIN}/users/${id}/groups/${target.id}`, {
    headers,
  })
  expect(response.ok()).toBe(true)
}

export const queueFrame = (page: Page) => page.getByRole('region', { name: 'Case queue' })
export const caseFrame = (page: Page) => page.getByRole('region', { name: /^Case #\d+$/ })

/** Show one section of the open case, on its tab. */
export async function openCaseTab(page: Page, name: string) {
  await caseFrame(page).getByRole('tab', { name: new RegExp(`^${name}`) }).click()
}

/** Show one section of the open case in a frame of its own, under the case. */
export async function popOut(page: Page, name: string) {
  await openCaseTab(page, name)
  await caseFrame(page).getByRole('button', { name: `Pop out ${name}` }).click()
}

/** Run a case action that asks for one value in a dialog. */
export async function act(page: Page, action: string, field: string, value: string) {
  await caseFrame(page).getByRole('button', { name: action }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel(field).fill(value)
  await dialog.getByRole('button', { name: 'Confirm' }).click()
}

export async function changeState(page: Page, state: string, resolution?: string) {
  await caseFrame(page).getByRole('button', { name: 'Change state' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('State').selectOption({ label: state })
  if (resolution) await dialog.getByLabel('Resolution').selectOption(resolution)
  await dialog.getByRole('button', { name: 'Confirm' }).click()
}

export async function signOut(page: Page, user: TestUser) {
  await page.getByRole('button', { name: user.displayName }).click()
  await page.getByRole('button', { name: 'Sign out' }).click()
}

/** Sign in from the app for the first time, enrolling a TOTP authenticator as required. */
export async function signIn(page: Page, user: TestUser) {
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).click()
  await page.getByLabel('Username or email').fill(user.username)
  await page.getByLabel('Password', { exact: true }).fill(user.password)
  await page.getByRole('button', { name: 'Sign In' }).click()

  await expect(page.getByText('Mobile Authenticator Setup')).toBeVisible()
  await page.locator('#mode-manual').click()
  const key = await page.locator('#kc-totp-secret-key').textContent()
  const totp = new TOTP({
    secret: Secret.fromBase32(key!.replace(/\s/g, '')),
    algorithm: 'SHA256',
    digits: 6,
    period: 30,
  })
  // A code generated just before the 30 s step rolls over can expire before Keycloak checks it.
  const secondsLeft = 30 - (Math.floor(Date.now() / 1000) % 30)
  if (secondsLeft < 5) await page.waitForTimeout(secondsLeft * 1000 + 250)
  await page.locator('#totp').fill(totp.generate())
  await page.locator('#saveTOTPBtn').click()
  await expect(page.getByRole('button', { name: user.displayName })).toBeVisible()
}
