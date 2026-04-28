import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Dev server proxies /api to the FastAPI process on localhost:5050 so
// hot-reload + cookie auth both work without CORS gymnastics.
const API_TARGET = process.env.VITE_API_TARGET || "http://127.0.0.1:5050";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "client", "src"),
    },
  },
  root: path.resolve(import.meta.dirname, "client"),
  // Same-origin in production: served from /web/dist/public mounted by FastAPI
  base: "./",
  build: {
    outDir: path.resolve(import.meta.dirname, "dist/public"),
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": {
        target: API_TARGET,
        changeOrigin: false,
      },
    },
    fs: {
      strict: true,
      deny: ["**/.*"],
    },
  },
});
