/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In dev, the API runs on :8000 (uvicorn) and Vite proxies to it.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: false },
      "/kite": "http://localhost:8000",
      "/healthz": "http://localhost:8000",
    },
  },
  build: { chunkSizeWarningLimit: 900 },
  test: { environment: "node" },
});
