import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

const backendPort = process.env.IMAGEFIND_PORT || "5175"

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    allowedHosts: [".trycloudflare.com"],
    headers: {
      "Content-Security-Policy": "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self' ws: wss:",
      "Referrer-Policy": "no-referrer",
      "X-Content-Type-Options": "nosniff",
      "X-Frame-Options": "DENY",
    },
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${backendPort}`,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/setupTests.ts",
    globals: true,
  },
})
