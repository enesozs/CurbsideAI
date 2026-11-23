import { defineConfig } from "@playwright/test";

const preview = !!process.env.CS_PREVIEW; // CS_PREVIEW=1: test the production build via `vite preview`
const port = Number(process.env.CS_PORT ?? (preview ? 4173 : 5173));

export default defineConfig({
  testDir: "./tests",
  timeout: 60_000,
  use: {
    baseURL: `http://localhost:${port}`,
    channel: "chrome", // use the locally installed Chrome; no browser download needed
    headless: true,
    viewport: { width: 1440, height: 900 },
  },
  webServer: {
    command: preview
      ? `npx vite build && npx vite preview --port ${port} --strictPort`
      : `npx vite --port ${port} --strictPort`,
    url: `http://localhost:${port}`,
    reuseExistingServer: true,
    timeout: 180_000,
  },
  projects: [
    { name: "desktop", use: { viewport: { width: 1440, height: 900 } } },
    { name: "mobile", use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } },
  ],
});
