import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";
import { resolve } from "node:path";

// The UI imports MonaTimeline straight out of ../src so the preview runs the
// exact component the renderer runs — no second implementation to drift.
// Media does NOT come from a public dir here: job assets are served by the
// Python API under /api/media/<job_id>/, and MonaTimeline resolves them via
// its assetBase prop.
//
// Auth: the UI never holds the API token. The browser authenticates via an
// HttpOnly session cookie (`/api/auth/*`, see app.tsx) that the `/api` proxy
// forwards untouched — this file must not inject an Authorization header,
// because this dev server's own port is bound to 0.0.0.0 by default and is
// reachable from the public internet on this VPS; anyone hitting the proxy
// would otherwise be authenticated as the owner regardless of the cookie.
export default defineConfig(({ mode }) => {
  // The repo's .env lives at the project root (two levels up from this file),
  // not in ui/ — Vite only auto-loads .env from its own root by default, so
  // `make autoedit-ui` (a bare `vite` process, no Python wrapper) would
  // otherwise never see AUTOEDIT_*. Passing "" as the prefix loads every var,
  // not just VITE_-prefixed ones.
  const repoRoot = resolve(__dirname, "../..");
  const env = loadEnv(mode, repoRoot, "");

  const apiPort = env.AUTOEDIT_PORT || "8861";
  const uiPort = Number(env.AUTOEDIT_UI_PORT || "5617");
  const bindHost = env.AUTOEDIT_BIND || "0.0.0.0";
  const publicHost = env.AUTOEDIT_PUBLIC_HOST || "";
  const apiTarget = env.AUTOEDIT_API || `http://127.0.0.1:${apiPort}`;

  return {
    root: resolve(__dirname),
    plugins: [react()],
    server: {
      host: bindHost,
      port: uiPort,
      strictPort: true,
      allowedHosts: publicHost ? [publicHost, "localhost", "127.0.0.1"] : true,
      hmr: publicHost
        ? { host: publicHost, clientPort: uiPort, protocol: "ws" }
        : undefined,
      fs: { allow: [resolve(__dirname, "..")] },
      proxy: {
        "/api": {
          target: apiTarget,
          changeOrigin: true,
        },
      },
    },
    build: { outDir: resolve(__dirname, "dist"), emptyOutDir: true },
  };
});
