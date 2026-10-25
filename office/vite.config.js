import {defineConfig} from 'vite';
import {formalGatewayPlugin} from './server/formal-dispatch-gateway.mjs';
import {harnessGatewayPlugin} from './server/harness-gateway.mjs';

// These server-only variables deliberately have NO VITE_ prefix.
export default defineConfig({
  // The office checkout may be reached through a junction; Vite dev resolves the
  // real path, which then sits outside the served root and every module fails
  // to load. Keeping symlinks as written serves the files under the configured
  // root. `vite build` is unaffected.
  resolve: {preserveSymlinks: true},
  plugins: [formalGatewayPlugin({
    upstream: process.env.OFFICE_FORMAL_UPSTREAM,
    tokenFile: process.env.OFFICE_FORMAL_TOKEN_FILE,
    adminTokenFile: process.env.OFFICE_FORMAL_ADMIN_TOKEN_FILE,
    project: process.env.OFFICE_FORMAL_PROJECT,
    group: process.env.OFFICE_FORMAL_GROUP,
  }), harnessGatewayPlugin()],
  server: { host: '127.0.0.1' },
  preview: { host: '127.0.0.1' },
  build: {
    target: 'esnext',
    // 独立页面有三个：办公室本体、与管理员的私聊、各窗口说了什么。
    rollupOptions: { input: { main: 'index.html', direct: 'direct.html', outputs: 'outputs.html' } },
  },
});
