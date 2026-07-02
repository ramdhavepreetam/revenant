import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../web',
    emptyOutDir: true,
  },
  server: {
    port: 8765,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8766', changeOrigin: true },
      '/audio': { target: 'http://127.0.0.1:8766', changeOrigin: true },
    },
  },
})
