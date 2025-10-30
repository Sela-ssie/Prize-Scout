import { defineConfig } from 'vite'

export default defineConfig({
  // Not using @vitejs/plugin-react to avoid ESM-only plugin loading issues
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000'
    }
  }
})
