import { defineConfig } from 'vite'

export default defineConfig({
  // esbuild handles JSX; this app does not need the React build plugin.
  build: {
    target: ['es2020', 'edge88', 'firefox78', 'chrome87', 'safari14']
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000'
    }
  }
})
