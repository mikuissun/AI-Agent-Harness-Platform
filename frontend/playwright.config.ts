import { defineConfig } from '@playwright/test'
export default defineConfig({
  testDir: './tests', fullyParallel: false, workers: 1, retries: 0,
  use: { baseURL: 'http://127.0.0.1:5179', channel: 'chrome', headless: true, viewport: { width: 1440, height: 1000 }, screenshot: 'off', trace: 'off' },
  webServer: { command: 'npm run dev -- --port 5179 --strictPort', url: 'http://127.0.0.1:5179', reuseExistingServer: false },
})
