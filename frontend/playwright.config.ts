import { defineConfig } from '@playwright/test'

const baseURL = 'http://127.0.0.1:4173'

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  use: {
    baseURL,
    channel: process.env.PLAYWRIGHT_CHANNEL,
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'npm run build && npm run preview -- --host 127.0.0.1 --strictPort',
    env: { VITE_API_BASE_URL: '/api' },
    url: baseURL,
  },
})
