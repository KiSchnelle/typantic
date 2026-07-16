import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// Build straight into the Python package so the server serves it from web_dist.
// In dev (`npm run dev`), proxy the API + WebSocket to a locally running server
// started with e.g. `typantic web serve --port 8791 --no-token`.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: "../src/typantic/web/web_dist",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8791",
      "/ws": { target: "ws://127.0.0.1:8791", ws: true },
    },
  },
});
