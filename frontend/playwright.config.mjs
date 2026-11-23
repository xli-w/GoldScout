import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";

export default defineConfig({
  testDir: "./tests",
  testMatch: "**/*.spec.mjs",
  webServer: {
    command: "python -m frontend.scripts.demo_server",
    cwd: fileURLToPath(new URL("..", import.meta.url)),
    wait: { stdout: /GOLDSCOUT_SYNTHETIC_PORT=(?<goldscout_synthetic_port>\d+)/ },
    reuseExistingServer: false,
  },
  use: { headless: true, channel: process.env.DASHBOARD_BROWSER_CHANNEL },
  projects: [
    { name: "desktop", use: { viewport: { width: 1440, height: 1000 } } },
    { name: "mobile", use: { viewport: { width: 390, height: 844 }, isMobile: true } },
  ],
});