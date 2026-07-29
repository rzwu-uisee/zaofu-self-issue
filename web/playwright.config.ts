import { defineConfig, devices } from "@playwright/test";

const chromiumExecutablePath = process.env.ZF_E2E_CHROMIUM_EXECUTABLE_PATH;

export default defineConfig({
  testDir: "./tests",
  timeout: 20_000,
  expect: {
    timeout: 5_000,
  },
  use: {
    baseURL: process.env.ZF_WEB_BASE_URL ?? "http://127.0.0.1:8001",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        launchOptions: chromiumExecutablePath
          ? { executablePath: chromiumExecutablePath }
          : undefined,
      },
    },
  ],
});
