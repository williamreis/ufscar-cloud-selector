import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      // Dev only: em produção o nginx do container faz esse proxy (ver nginx.conf).
      "/api": {
        target: process.env.VITE_DEV_BACKEND_URL || "http://localhost:8001",
        changeOrigin: true,
      },
    },
  },
})
