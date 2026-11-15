import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  use: { baseURL: process.env.TRADER_DASHBOARD_TEST_URL || "http://127.0.0.1:8765", headless: true, channel: process.env.DASHBOARD_BROWSER_CHANNEL },
  projects: [
    { name: "desktop", use: { viewport: { width: 1440, height: 1000 } } },
    { name: "mobile", use: { viewport: { width: 390, height: 844 }, isMobile: true } },
  ],
});