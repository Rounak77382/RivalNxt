import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react-swc";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/__tests__/**/*.{test,spec}.{ts,tsx}"],
    setupFiles: ["./vitest.setup.ts"],
    // Exit 0 when no test files match, so the CI job is green before the
    // first test lands and stays meaningful afterwards.
    passWithNoTests: true,
    restoreMocks: true,
    // Timers and RAF are stubbed per-test; keep suites isolated so a leaked
    // interval in one file cannot affect another.
    isolate: true,
  },
});
