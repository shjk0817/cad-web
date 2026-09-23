import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5180,
    strictPort: true,
    // 将 /api 请求代理到并行开发的 FastAPI 后端
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        // SSE 不能被 vite 缓冲，否则进度事件不实时到达前端
        // （http-proxy 文档：ws=true 才会关闭缓冲，但对 text/event-stream
        // 也必须显式声明 SSE 友好的头，否则 Node 会按 HTTP 1.1 缓冲）
        ws: false,
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq) => {
            proxyReq.setHeader('Cache-Control', 'no-cache')
            proxyReq.setHeader('X-Accel-Buffering', 'no')
          })
          proxy.on('proxyRes', (proxyRes) => {
            // 让上游 SSE 响应不被代理缓冲
            proxyRes.headers['cache-control'] = 'no-cache'
            proxyRes.headers['x-accel-buffering'] = 'no'
          })
        },
      },
    },
  },
})
