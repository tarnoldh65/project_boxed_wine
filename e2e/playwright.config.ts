import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  forbidOnly: !!process.env.CI,
  workers: 2,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: 'http://localhost:8080',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
