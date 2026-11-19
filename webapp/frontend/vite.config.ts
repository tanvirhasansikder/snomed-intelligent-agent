import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run build` writes into ../static, which the FastAPI server serves.
export default defineConfig({
  plugins: [react()],
  // Stable names for the app's own files, so a rebuild overwrites them instead of adding new ones.
  build: {
    outDir: "../static", emptyOutDir: true, chunkSizeWarningLimit: 900,
    rollupOptions: { output: { entryFileNames: "assets/app.js", chunkFileNames: "assets/[name].js", assetFileNames: (a) => (a.names?.[0] ?? a.name ?? "").endsWith(".css") ? "assets/app.css" : "assets/[name]-[hash][extname]" } },
  },
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
