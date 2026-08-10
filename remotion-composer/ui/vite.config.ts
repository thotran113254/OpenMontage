import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { resolve } from "node:path";

// The UI imports MonaTimeline straight out of ../src so the preview runs the
// exact component the renderer runs — no second implementation to drift.
// Media does NOT come from a public dir here: job assets are served by the
// Python API under /api/media/<job_id>/, and MonaTimeline resolves them via
// its assetBase prop.
export default defineConfig({
  root: resolve(__dirname),
  plugins: [react()],
  server: {
    port: 5173,
    fs: { allow: [resolve(__dirname, "..")] },
    proxy: {
      "/api": {
        target: process.env.AUTOEDIT_API ?? "http://127.0.0.1:8756",
        changeOrigin: true,
      },
    },
  },
  build: { outDir: resolve(__dirname, "dist"), emptyOutDir: true },
});
