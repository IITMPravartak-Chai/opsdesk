import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backend = process.env.VITE_BACKEND || "http://localhost:8001"; // the OpsDesk API (FastAPI)

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": backend, "/healthz": backend },
  },
  build: { outDir: "dist", sourcemap: false },
});
