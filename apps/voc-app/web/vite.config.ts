import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // Never load the Operator's environment or expose provider keys in the bundle.
  envDir: false,
  server: { proxy: { "/api": "http://127.0.0.1:8091" } },
  test: { include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"] },
});
