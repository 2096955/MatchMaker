import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// Dev-server proxy targets. The frontend bundle uses RELATIVE paths
// (`/api/*`, `/mcp/*`) so the same artifact runs behind CloudFront
// (which proxies both to the ALB origin) and locally via Vite.
//
// Local default is the Flask backend on :5000. Override either target
// with VITE_API_PROXY / VITE_MCP_PROXY in `.env.local` to point at the
// dev ALB (scudo-dev-alb-2025833982.eu-west-2.elb.amazonaws.com) when
// you want the dev server to hit the deployed backend.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.VITE_API_PROXY || 'http://localhost:5000'
  const mcpTarget = env.VITE_MCP_PROXY || apiTarget

  return {
    plugins: [react()],
    server: {
      port: 3000,
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
        },
        '/mcp': {
          target: mcpTarget,
          changeOrigin: true,
          // MCP uses long-lived streamable-HTTP / SSE; disable buffering
          // and timeouts at the proxy boundary so the stream stays open.
          ws: true,
          configure: (proxy) => {
            proxy.on('proxyReq', (proxyReq) => {
              proxyReq.setHeader('Accept-Encoding', 'identity')
            })
          },
        },
      },
    },
  }
})
