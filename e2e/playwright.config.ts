import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  forbidOnly: !!process.env.CI,
  workers: 2,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    // The kind deployment (deploy/kind) serves the app on https://localhost:8443 with a self-signed certificate.
    baseURL: process.env.BASE_URL ?? 'http://localhost:8080',
    ignoreHTTPSErrors: true,
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
