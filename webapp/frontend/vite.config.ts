import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run build` writes into ../static, which the FastAPI server serves.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../static", emptyOutDir: true, chunkSizeWarningLimit: 900 },
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
