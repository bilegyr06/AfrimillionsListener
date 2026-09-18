import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  oxc: {
    // Override the Next-specific `"jsx": "preserve"` in tsconfig.json so
    // .tsx files are transformed to JS when tested.
    jsx: { runtime: "automatic" },
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url)),
    },
  },
  test: {
    environment: "node",
    // Pin the timezone so date-times in golden snapshots are reproducible
    // across machines and CI. The locale is pinned in lib/format.ts itself
    // (host env vars like LANG/LC_ALL are not honored on Windows).
    env: {
      TZ: "UTC",
    },
  },
});