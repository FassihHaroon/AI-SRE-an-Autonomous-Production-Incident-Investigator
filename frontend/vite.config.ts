/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";

// In development the Vite server proxies API calls to the backend, so the browser only
// ever talks to one origin (the same as production, where nginx does the proxying).
const apiTarget = process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8010";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: apiTarget, changeOrigin: true },
    },
  },
  build: {
    // Maps are generated for debugging but not referenced from the shipped bundle.
    sourcemap: "hidden",
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    restoreMocks: true,
  },
});
