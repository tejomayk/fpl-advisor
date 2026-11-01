// Builds the single-page app into the Python package's static folder, so FastAPI and the Vercel
// deploy serve it with no Node at runtime. Dev: `npm run dev` proxies /api and /static to :8000.
import { rmSync } from "node:fs";
import { fileURLToPath } from "node:url";
import tailwind from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const outDir = fileURLToPath(new URL("../src/fpl_advisor/static", import.meta.url));

// The static folder also holds hand-kept files (fonts, icon, sample JSON), so it can't be emptied.
// Only the previous build (index.html + assets/) is removed, so stale hashed files never pile up.
const cleanPreviousBuild = {
  name: "clean-previous-build",
  apply: "build",
  buildStart() {
    rmSync(`${outDir}/assets`, { recursive: true, force: true });
    rmSync(`${outDir}/index.html`, { force: true });
  },
};

// The animation engine is a lazy chunk (LazyMotion). Preload it from the page so content that
// animates in never waits on a second round trip. Deterministic: the name comes from the bundle.
const preloadMotion = {
  name: "preload-motion-chunk",
  apply: "build",
  transformIndexHtml: {
    order: "post",
    handler(_html, ctx) {
      const chunk = Object.values(ctx.bundle || {}).find((c) => c.type === "chunk" && c.name === "motionFeatures");
      return chunk
        ? [{ tag: "link", attrs: { rel: "modulepreload", crossorigin: true, href: `/static/${chunk.fileName}` }, injectTo: "head" }]
        : [];
    },
  },
};

export default defineConfig(({ command }) => ({
  base: command === "build" ? "/static/" : "/",
  plugins: [react(), tailwind(), cleanPreviousBuild, preloadMotion],
  publicDir: false,
  build: {
    outDir,
    emptyOutDir: false,
    assetsDir: "assets",
    sourcemap: false,
    modulePreload: { polyfill: false },
    reportCompressedSize: false,
  },
  server: {
    port: 5180,
    strictPort: true,
    proxy: {
      "/api": "http://localhost:8000",
      "/static": "http://localhost:8000",
    },
  },
}));
